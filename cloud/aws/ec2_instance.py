#!/usr/bin/env python3
"""AWS EC2 instance skill CLI.

Uses boto3 so your existing AWS auth (profiles, SSO, IAM role, env vars)
works without extra configuration. Mirrors the shape of the digitalocean
skill: create / list / show / destroy / reap + discovery helpers.

Deps: boto3, PyYAML. Auth: whatever boto3 picks up (AWS_PROFILE, AWS_REGION,
shared credentials file, SSO, IMDS if running on EC2).
Docs: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html
"""
import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from cloud import (
    TAG_MARKER,
    load_preset,
    merge_cloud_init,
    parse_ttl,
    render_user_data,
    systemd_oneshot_timer,
    ttl_from_tag,
    ttl_tag,
)

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    sys.exit("boto3 required: pip install boto3")

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

PRESETS_DIR = Path(__file__).parent / "cloud-init"
DEFAULT_AMI_PATTERN = "al2023-ami-*-x86_64"


def ec2(region=None):
    return boto3.client("ec2", region_name=region)


# ----- tags helpers -----

def _tags_to_dict(tags):
    return {t["Key"]: t["Value"] for t in (tags or [])}


def _dict_to_tags(d):
    return [{"Key": k, "Value": v} for k, v in d.items()]


# ----- self-destruct (AWS-specific) -----

SELF_DESTRUCT_SCRIPT = """#!/bin/bash
set -eu
TOKEN=$(curl -fsSX PUT "http://169.254.169.254/latest/api/token" \\
    -H "X-aws-ec2-metadata-token-ttl-seconds: 120")
IID=$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" \\
    http://169.254.169.254/latest/meta-data/instance-id)
REGION=$(curl -fsS -H "X-aws-ec2-metadata-token: $TOKEN" \\
    http://169.254.169.254/latest/meta-data/placement/region)
# Requires an IAM instance profile with ec2:TerminateInstances on this instance.
aws ec2 terminate-instances --region "$REGION" --instance-ids "$IID"
"""


def add_self_destruct(cfg, when_utc):
    files, runs = systemd_oneshot_timer(
        name="aws-self-destruct",
        when_utc=when_utc,
        script_path="/usr/local/sbin/aws-self-destruct.sh",
        script_body=SELF_DESTRUCT_SCRIPT,
    )
    cfg = dict(cfg)
    cfg["write_files"] = cfg.get("write_files", []) + files
    cfg["runcmd"] = cfg.get("runcmd", []) + runs
    return cfg


# ----- AMI resolution -----

def resolve_ami(client, name_pattern):
    """Find the newest AMI matching the given Name pattern, owned by Amazon."""
    resp = client.describe_images(
        Owners=["amazon"],
        Filters=[
            {"Name": "name", "Values": [name_pattern]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": ["x86_64"]},
            {"Name": "virtualization-type", "Values": ["hvm"]},
        ],
    )
    imgs = sorted(resp.get("Images", []),
                  key=lambda i: i.get("CreationDate", ""),
                  reverse=True)
    if not imgs:
        sys.exit(f"No AMI matching pattern {name_pattern!r}.")
    return imgs[0]["ImageId"]


# ----- commands -----

def cmd_create(args):
    client = ec2(args.region)

    cfg = {}
    if args.preset:
        try:
            cfg = load_preset(PRESETS_DIR, args.preset)
        except (FileNotFoundError, ValueError) as e:
            sys.exit(str(e))
    if args.user_data:
        extra = yaml.safe_load(Path(args.user_data).read_text()) or {}
        cfg = merge_cloud_init(cfg, extra)

    ttl_dt = None
    tags = dict(t.split("=", 1) for t in (args.tag or []))
    tags.setdefault("Name", args.name)

    if args.ttl:
        try:
            ttl_dt = parse_ttl(args.ttl)
        except ValueError as e:
            sys.exit(str(e))
        tags[TAG_MARKER] = "true"
        tags["TTL"] = ttl_tag(ttl_dt)

    if args.self_destruct:
        if not ttl_dt:
            sys.exit("--self-destruct requires --ttl.")
        if not args.iam_instance_profile:
            sys.exit("--self-destruct requires --iam-instance-profile "
                     "with ec2:TerminateInstances permission on this instance.")
        cfg = add_self_destruct(cfg, ttl_dt)

    ami = args.ami or resolve_ami(client, args.ami_name_pattern)
    user_data = render_user_data(cfg)

    params = {
        "ImageId": ami,
        "InstanceType": args.instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "KeyName": args.key_name,
        "TagSpecifications": [
            {"ResourceType": "instance", "Tags": _dict_to_tags(tags)},
        ],
    }
    if args.subnet_id:
        params["SubnetId"] = args.subnet_id
    if args.security_group_ids:
        params["SecurityGroupIds"] = args.security_group_ids
    if user_data:
        params["UserData"] = user_data
    if args.iam_instance_profile:
        params["IamInstanceProfile"] = {"Name": args.iam_instance_profile}

    if args.dry_run:
        print(json.dumps(params, indent=2, default=str))
        return

    try:
        resp = client.run_instances(**params)
    except (BotoCoreError, ClientError) as e:
        sys.exit(f"run-instances failed: {e}")

    inst = resp["Instances"][0]
    iid = inst["InstanceId"]
    print(f"created instance {iid} ({args.name}) ami={ami}")
    if ttl_dt:
        mode = "self-destruct on instance" if args.self_destruct else "manual reap"
        print(f"ttl: {ttl_dt.isoformat()} ({mode})")
    if args.wait:
        ip = wait_for_ip(client, iid)
        print(f"public ip: {ip or '(none - private subnet?)'}")


def wait_for_ip(client, iid, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = client.describe_instances(InstanceIds=[iid])
        inst = resp["Reservations"][0]["Instances"][0]
        state = inst["State"]["Name"]
        if state == "running":
            return inst.get("PublicIpAddress") or ""
        if state in ("terminated", "shutting-down"):
            sys.exit(f"Instance {iid} entered state {state} before running.")
        time.sleep(3)
    sys.exit(f"Timed out waiting for {iid} to reach running state.")


def cmd_list(args):
    client = ec2(args.region)
    filters = []
    if args.tag:
        k, _, v = args.tag.partition("=")
        filters.append({"Name": f"tag:{k}", "Values": [v or "*"]})
    filters.append({
        "Name": "instance-state-name",
        "Values": ["pending", "running", "stopping", "stopped"]
        if not args.all else
        ["pending", "running", "stopping", "stopped", "shutting-down", "terminated"],
    })

    resp = client.describe_instances(Filters=filters)
    for res in resp["Reservations"]:
        for inst in res["Instances"]:
            tags = _tags_to_dict(inst.get("Tags"))
            name = tags.get("Name", "-")
            ttl = tags.get("TTL", "")
            print(f"{inst['InstanceId']}\t{inst['State']['Name']}\t{name}\t"
                  f"{inst.get('PublicIpAddress', '') or '-'}\t{ttl}")


def resolve(client, id_or_name):
    if id_or_name.startswith("i-"):
        return id_or_name
    resp = client.describe_instances(
        Filters=[{"Name": "tag:Name", "Values": [id_or_name]},
                 {"Name": "instance-state-name",
                  "Values": ["pending", "running", "stopping", "stopped"]}],
    )
    matches = [i for r in resp["Reservations"] for i in r["Instances"]]
    if not matches:
        sys.exit(f"No instance named {id_or_name!r}.")
    if len(matches) > 1:
        sys.exit(f"Multiple instances named {id_or_name!r}: "
                 f"{[i['InstanceId'] for i in matches]}")
    return matches[0]["InstanceId"]


def cmd_show(args):
    client = ec2(args.region)
    resp = client.describe_instances(
        InstanceIds=[resolve(client, args.target)])
    inst = resp["Reservations"][0]["Instances"][0]
    print(json.dumps(inst, indent=2, default=str))


def cmd_destroy(args):
    client = ec2(args.region)
    iid = resolve(client, args.target)
    if not args.yes:
        if input(f"Terminate instance {iid}? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")
    client.terminate_instances(InstanceIds=[iid])
    print(f"terminated {iid}")


def cmd_reap(args):
    client = ec2(args.region)
    resp = client.describe_instances(
        Filters=[{"Name": f"tag:{TAG_MARKER}", "Values": ["true"]},
                 {"Name": "instance-state-name",
                  "Values": ["pending", "running", "stopping", "stopped"]}],
    )
    now = datetime.now(timezone.utc)
    expired_ids = []
    for res in resp["Reservations"]:
        for inst in res["Instances"]:
            tags = _tags_to_dict(inst.get("Tags"))
            dt = ttl_from_tag(tags.get("TTL", ""))
            if dt and dt <= now:
                name = tags.get("Name", "-")
                print(f"{'would terminate' if args.dry_run else 'terminating'} "
                      f"{inst['InstanceId']} {name} (expired {dt.isoformat()})")
                expired_ids.append(inst["InstanceId"])
    if not expired_ids:
        print("nothing expired")
        return
    if not args.dry_run:
        client.terminate_instances(InstanceIds=expired_ids)


def cmd_list_amis(args):
    client = ec2(args.region)
    pattern = args.pattern or DEFAULT_AMI_PATTERN
    resp = client.describe_images(
        Owners=["amazon"],
        Filters=[{"Name": "name", "Values": [pattern]},
                 {"Name": "state", "Values": ["available"]}],
    )
    for img in sorted(resp["Images"],
                      key=lambda i: i.get("CreationDate", ""),
                      reverse=True)[:20]:
        print(f"{img['ImageId']}\t{img.get('CreationDate', '')[:10]}\t{img['Name']}")


def cmd_list_regions(args):
    client = ec2("us-east-1")
    for r in client.describe_regions()["Regions"]:
        print(f"{r['RegionName']}\t{r['Endpoint']}")


def cmd_list_instance_types(args):
    client = ec2(args.region)
    paginator = client.get_paginator("describe_instance_type_offerings")
    for page in paginator.paginate(LocationType="region"):
        for o in page["InstanceTypeOfferings"]:
            print(o["InstanceType"])


def cmd_list_keys(args):
    client = ec2(args.region)
    for k in client.describe_key_pairs()["KeyPairs"]:
        print(f"{k['KeyName']}\t{k.get('KeyFingerprint', '')}")


# ----- argparse -----

def build_parser():
    p = argparse.ArgumentParser(prog="ec2_instance.py",
                                description="AWS EC2 instance orchestrator")
    p.add_argument("--region",
                   help="override region (else AWS_REGION / profile default)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Create an instance")
    c.add_argument("--name", required=True)
    c.add_argument("--instance-type", required=True,
                   help="e.g. t3.small - see list-instance-types")
    c.add_argument("--ami", help="explicit AMI id; otherwise resolved by name pattern")
    c.add_argument("--ami-name-pattern", default=DEFAULT_AMI_PATTERN,
                   help=f"AMI name glob (default: {DEFAULT_AMI_PATTERN})")
    c.add_argument("--key-name", required=True, help="EC2 key pair name")
    c.add_argument("--subnet-id", help="subnet to place the instance in")
    c.add_argument("--security-group-ids", nargs="+",
                   help="one or more sg-... ids")
    c.add_argument("--iam-instance-profile",
                   help="instance profile NAME (required for --self-destruct)")
    c.add_argument("--preset", help="cloud-init preset from cloud-init/")
    c.add_argument("--user-data",
                   help="extra cloud-config yaml merged on top of preset")
    c.add_argument("--tag", action="append",
                   help="additional tag key=value; repeatable")
    c.add_argument("--ttl",
                   help="4h | 30m | 2d | ISO8601 - tags instance for reap")
    c.add_argument("--self-destruct", action="store_true",
                   help="install on-instance self-terminate at TTL "
                        "(requires --iam-instance-profile)")
    c.add_argument("--no-wait", dest="wait", action="store_false",
                   help="don't poll for public IP")
    c.add_argument("--dry-run", action="store_true",
                   help="print the RunInstances params and exit")
    c.set_defaults(func=cmd_create, wait=True)

    l = sub.add_parser("list", help="List instances")
    l.add_argument("--tag", help="filter by tag, e.g. auto-ttl=true or Name=foo")
    l.add_argument("--all", action="store_true",
                   help="include terminated / shutting-down")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="Show an instance")
    s.add_argument("target", help="instance id (i-...) or unique Name tag")
    s.set_defaults(func=cmd_show)

    d = sub.add_parser("destroy", help="Terminate an instance")
    d.add_argument("target")
    d.add_argument("--yes", action="store_true")
    d.set_defaults(func=cmd_destroy)

    r = sub.add_parser("reap", help="Terminate expired TTL-tagged instances")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_reap)

    la = sub.add_parser("list-amis", help="List AMIs matching a name pattern")
    la.add_argument("--pattern")
    la.set_defaults(func=cmd_list_amis)

    sub.add_parser("list-regions").set_defaults(func=cmd_list_regions)
    sub.add_parser("list-instance-types").set_defaults(func=cmd_list_instance_types)
    sub.add_parser("list-keys").set_defaults(func=cmd_list_keys)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

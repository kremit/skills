#!/usr/bin/env python3
"""Atlantic.net Cloud server skill CLI.

The Atlantic.net API has no native cloud-init or tag support, so this skill
bootstraps post-creation by SSH-ing to the new server and running a bash
script (either a shipped preset in bootstrap/ or a BYO path). TTL tracking
uses the shared lib/cloud manifest since server-side tags are not available.

Deps: PyYAML (for lib/cloud compatibility), stdlib only otherwise.
Auth: ATLANTIC_ACCESS_KEY_ID, ATLANTIC_SECRET_KEY.
Docs: https://www.atlantic.net/docs/api/
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from cloud import HTTPError, http_json, manifest, parse_ttl

API = "https://cloudapi.atlantic.net/"
API_VERSION = "2010-12-30"
PROVIDER = "atlanticnet"
BOOTSTRAP_DIR = Path(__file__).parent / "bootstrap"
EXTENDS_RE = re.compile(r"^#\s*@extends\s+(\S+)\s*$", re.M)


# ----- auth + request -----

def _credentials():
    access = os.environ.get("ATLANTIC_ACCESS_KEY_ID")
    secret = os.environ.get("ATLANTIC_SECRET_KEY")
    if not access or not secret:
        sys.exit("ATLANTIC_ACCESS_KEY_ID and ATLANTIC_SECRET_KEY must be set. "
                 "Get them from https://cloud.atlantic.net/ under API Access.")
    return access, secret


def _sign(secret, timestamp, rndguid):
    msg = f"{timestamp}{rndguid}".encode()
    raw = hmac.new(secret.encode(), msg, hashlib.sha256).digest()
    return base64.b64encode(raw).decode()


def call(action, **params):
    access, secret = _credentials()
    ts = str(int(time.time()))
    rnd = str(uuid.uuid4())
    query = {
        "Action": action,
        "Version": API_VERSION,
        "Format": "json",
        "ACSAccessKeyId": access,
        "Timestamp": ts,
        "Rndguid": rnd,
        "Signature": _sign(secret, ts, rnd),
        **{k: v for k, v in params.items() if v is not None},
    }
    try:
        return http_json("GET", API, query=query)
    except HTTPError as e:
        sys.exit(f"atlantic api {action} failed: {e.code} {e.reason}\n{e.body}")


def _unwrap(resp, action):
    """Atlantic wraps responses as {action+'response': {...}}; pull it out."""
    key = action + "response"
    if key in resp:
        return resp[key]
    return resp


# ----- bootstrap script loading (with @extends) -----

def load_bootstrap(name, seen=None):
    seen = seen or set()
    if name in seen:
        sys.exit(f"Bootstrap inheritance cycle through {name!r}.")
    seen.add(name)
    p = BOOTSTRAP_DIR / f"{name}.sh"
    if not p.exists():
        avail = ", ".join(sorted(x.stem for x in BOOTSTRAP_DIR.glob("*.sh")))
        sys.exit(f"Bootstrap {name!r} not found. Available: {avail or '(none)'}")
    text = p.read_text()
    m = EXTENDS_RE.search(text)
    if m:
        parent = load_bootstrap(m.group(1), seen)
        body = EXTENDS_RE.sub("", text)
        return parent + "\n# ---- child: " + name + " ----\n" + body
    return text


# ----- SSH bootstrap -----

def wait_for_ssh(ip, port=22, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no",
                 "-o", "UserKnownHostsFile=/dev/null",
                 "-o", "ConnectTimeout=5",
                 "-o", "BatchMode=yes",
                 "-p", str(port), f"root@{ip}", "true"],
                capture_output=True, timeout=10)
            if r.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            pass
        time.sleep(3)
    return False


def run_bootstrap(ip, identity_file, script_body):
    ssh = ["ssh", "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null",
           "-o", "BatchMode=yes"]
    if identity_file:
        ssh += ["-i", identity_file]
    ssh += [f"root@{ip}", "bash -s"]
    r = subprocess.run(ssh, input=script_body.encode(),
                       capture_output=False)
    if r.returncode != 0:
        sys.exit(f"bootstrap failed on {ip} with exit {r.returncode}")


# ----- commands -----

def cmd_create(args):
    ttl_dt = None
    if args.ttl:
        try:
            ttl_dt = parse_ttl(args.ttl)
        except ValueError as e:
            sys.exit(str(e))
    if args.self_destruct:
        sys.exit("--self-destruct is not supported on Atlantic.net (no cloud-init). "
                 "Use --ttl + `reap` instead.")

    if args.dry_run:
        print(json.dumps({
            "Action": "run-instance",
            "servername": args.name,
            "imageid": args.image,
            "planname": args.plan,
            "vm_location": args.location,
            "key_id": args.ssh_key_id,
            "serverqty": 1,
            "term": "hourly",
            "ttl": ttl_dt.isoformat() if ttl_dt else None,
            "bootstrap": args.preset or args.bootstrap,
        }, indent=2))
        return

    resp = call(
        "run-instance",
        servername=args.name,
        imageid=args.image,
        planname=args.plan,
        vm_location=args.location,
        key_id=args.ssh_key_id,
        serverqty="1",
        term="hourly",
        enablebackup="N",
    )
    data = _unwrap(resp, "run-instance")
    items = data.get("instancesSet", {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    if not items:
        sys.exit(f"unexpected response shape:\n{json.dumps(resp, indent=2)}")
    inst = items[0]
    iid = str(inst["instanceid"])
    ip = inst.get("ip_address", "")
    print(f"created instance {iid} ({args.name}) at {ip}")

    if ttl_dt:
        manifest.add(PROVIDER, iid, args.name, ttl_dt,
                     metadata={"location": args.location, "plan": args.plan})
        print(f"ttl: {ttl_dt.isoformat()} (manual reap via `reap`)")

    script = None
    if args.preset:
        script = "#!/bin/bash\nset -euxo pipefail\n" + load_bootstrap(args.preset)
    elif args.bootstrap:
        script = Path(args.bootstrap).read_text()
    if script:
        if not args.ssh_key_id:
            sys.exit("bootstrap requires --ssh-key-id so we can SSH in as root.")
        print(f"waiting for ssh on {ip}...")
        if not wait_for_ssh(ip):
            sys.exit(f"ssh never came up on {ip}")
        print("running bootstrap...")
        run_bootstrap(ip, args.identity_file, script)
        print("bootstrap complete.")


def cmd_list(args):
    resp = call("list-instances")
    data = _unwrap(resp, "list-instances")
    items = data.get("instancesSet", {}).get("item") or []
    if isinstance(items, dict):
        items = [items]
    for i in items:
        ttl_note = ""
        for m in manifest.list_entries(provider=PROVIDER):
            if m["instance_id"] == str(i.get("InstanceId") or i.get("instanceid")):
                ttl_note = m["ttl"]
                break
        iid = i.get("InstanceId") or i.get("instanceid", "?")
        name = i.get("InstanceName") or i.get("servername", "-")
        ip = i.get("IP_Address") or i.get("ip_address", "-")
        state = i.get("vm_status") or i.get("status", "-")
        print(f"{iid}\t{state}\t{name}\t{ip}\t{ttl_note}")


def cmd_show(args):
    resp = call("describe-instance", instanceid=args.target)
    print(json.dumps(resp, indent=2))


def cmd_destroy(args):
    if not args.yes:
        if input(f"Terminate instance {args.target}? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")
    call("terminate-instance", instanceid=args.target)
    manifest.remove(PROVIDER, args.target)
    print(f"terminated {args.target}")


def cmd_reap(args):
    expired = manifest.expired(provider=PROVIDER)
    if not expired:
        print("nothing expired")
        return
    for e in expired:
        iid = e["instance_id"]
        action = "would terminate" if args.dry_run else "terminating"
        print(f"{action} {iid} {e['name']} (expired {e['ttl']})")
        if not args.dry_run:
            try:
                call("terminate-instance", instanceid=iid)
            except SystemExit as err:
                print(f"  warning: {err}", file=sys.stderr)
            manifest.remove(PROVIDER, iid)


def cmd_list_plans(args):
    resp = call("describe-plan", plan_name="all", platform="cloud")
    data = _unwrap(resp, "describe-plan")
    plans = data.get("plans", {}).get("plan") or data.get("plan") or []
    if isinstance(plans, dict):
        plans = [plans]
    for p in plans:
        print(f"{p.get('plan_name', '?')}\t"
              f"{p.get('num_cpu', '?')}cpu\t"
              f"{p.get('ram_in_gb', '?')}GB\t"
              f"${p.get('price_per_hour', '?')}/hr")


def cmd_list_locations(args):
    resp = call("list-locations")
    data = _unwrap(resp, "list-locations")
    locs = data.get("Locations", {}).get("Location") or []
    if isinstance(locs, dict):
        locs = [locs]
    for l in locs:
        print(f"{l.get('LocationID') or l.get('code', '?')}\t"
              f"{l.get('city', '?')}, {l.get('country', '?')}")


def cmd_list_keys(args):
    resp = call("list-sshkeys")
    data = _unwrap(resp, "list-sshkeys")
    keys = data.get("KeysSet", {}).get("item") or data.get("sshkeys", {}).get("key") or []
    if isinstance(keys, dict):
        keys = [keys]
    for k in keys:
        print(f"{k.get('key_id', '?')}\t{k.get('key_name', '?')}")


def cmd_manifest(args):
    entries = manifest.list_entries(provider=PROVIDER)
    if args.json:
        print(json.dumps(entries, indent=2))
        return
    if not entries:
        print("(empty)")
        return
    now = datetime.now(timezone.utc)
    for e in entries:
        dt = datetime.fromisoformat(e["ttl"])
        expired = "EXPIRED" if dt <= now else ""
        print(f"{e['instance_id']}\t{e['name']}\t{e['ttl']}\t{expired}")


# ----- argparse -----

def build_parser():
    p = argparse.ArgumentParser(
        prog="atlantic_server.py",
        description="Atlantic.net Cloud server orchestrator")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Create a cloud server")
    c.add_argument("--name", required=True)
    c.add_argument("--plan", required=True, help="plan name - see list-plans")
    c.add_argument("--image", required=True,
                   help="imageid from the Atlantic portal (no public list)")
    c.add_argument("--location", required=True,
                   help="vm_location code - see list-locations")
    c.add_argument("--ssh-key-id",
                   help="key_id registered on Atlantic.net (required for bootstrap)")
    c.add_argument("--identity-file",
                   help="local private key path used to ssh in for bootstrap")
    c.add_argument("--preset",
                   help="bootstrap script from bootstrap/ (e.g. rpm-build)")
    c.add_argument("--bootstrap",
                   help="path to a custom bootstrap script to run via ssh")
    c.add_argument("--ttl",
                   help="4h | 30m | 2d | ISO8601 - recorded in local manifest")
    c.add_argument("--self-destruct", action="store_true",
                   help="not supported on Atlantic.net; will error")
    c.add_argument("--dry-run", action="store_true")
    c.set_defaults(func=cmd_create)

    sub.add_parser("list", help="List servers").set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="Show a server")
    s.add_argument("target", help="instanceid")
    s.set_defaults(func=cmd_show)

    d = sub.add_parser("destroy", help="Terminate a server")
    d.add_argument("target")
    d.add_argument("--yes", action="store_true")
    d.set_defaults(func=cmd_destroy)

    r = sub.add_parser("reap", help="Terminate expired entries from local manifest")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_reap)

    sub.add_parser("list-plans").set_defaults(func=cmd_list_plans)
    sub.add_parser("list-locations").set_defaults(func=cmd_list_locations)
    sub.add_parser("list-keys").set_defaults(func=cmd_list_keys)

    m = sub.add_parser("manifest",
                       help="Show the local TTL manifest for this provider")
    m.add_argument("--json", action="store_true")
    m.set_defaults(func=cmd_manifest)

    return p


def main():
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

---
name: aws
description: Launch, inspect, and terminate AWS EC2 instances with cloud-init presets and tag-based TTL. Use when the user wants a throwaway build rig, dev host, or static-site host on AWS and needs a public IP back quickly. Supports RPM build rigs, Node+httpd static sites, and bare Amazon Linux 2023.
homepage: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html
---

# AWS EC2 Skill

Thin Python CLI (`ec2_instance.py`) using `boto3` over your existing AWS auth. Mirrors the shape of the digitalocean skill: create / list / show / destroy / reap + discovery. Shares TTL + cloud-init helpers with the sibling skills via `../cloudlib`.

## Auth

Uses whatever boto3 picks up in this order:

1. Env vars (`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` + optional `AWS_SESSION_TOKEN`)
2. Shared config (`~/.aws/config` + `~/.aws/credentials`; set `AWS_PROFILE` to pick a profile)
3. SSO (`aws sso login`)
4. IMDS (if running on another EC2)

Region resolution: `--region` flag > `AWS_REGION` env > profile default.

Test:

```bash
./ec2_instance.py list-regions
./ec2_instance.py list-keys
```

## Dependencies

```bash
pip install boto3 pyyaml
```

Also imports from the sibling `../cloudlib` package for HTTP/TTL/cloud-init helpers.

## Recipes

### RPM build rig, 4h TTL, manual reap (default)

```bash
./ec2_instance.py --region us-east-1 create --name rpm-rig \
  --instance-type t3.small --key-name my-ec2-key \
  --security-group-ids sg-0abcd1234 --subnet-id subnet-0ef56789 \
  --preset rpm-build --ttl 4h
# later:
./ec2_instance.py --region us-east-1 reap
```

### Static-site host, self-destruct in 2h

```bash
./ec2_instance.py --region us-east-1 create --name site-demo \
  --instance-type t3.micro --key-name my-ec2-key \
  --security-group-ids sg-0abcd1234 --subnet-id subnet-0ef56789 \
  --preset node-httpd --ttl 2h --self-destruct \
  --iam-instance-profile ec2-self-destruct
```

The IAM instance profile needs an inline policy like:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "ec2:TerminateInstances",
    "Resource": "*",
    "Condition": {
      "StringEquals": {"aws:ResourceTag/auto-ttl": "true"}
    }
  }]
}
```

That condition limits the instance to terminating only instances *it itself tagged* via this skill - genuine per-resource scoping AWS gives you that DO can't.

### Bare AL2023, no TTL

```bash
./ec2_instance.py --region us-east-1 create --name scratch \
  --instance-type t3.micro --key-name my-ec2-key \
  --security-group-ids sg-0abcd1234 --preset base-al2023
```

### Discovery

```bash
./ec2_instance.py list-regions
./ec2_instance.py --region us-east-1 list-keys
./ec2_instance.py --region us-east-1 list-instance-types | grep t3
./ec2_instance.py --region us-east-1 list-amis --pattern 'al2023-ami-*-x86_64'
```

### Inspect / clean up

```bash
./ec2_instance.py --region us-east-1 list
./ec2_instance.py --region us-east-1 list --tag auto-ttl=true
./ec2_instance.py --region us-east-1 show rpm-rig
./ec2_instance.py --region us-east-1 destroy rpm-rig --yes
./ec2_instance.py --region us-east-1 reap --dry-run
./ec2_instance.py --region us-east-1 reap
```

### Dry-run the RunInstances params

```bash
./ec2_instance.py --region us-east-1 create --name test \
  --instance-type t3.micro --key-name my-ec2-key \
  --preset rpm-build --ttl 1h --dry-run
```

## Preset format

Same `x-extends` inheritance as the DO skill. Files live in `cloud-init/*.yaml` without the `#cloud-config` header. Shipped presets assume Amazon Linux 2023 (user `ec2-user`):

- `base-al2023`: minimal baseline (packages, motd). No firewall layer since AWS uses Security Groups.
- `rpm-build`: extends base + rpm-build/mock/rpmdevtools, `ec2-user` in `mock` group, `rpmdev-setuptree` run
- `node-httpd`: extends base + httpd + nodejs/npm, sample index.html

## TTL mechanics

`--ttl 4h` tags the instance with two tags:

- `auto-ttl=true` - marker for filtering/reaping and for the IAM condition above
- `TTL=ttl-YYYYMMDDTHHMMSSZ` - absolute expiry in UTC (same format the DO skill uses)

Default: nothing terminates automatically. Run `reap` manually or in your own cron.

With `--self-destruct`: a systemd oneshot timer is baked into cloud-init. At TTL it reads instance id from IMDSv2, reads region from IMDS, and calls `aws ec2 terminate-instances`. **No credentials on the instance** - it uses the IAM instance profile, which is the right AWS pattern. That's why `--self-destruct` requires `--iam-instance-profile`.

## AL2023 + aws CLI for self-destruct

The self-destruct script calls `aws ec2 terminate-instances`. AL2023 ships `aws` v2 out of the box. Other distros (Ubuntu, etc.) may need `dnf install awscli` / `apt install awscli` added to your preset's `packages:` if you swap the AMI.

## Gotchas

- **Default VPC.** If you don't pass `--subnet-id` and `--security-group-ids`, the instance lands in your default VPC/default SG - which may not exist or may not allow SSH. Pass them explicitly for anything real.
- **Key pair is per-region.** `--key-name` must match a key pair registered in the target region. `list-keys` shows the current region's keys.
- **AMI id is per-region.** `--ami i-...` bound to a region won't work elsewhere. `--ami-name-pattern` resolves to the newest match in the current region; pin with `--ami` only if you need a specific build.
- **Self-destruct IAM scoping.** The example condition (`aws:ResourceTag/auto-ttl`) lets the instance terminate only resources tagged the same way, not your entire account. Use it.
- **`t3.micro` in us-east-1** may hit free-tier vs on-demand pricing rules depending on your account. Expect `t3.small` and up for reliable reproducibility.
- **boto3 retries** are on by default. Transient 5xx auto-retries; don't panic if something takes a few seconds longer than expected.

## Files

```
ec2_instance.py          # the CLI
cloud-init/
  base-al2023.yaml       # AL2023 baseline
  rpm-build.yaml         # extends base + RPM tooling
  node-httpd.yaml        # extends base + httpd + nodejs
../cloudlib/             # shared helpers (HTTP, TTL, cloud-init, manifest)
```

## When to reach for Terraform instead

Long-lived EC2 fleets, launch templates that multiple tools consume, anything under change-management. This skill is the "one-command build rig for an afternoon" answer. See the cross-skill comparison alongside.

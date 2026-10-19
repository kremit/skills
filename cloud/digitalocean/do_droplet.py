#!/usr/bin/env python3
"""DigitalOcean droplet skill CLI.

Uses shared helpers from ../../lib/cloud for HTTP, TTL, and cloud-init logic.
Provider-specific concerns (auth, request shape, metadata service URL,
response parsing) live here.

Deps: PyYAML. Auth: DIGITALOCEAN_TOKEN.
Docs: https://docs.digitalocean.com/reference/api/api-reference/
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
from cloud import (
    HTTPError,
    TAG_MARKER,
    http_json,
    load_preset,
    merge_cloud_init,
    parse_ttl,
    render_user_data,
    systemd_oneshot_timer,
    ttl_from_tag,
    ttl_tag,
)

try:
    import yaml
except ImportError:
    sys.exit("PyYAML required: pip install pyyaml")

API = "https://api.digitalocean.com/v2"
PRESETS_DIR = Path(__file__).parent / "cloud-init"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


# ----- auth + request -----

def _token():
    t = os.environ.get("DIGITALOCEAN_TOKEN")
    if not t:
        sys.exit("DIGITALOCEAN_TOKEN not set. Create one at "
                 "https://cloud.digitalocean.com/account/api/tokens")
    return t


def api(method, path, body=None, query=None):
    """Send an authenticated DO API request. Raises HTTPError on non-2xx.

    Callers that want non-fatal behavior catch HTTPError locally;
    main() formats anything uncaught.
    """
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }
    return http_json(method, API + path,
                     headers=headers, body=body, query=query)


def format_http_error(e):
    """Pretty-print a DO HTTPError for user-facing exit messages."""
    if e.code == 401:
        return "401 unauthorized. Check DIGITALOCEAN_TOKEN."
    if e.code == 422:
        return f"422 validation error:\n{e.body}"
    if e.code == 429:
        return f"429 rate limited. Reset: {e.headers.get('RateLimit-Reset', '?')}"
    return f"{e.code} {e.reason}:\n{e.body}"


# ----- project scoping -----

def resolve_project(spec):
    """Accept a project UUID or name and return the UUID."""
    if UUID_RE.match(spec):
        return spec
    projects = api("GET", "/projects").get("projects", [])
    matches = [p for p in projects if p["name"] == spec]
    if not matches:
        names = ", ".join(p["name"] for p in projects)
        sys.exit(f"No DO project named {spec!r}. Projects: {names}")
    if len(matches) > 1:
        sys.exit(f"Multiple projects named {spec!r}; use the UUID.")
    return matches[0]["id"]


def project_droplet_ids(project_id):
    """Set of droplet ids currently assigned to the given project."""
    ids = set()
    page = 1
    while True:
        resp = api("GET", f"/projects/{project_id}/resources",
                   query={"per_page": 200, "page": page})
        for r in resp.get("resources", []):
            urn = r.get("urn", "")
            if urn.startswith("do:droplet:"):
                try:
                    ids.add(int(urn.split(":")[-1]))
                except ValueError:
                    pass
        next_link = resp.get("links", {}).get("pages", {}).get("next")
        if not next_link:
            break
        page += 1
    return ids


def assign_to_project(project_id, droplet_id):
    api("POST", f"/projects/{project_id}/resources",
        body={"resources": [f"do:droplet:{droplet_id}"]})


def _scoped_ids(args):
    """Return the set of droplet ids in --project scope, or None if no scope."""
    if not getattr(args, "project_id", None):
        return None
    return project_droplet_ids(args.project_id)


# ----- self-destruct (DO-specific script body) -----

SELF_DESTRUCT_SCRIPT = """#!/bin/bash
set -eu
source /etc/do-reaper.env
ID=$(curl -fsS http://169.254.169.254/metadata/v1/id)
curl -fsS -X DELETE "https://api.digitalocean.com/v2/droplets/$ID" \\
    -H "Authorization: Bearer $DO_REAPER_TOKEN"
"""


def add_self_destruct(cfg, when_utc, reaper_token):
    files, runs = systemd_oneshot_timer(
        name="do-self-destruct",
        when_utc=when_utc,
        script_path="/usr/local/sbin/do-self-destruct.sh",
        script_body=SELF_DESTRUCT_SCRIPT,
    )
    files.insert(0, {
        "path": "/etc/do-reaper.env",
        "permissions": "0600",
        "owner": "root:root",
        "content": f"DO_REAPER_TOKEN={reaper_token}\n",
    })
    cfg = dict(cfg)
    cfg["write_files"] = cfg.get("write_files", []) + files
    cfg["runcmd"] = cfg.get("runcmd", []) + runs
    return cfg


# ----- commands -----

def cmd_create(args):
    cfg = {}
    if args.preset:
        try:
            cfg = load_preset(PRESETS_DIR, args.preset)
        except (FileNotFoundError, ValueError) as e:
            sys.exit(str(e))
    if args.user_data:
        extra = yaml.safe_load(Path(args.user_data).read_text()) or {}
        cfg = merge_cloud_init(cfg, extra)

    tags = list(args.tag or [])
    ttl_dt = None
    if args.ttl:
        try:
            ttl_dt = parse_ttl(args.ttl)
        except ValueError as e:
            sys.exit(str(e))
        tags += [TAG_MARKER, ttl_tag(ttl_dt)]
    if args.self_destruct:
        if not ttl_dt:
            sys.exit("--self-destruct requires --ttl.")
        reaper_token = os.environ.get("DIGITALOCEAN_REAPER_TOKEN") or _token()
        cfg = add_self_destruct(cfg, ttl_dt, reaper_token)

    body = {
        "name": args.name,
        "region": args.region,
        "size": args.size,
        "image": args.image,
        "ssh_keys": args.ssh_key or [],
        "tags": tags,
        "user_data": render_user_data(cfg),
    }

    if args.dry_run:
        if args.project_id:
            print(f"# project scope: {args.project} ({args.project_id}) "
                  f"- droplet will be reassigned after create")
        print(json.dumps(body, indent=2))
        return

    resp = api("POST", "/droplets", body=body)
    droplet = resp["droplet"]
    print(f"created droplet {droplet['id']} ({droplet['name']})")
    if args.project_id:
        try:
            assign_to_project(args.project_id, droplet["id"])
            print(f"project: {args.project} ({args.project_id})")
        except HTTPError as e:
            hint = (" - token likely missing 'project:update' scope"
                    if e.code == 403 else "")
            print(f"WARNING: project assignment failed "
                  f"({e.code} {e.reason}){hint}.\n"
                  f"  droplet {droplet['id']} is in the default project; "
                  f"move it via dashboard or regenerate token with "
                  f"project:read + project:update scopes.",
                  file=sys.stderr)
    if ttl_dt:
        mode = "self-destruct on droplet" if args.self_destruct else "manual reap"
        print(f"ttl: {ttl_dt.isoformat()} ({mode})")
    if args.wait:
        ip = wait_for_ip(droplet["id"])
        print(f"ip: {ip}")


def wait_for_ip(did, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        d = api("GET", f"/droplets/{did}")["droplet"]
        if d["status"] == "active":
            for n in d.get("networks", {}).get("v4", []):
                if n["type"] == "public":
                    return n["ip_address"]
        time.sleep(3)
    sys.exit(f"Timed out waiting for droplet {did} to become active.")


def _public_ip(d):
    for n in d.get("networks", {}).get("v4", []):
        if n["type"] == "public":
            return n["ip_address"]
    return ""


def cmd_list(args):
    q = {"tag_name": args.tag} if args.tag else None
    droplets = api("GET", "/droplets", query=q).get("droplets", [])
    scoped = _scoped_ids(args)
    if scoped is not None:
        droplets = [d for d in droplets if d["id"] in scoped]
    if args.json:
        print(json.dumps(droplets, indent=2))
        return
    for d in droplets:
        print(f"{d['id']}\t{d['status']}\t{d['name']}\t"
              f"{_public_ip(d)}\t{','.join(d.get('tags', []))}")


def resolve(id_or_name, scoped_ids=None):
    """Accept a numeric id or a unique name; optionally confined to scoped_ids."""
    if id_or_name.isdigit():
        did = int(id_or_name)
        if scoped_ids is not None and did not in scoped_ids:
            sys.exit(f"Droplet {did} is not in the selected project.")
        return did
    droplets = api("GET", "/droplets",
                   query={"name": id_or_name}).get("droplets", [])
    if scoped_ids is not None:
        droplets = [d for d in droplets if d["id"] in scoped_ids]
    if not droplets:
        scope = " in the selected project" if scoped_ids is not None else ""
        sys.exit(f"No droplet named {id_or_name!r}{scope}.")
    if len(droplets) > 1:
        sys.exit(f"Multiple droplets named {id_or_name!r}: "
                 f"{[d['id'] for d in droplets]}")
    return droplets[0]["id"]


def cmd_show(args):
    d = api("GET",
            f"/droplets/{resolve(args.target, _scoped_ids(args))}")["droplet"]
    print(json.dumps(d, indent=2))


def cmd_destroy(args):
    did = resolve(args.target, _scoped_ids(args))
    if not args.yes:
        if input(f"Destroy droplet {did}? [y/N] ").strip().lower() != "y":
            sys.exit("aborted")
    api("DELETE", f"/droplets/{did}")
    print(f"destroyed {did}")


def cmd_reap(args):
    droplets = api("GET", "/droplets",
                   query={"tag_name": TAG_MARKER}).get("droplets", [])
    scoped = _scoped_ids(args)
    if scoped is not None:
        droplets = [d for d in droplets if d["id"] in scoped]
    now = datetime.now(timezone.utc)
    expired = []
    for d in droplets:
        for t in d.get("tags", []):
            dt = ttl_from_tag(t)
            if dt and dt <= now:
                expired.append((d, dt))
                break
    if not expired:
        print("nothing expired")
        return
    for d, dt in expired:
        action = "would destroy" if args.dry_run else "destroying"
        print(f"{action} {d['id']} {d['name']} (expired {dt.isoformat()})")
        if not args.dry_run:
            api("DELETE", f"/droplets/{d['id']}")


def cmd_list_projects(args):
    for p in api("GET", "/projects").get("projects", []):
        default = " (default)" if p.get("is_default") else ""
        print(f"{p['id']}\t{p['name']}{default}\t{p.get('purpose', '')}")


def cmd_list_regions(args):
    for r in api("GET", "/regions")["regions"]:
        if r.get("available"):
            preview = ",".join(r.get("sizes", [])[:3])
            print(f"{r['slug']}\t{r['name']}\t{preview}...")


def cmd_list_sizes(args):
    for s in api("GET", "/sizes", query={"per_page": 200})["sizes"]:
        if s.get("available"):
            regions = ",".join(s.get("regions", [])[:4])
            print("%-28s  %-8s  %-8s  %-10s  %s" % (
                s["slug"],
                f"{s['vcpus']}vcpu",
                f"{s['memory']}MB",
                f"${s['price_monthly']}/mo",
                regions,
            ))


def cmd_list_images(args):
    imgs = api("GET", "/images",
               query={"type": "distribution", "per_page": 200})["images"]
    for i in imgs:
        print(f"{i['slug']}\t{i['distribution']}\t{i['name']}")


def cmd_list_keys(args):
    for k in api("GET", "/account/keys")["ssh_keys"]:
        print(f"{k['id']}\t{k['fingerprint']}\t{k['name']}")


# ----- argparse -----

def build_parser():
    p = argparse.ArgumentParser(
        prog="do_droplet.py",
        description="DigitalOcean droplet orchestrator")
    p.add_argument("--project",
                   default=os.environ.get("DIGITALOCEAN_PROJECT"),
                   help="scope all operations to this DO project (name or UUID); "
                        "env: DIGITALOCEAN_PROJECT")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Create a droplet")
    c.add_argument("--name", required=True)
    c.add_argument("--region", required=True,
                   help="e.g. nyc3, sfo3 - see list-regions")
    c.add_argument("--size", required=True, help="size slug - see list-sizes")
    c.add_argument("--image", default="rockylinux-9-x64",
                   help="image slug (default: rockylinux-9-x64)")
    c.add_argument("--preset", help="cloud-init preset from cloud-init/")
    c.add_argument("--user-data",
                   help="extra cloud-config yaml merged on top of preset")
    c.add_argument("--ssh-key", action="append",
                   help="fingerprint or id; repeatable")
    c.add_argument("--tag", action="append", help="extra tag; repeatable")
    c.add_argument("--ttl",
                   help="4h | 30m | 2d | ISO8601 - tags droplet for reap")
    c.add_argument("--self-destruct", action="store_true",
                   help="also install on-droplet self-delete at TTL")
    c.add_argument("--no-wait", dest="wait", action="store_false",
                   help="don't poll for IP")
    c.add_argument("--dry-run", action="store_true",
                   help="print the POST body and exit")
    c.set_defaults(func=cmd_create, wait=True)

    l = sub.add_parser("list", help="List droplets")
    l.add_argument("--tag")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="Show a droplet")
    s.add_argument("target", help="id or unique name")
    s.set_defaults(func=cmd_show)

    d = sub.add_parser("destroy", help="Destroy a droplet")
    d.add_argument("target")
    d.add_argument("--yes", action="store_true")
    d.set_defaults(func=cmd_destroy)

    r = sub.add_parser("reap", help="Destroy expired TTL-tagged droplets")
    r.add_argument("--dry-run", action="store_true")
    r.set_defaults(func=cmd_reap)

    sub.add_parser("list-regions").set_defaults(func=cmd_list_regions)
    sub.add_parser("list-sizes").set_defaults(func=cmd_list_sizes)
    sub.add_parser("list-images").set_defaults(func=cmd_list_images)
    sub.add_parser("list-keys").set_defaults(func=cmd_list_keys)
    sub.add_parser("list-projects").set_defaults(func=cmd_list_projects)

    return p


def main():
    args = build_parser().parse_args()
    try:
        args.project_id = resolve_project(args.project) if args.project else None
        args.func(args)
    except HTTPError as e:
        sys.exit(format_http_error(e))


if __name__ == "__main__":
    main()

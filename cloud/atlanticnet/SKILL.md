---
name: atlanticnet
description: Create, inspect, and terminate Atlantic.net Cloud servers with shell bootstrap presets and TTL tracking via a local manifest. Use when the user wants a throwaway build rig, dev host, or static-site host on Atlantic.net and needs an IP back quickly. Supports RPM build rigs, Node+Apache static sites, and bare Rocky 9 servers.
homepage: https://www.atlantic.net/docs/api/
---

# Atlantic.net Cloud Server Skill

Thin Python CLI (`atlantic_server.py`) over the Atlantic.net Cloud API. Mirrors the shape of the digitalocean and aws skills as closely as Atlantic's API allows, with honest differences documented below.

## What's different from the DO/AWS skills

Atlantic.net's API is older and thinner than DO's or AWS's:

- **No cloud-init / user_data.** The API has no field for first-boot configuration. Instead, this skill **SSHes in after create** and runs a bash script (either a shipped preset in `bootstrap/` or a BYO path). Bootstrap presets are bash equivalents of the cloud-init presets in the other skills.
- **No server-side tags.** TTL is tracked in a **local JSON manifest** (`~/.local/state/cloudlib/ttl-manifest.json` by default, shared across all cloudlib-using skills). The `reap` command reads this manifest.
- **No `--self-destruct`.** Without cloud-init we can't cleanly inject a systemd timer at boot. Use `--ttl` + `reap` from a host you control (cron, systemd-timer on your laptop).

Within those limits, the skill looks and feels the same as the others.

## Getting an API key

1. Sign in at https://cloud.atlantic.net/.
2. Go to **API Access** (from the account menu) and generate a new API key.
3. You'll get an **Access Key ID** and a **Secret Key** (shown once - copy both).
4. Export in your shell:

```bash
export ATLANTIC_ACCESS_KEY_ID="AKIA..."
export ATLANTIC_SECRET_KEY="..."
```

Test:

```bash
./atlantic_server.py list-keys
./atlantic_server.py list-locations
```

## Dependencies

```bash
pip install pyyaml    # lib/cloud uses it; stdlib covers the rest
```

Also imports from the shared `../../lib/cloud` package.

You also need **an SSH key registered on Atlantic.net** (`list-keys` to see ids, or register via the portal) and the matching **private key locally** for the post-create bootstrap step.

## Recipes

### RPM build rig, 4h TTL, manual reap

```bash
./atlantic_server.py create --name rpm-rig \
  --plan G2.4GB --location USEAST3 --image rocky9 \
  --ssh-key-id 12345 --identity-file ~/.ssh/atlantic_id_ed25519 \
  --preset rpm-build --ttl 4h
# later:
./atlantic_server.py reap
```

### Static-site host

```bash
./atlantic_server.py create --name site-demo \
  --plan G2.2GB --location USEAST1 --image rocky9 \
  --ssh-key-id 12345 --identity-file ~/.ssh/atlantic_id_ed25519 \
  --preset node-apache --ttl 2h
```

### Bare Rocky 9, BYO bootstrap

```bash
./atlantic_server.py create --name scratch \
  --plan G2.2GB --location USEAST1 --image rocky9 \
  --ssh-key-id 12345 --identity-file ~/.ssh/atlantic_id_ed25519 \
  --bootstrap ./my-setup.sh
```

### Discovery

```bash
./atlantic_server.py list-locations
./atlantic_server.py list-plans
./atlantic_server.py list-keys
```

### Inspect / clean up

```bash
./atlantic_server.py list                   # live via API (manifest TTL annotated)
./atlantic_server.py manifest               # local TTL manifest only
./atlantic_server.py show 12345
./atlantic_server.py destroy 12345 --yes
./atlantic_server.py reap --dry-run
./atlantic_server.py reap
```

### Dry-run the request

```bash
./atlantic_server.py create --name test \
  --plan G2.2GB --location USEAST1 --image rocky9 \
  --ssh-key-id 12345 --preset rpm-build --ttl 1h --dry-run
```

## Bootstrap format

Files in `bootstrap/*.sh` are standalone bash. One skill-only marker is recognized:

- `# @extends <name>` in the first comment block - prepend that preset's contents verbatim, then run this one.

Shipped presets (all assume Rocky 9):

- `base-rocky9.sh`: dev user with NOPASSWD sudo, firewalld, ssh allowed, common packages
- `rpm-build.sh`: extends base + rpm-build/mock/rpmdevtools, CRB repo enabled
- `node-apache.sh`: extends base + httpd + Node LTS via NodeSource, firewall opens 80/443

Add your own by dropping `foo.sh` into `bootstrap/` and using `--preset foo`.

## TTL mechanics

`--ttl 4h` records an entry in the local manifest at `~/.local/state/cloudlib/ttl-manifest.json` (override with `$CLOUDLIB_TTL_MANIFEST`). The entry includes provider=atlanticnet, instance id, name, ttl, created_at, and metadata (plan + location).

`reap` reads that file, finds entries past their TTL for this provider, and calls `terminate-instance` on each. Nothing happens automatically; you run `reap` manually or wire it into your own cron.

This is the same pattern the DO skill uses with tags, just moved client-side because the API lacks tags.

## Gotchas

- **No `list-images` API.** You pass an `imageid` (e.g. `rocky9`). Current image ids: check the Atlantic portal when creating a server manually - the slug is what to pass as `--image`. The skill intentionally does not hard-code these because they change.
- **Root-first login.** Atlantic injects your `key_id` as root's authorized_keys. The bootstrap script creates a `dev` user and copies the key over. Subsequent SSH should be `ssh -i key dev@ip`.
- **SSH bootstrap timing.** The skill polls for port 22 up to 180s after create. Slow locations sometimes take longer. If it fails, the server is still created and in the manifest; SSH in manually and run the bootstrap script yourself.
- **Signature encoding.** The HMAC-SHA256 signature is base64-encoded and then URL-encoded by `urllib` when building the query. If you see 403 from the API, verify both env vars are set and no trailing whitespace crept in.
- **Manifest portability.** The manifest file is on your laptop. If you create a server on laptop A and try to reap from laptop B, B won't know about it. Either share the file or run `reap` from the same host that created.
- **Price model.** Atlantic's `run-instance` accepts `term` (we hardcode `hourly`). If you want monthly billing, edit the call in `atlantic_server.py`.

## Files

```
atlantic_server.py       # the CLI
bootstrap/
  base-rocky9.sh         # Rocky 9 baseline (bash)
  rpm-build.sh           # extends base + RPM tooling
  node-apache.sh         # extends base + httpd + Node LTS
../../lib/cloud/         # shared helpers (HTTP, TTL, cloud-init, manifest)
```

## Why this skill is justified vs Terraform for Atlantic.net specifically

Atlantic.net has no first-party Terraform provider. Community options are thin and the `terraform-provider-restapi` approach loses you meaningful plan diffs. This skill is the pragmatic path until Atlantic publishes a real provider. If they ever do, the cloudlib helpers here (TTL manifest, bootstrap dispatch) are still worth keeping for any throwaway-server workflow.

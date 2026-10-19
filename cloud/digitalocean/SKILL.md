---
name: digitalocean
description: Spin up, inspect, and destroy DigitalOcean droplets with cloud-init presets and TTL tagging. Use when the user wants to stand up a throwaway build rig, dev host, or static-site host on DigitalOcean and get an IP back quickly. Supports RPM build rigs, Node+Apache static sites, and bare Rocky 9 droplets out of the box.
homepage: https://docs.digitalocean.com/reference/api/api-reference/
---

# DigitalOcean Droplet Skill

Thin Python orchestrator over the DO v2 API (`do_droplet.py`) plus a library of cloud-init presets in `cloud-init/`. Default auto-cleanup is tag-based (user runs `reap`); opt-in `--self-destruct` puts a systemd timer on the droplet for unattended cleanup.

## Getting a DigitalOcean API token

1. Sign in at https://cloud.digitalocean.com/.
2. If you want blast-radius isolation, create (or switch to) a dedicated **Team** for scratch work first: top-left team switcher → **Create a team**. Every token is scoped to exactly one team, so anything that token can do is capped to that team's resources and budget. See "Token scoping" below.
3. Go to **API** in the left nav, or straight to https://cloud.digitalocean.com/account/api/tokens.
4. Click **Generate New Token**.
5. Give it a name (e.g. `droplet-skill-laptop`) and choose **Custom Scopes**:
   - For the main token: at minimum `droplet:create read update delete`, `ssh_key:read`, `image:read`, `region:read`, `size:read`, `tag:create read delete`.
   - **If you use `--project`**: also add `project:read` and `project:update`. Without `project:update`, droplets are created successfully in the default project, but the skill can't move them into the scoped project - it warns and leaves the droplet where it landed for you to move in the dashboard.
   - For a reaper token used by `--self-destruct`: `droplet:delete read`, nothing else.
6. Set an expiry (90 days is a sane default; never pick "no expiry" for tokens that will live on a droplet).
7. Copy the token once - DO only shows it at creation.
8. Put it in your shell init:

```bash
export DIGITALOCEAN_TOKEN="dop_v1_..."
```

Test it:

```bash
./do_droplet.py list-keys
```

## Scoping operations to a DO Project (recommended)

DO lets you group resources into **Projects** (dashboard → Projects). Every droplet lives in exactly one project; the default project is "your account's name" unless you've changed it. Projects are a strong blast-radius cap: if you confine this skill to a project called `Testing`, a bug in the script (or in the model calling it) physically cannot see - let alone destroy - droplets in `Production`.

Pass `--project` as a top-level flag before the subcommand, or set `DIGITALOCEAN_PROJECT`. Accepts either a project **name** or a **UUID**:

```bash
# scope this invocation only
./do_droplet.py --project Testing list
./do_droplet.py --project <project-uuid> reap

# or set once for the shell session
export DIGITALOCEAN_PROJECT="Testing"
./do_droplet.py list
./do_droplet.py destroy rpm-rig --yes   # errors if rpm-rig is not in Testing
```

Find your project IDs:

```bash
./do_droplet.py list-projects
```

### Behavior

- `create` - creates the droplet (DO puts it in the default project first), then immediately moves it into `--project`. There is a brief window (seconds) where the droplet is in the default project; acceptable for dev workflows. **If the reassignment fails** (e.g. token lacks `project:update` scope), the skill warns but does not destroy the droplet - it stays in the default project and you move it manually. The droplet create is treated as the "real" work; project placement is best-effort.
- `list` / `reap` - only show/touch droplets in the scoped project.
- `show` / `destroy` - verify the target droplet is in the scoped project; error otherwise, even when you pass a droplet id directly. This is the blast-radius cap.
- `list-projects` / `list-regions` / `list-sizes` / `list-images` / `list-keys` - unaffected; these are account-level reference data.

### What DO Projects do NOT restrict

API tokens are still scoped to **Teams**, not Projects. The `--project` flag is a client-side filter on top of a team-scoped token, not an API-level permission. For full isolation, combine both: a dedicated Team for scratch work + `--project Testing` within that team. See "Token scoping" below.

## Dependencies

```bash
pip install pyyaml    # only runtime dep
```

The skill also imports from the shared `lib/cloud/` package in this repo (`../../lib/cloud`), which bundles shared HTTP/TTL/cloud-init helpers used by the AWS and Atlantic.net skills too. No install needed - the script adds its parent dir to `sys.path`.

## Token scoping

Every DO token is already bound to one Team and cannot see resources in other teams. That alone is a strong cap - a compromised token can only touch what's in its team.

For `--self-destruct`, where a token ends up written to the droplet, stack three cheap safeguards:

1. **Dedicated Team** for throwaway droplets, separate billing alerts.
2. **Second token** (`DIGITALOCEAN_REAPER_TOKEN`) with only `droplet:delete read` custom scopes.
3. **Short expiry** (7-30 days) on the reaper token; rotate manually.

```bash
export DIGITALOCEAN_REAPER_TOKEN="dop_v1_..."
```

If `DIGITALOCEAN_REAPER_TOKEN` is unset, the main token is used - fine for experimentation, not for anything you wouldn't want deleted if the droplet were compromised.

---

## Recipes

### RPM build rig scoped to a Testing project, 4h TTL, manual reap (safest default)

```bash
./do_droplet.py --project Testing create --name rpm-rig \
  --region nyc3 --size s-2vcpu-4gb \
  --preset rpm-build --ssh-key 12345678 --ttl 4h
# later, manually or via your own cron:
./do_droplet.py --project Testing reap
```

### Static site host, on-droplet self-destruct after 2h

```bash
./do_droplet.py create --name site-demo --region sfo3 --size s-1vcpu-2gb \
  --preset node-apache --ssh-key 12345678 --ttl 2h --self-destruct
```

### Bare Rocky 9, no TTL, no cleanup

```bash
./do_droplet.py create --name scratch --region nyc3 --size s-1vcpu-2gb \
  --preset base-rocky9 --ssh-key 12345678
```

### Merge custom cloud-config on top of a preset

```bash
./do_droplet.py create --name custom --region nyc3 --size s-1vcpu-2gb \
  --preset base-rocky9 --user-data ./my-extra.yaml --ssh-key 12345678
```

### Discovery

```bash
./do_droplet.py list-keys                     # SSH keys in your DO account
./do_droplet.py list-regions                  # regions + available sizes preview
./do_droplet.py list-sizes | grep 2gb         # pick a size slug
./do_droplet.py list-images | grep -i rocky   # confirm current image slug
```

### Inspect / clean up

```bash
./do_droplet.py list                          # all droplets
./do_droplet.py list --tag auto-ttl           # only the ones this skill created with --ttl
./do_droplet.py show rpm-rig
./do_droplet.py destroy rpm-rig --yes
./do_droplet.py reap --dry-run                # preview expired
./do_droplet.py reap                          # destroy expired
```

### Dry-run the POST body (no API call)

```bash
./do_droplet.py create --name test --region nyc3 --size s-1vcpu-2gb \
  --preset rpm-build --ttl 1h --dry-run
```

---

## Preset format

Files in `cloud-init/*.yaml` are normal cloud-config YAML *without* the `#cloud-config` header (the skill adds it when posting). One skill-only key is supported:

- `x-extends: <preset-name>` - inherit from another preset. Lists (`packages`, `runcmd`, `write_files`) concat base-then-child; dicts merge; scalars overwrite.

Add your own preset by dropping `foo.yaml` into `cloud-init/` and using `--preset foo`. Shipped presets:

- `base-rocky9`: Rocky 9 baseline - `dev` user with NOPASSWD sudo, firewalld + ssh open, common packages
- `rpm-build`: extends base + rpm-build/rpmdevtools/mock/rpmlint, `dev` in `mock` group, `rpmdev-setuptree` run, CRB repo enabled
- `node-apache`: extends base + httpd + Node LTS via NodeSource, firewall opens 80/443, sample index.html

---

## TTL mechanics

`--ttl 4h` tags the droplet with two tags:

- `auto-ttl` - flat marker for filtering/reaping
- `ttl-YYYYMMDDTHHMMSSZ` - absolute expiry in UTC

Accepted forms: `30m`, `4h`, `2d`, or ISO8601.

**By default nothing is deleted automatically.** You run `reap` manually or wire it into your own cron/systemd-timer on a host you control. This is deliberate - the skill does not touch your crontab.

With `--self-destruct`, the skill additionally writes a systemd oneshot service + timer into the cloud-init blob. At the TTL, the timer fires a shell script that: (1) reads the reaper token from `/etc/do-reaper.env` (mode 0600), (2) fetches the droplet id from DO metadata service (`169.254.169.254/metadata/v1/id`), (3) calls `DELETE /v2/droplets/{id}` against the DO API.

---

## Gotchas

- **Self-destruct token scope.** DO does not support per-resource tokens, but it does support per-team tokens plus custom scopes. A compromised reaper token can delete any droplet *in that team*, not your entire account. See "Token scoping" above for the recommended dedicated-team + `droplet:delete` + short-expiry stack.
- **Rocky image slug** is `rockylinux-9-x64`, not `rocky-9` or `rocky9`. Run `list-images | grep -i rocky` to confirm current slugs; DO occasionally bumps them.
- **Size/region compatibility.** Not every size is in every region. A 422 on create is almost always this - check `list-regions` for what's available where.
- **SSH keys must already exist in your DO account** before `create` (see `list-keys`). You pass a fingerprint or numeric id (repeat `--ssh-key` for multiple). If you omit `--ssh-key`, DO emails a root password, which you do not want.
- **Cloud-init failures do not fail the create.** The droplet comes up and returns an IP even if your yaml is broken. SSH in and `sudo cat /var/log/cloud-init-output.log` to debug.
- **YAML colon-space in runcmd breaks parsing.** A `runcmd` entry like `- curl -H "Content-Type: text/plain" ...` gets parsed as a dict because YAML sees `Content-Type: text/plain` and makes a mapping - quotes around the header don't help. Cloud-init then errors with `TypeError: Unable to shellify type 'dict'`. Fix: wrap the whole line in a `|` block scalar, drop the space after the colon (`Content-Type:text/plain` is valid HTTP), or use argv list form (`[curl, -H, "Content-Type: text/plain", ...]`).
- **DO base images drift behind current repos.** The `rockylinux-9-x64` image can be point-release 9.2 while current mirrors serve 9.5+. A bare `dnf install <pkg>` pulls newer deps (notably `openssl-libs`) without upgrading `openssh-server`, leaving sshd built against an OpenSSL ABI it can't link at runtime - SSH refuses connections with no obvious log. Fix: always `package_upgrade: true` (or `dnf -y update` as the first runcmd) before installing anything. The shipped `base-rocky9` preset already does this.
- **`users:` without `- default` locks you out.** If a preset or your `--user-data` defines a `users:` block, the *first* entry must be the literal string `default`, e.g. `users: [default, {name: dev, ...}]`. Cloud-init otherwise treats the block as a full replacement of the image's default user setup, including the ssh_authorized_keys module that puts your DO SSH key on root. The shipped `base-rocky9`/`rpm-build`/`node-apache` presets already include `- default`. The `minimal-rocky9` preset has no `users:` block at all, so it's immune.
- **DO tag characters.** Colons and periods are not allowed in tags; the skill formats timestamps as `ttl-20260423T120000Z` to comply.
- **Firewalld is enabled by default.** Base preset opens SSH; node-apache opens 80/443. For other ports add to your own user-data or a new preset.
- **`--wait` is on by default** for `create` and polls up to 120s. Pass `--no-wait` to skip polling (just prints the droplet id).

---

## If you meant App Platform, not Droplets

This skill is Droplets (raw VMs) only. If the user wants DO's **App Platform** (PaaS - you hand it a repo, DO runs containers behind a managed LB), neither this skill nor the sibling AWS/Atlantic skills cover it. Reach for one of these instead:

- **[digitalocean-labs/do-app-platform-skills](https://github.com/digitalocean-labs/do-app-platform-skills)** - DO's official skills repo for App Platform: designer, migration, deployment, managed postgres, networking, troubleshooting. Uses `doctl` under the hood.
- **[DO MCP server](https://www.digitalocean.com/community/tutorials/control-apps-using-mcp-server)** - `npx @digitalocean/mcp` gives Claude Desktop / Cursor / Claude Code typed tool calls for App Platform apps (list/create/delete, restart, fetch logs, validate specs). Also App Platform only at time of writing.

Quick heuristic: if the user talks about a **repo, a build command, a runtime, or managed postgres**, that's App Platform - send them to the links above. If they talk about an **IP, SSH, a cloud-init file, or RPM/Apache/systemd**, that's Droplets - this skill.

## When to use this vs Terraform

Use this skill when the droplet is *ephemeral scratch* - build rig, demo host, one-off test - and the value is "stand it up in one command, get an IP, throw it away." State tracking is deliberately thin: tags and the skill's `reap` command.

Reach for Terraform when the droplet (or fleet) is *state you keep and care about drift in*. See the conversation comparison alongside this skill for the full rundown.

---

## Files

```
do_droplet.py            # the CLI - create/list/show/destroy/reap + list-* discovery
cloud-init/
  base-rocky9.yaml       # Rocky 9 baseline
  rpm-build.yaml         # extends base + RPM tooling
  node-apache.yaml       # extends base + httpd + Node LTS
../../lib/cloud/         # shared helpers (HTTP, TTL, cloud-init) used by sibling provider skills
```

# Service Migration Skill: Design

Date: 2026-09-24
Skill path: `sysadmin/service-migration/`

## Goal

Research an installed system service (named, httpd, amavis/SpamAssassin, etc.), discover its dependencies and undocumented customizations, write a condensed Markdown migration plan, and prove the plan with a dry-run migration into a sandbox VM or podman container. The skill never performs the live cutover.

## Non-goals

- Live cutover: stopping, restarting, or writing to the source service.
- Copying private key material or secrets.
- Distros without systemd. RHEL-family (Rocky/Alma/Fedora) is the primary target; Debian-family commands can be added later.

## Layout

```
sysadmin/service-migration/
  SKILL.md           # runbook: phases, judgment rules, safety rails
  discover.sh        # read-only fact gatherer, emits JSON
  verify.sh          # post-replay checks on the dry-run target, emits JSON
  redact-expr.sh     # builds sed scripts that blank credential values (view or replay)
  plan_template.md   # condensed plan skeleton
```

## Transport

The target is either `local` or an SSH destination (`user@host` or an ssh_config alias):

- `local`: `sudo bash discover.sh <service>`
- remote: `ssh <target> 'sudo bash -s -- <service>' < discover.sh`

Nothing is installed or left on the source host, and scripts stream over stdin. `verify.sh` works the same way against the dry-run target.

When non-interactive sudo is not available on the source, Claude asks the user to grant temporary key-only root access rather than trying a password prompt, since the script streams over stdin across dozens of remote calls. The user makes that change themselves; Claude never edits `authorized_keys` or `sshd_config` on the source. Phase 4 includes removing that temporary access.

## Output location

- If CWD is inside this skills repo (`git rev-parse --show-toplevel` equals the repo containing the skill): write to `./tmp/`. `tmp/` is gitignored.
- Otherwise: write to `./migrations/`.

Files per run:

- `<host>-<service>-<YYYY-MM-DD>.md`: the plan
- `<host>-<service>-discover.json`: raw discovery, one file per service visited

## Phases

### 1. Discover

`discover.sh <service>` gathers the following, read-only, as JSON:

- **Unit:** `systemctl cat`, `systemctl show` (ExecStart, User, Group, EnvironmentFile, ReadWritePaths, After/Requires/Wants), drop-ins, active/enabled state
- **Package:** owning package, version, `rpm -V` output (modified shipped files), config files not owned by any package
- **Config tree:** file list for `/etc/<service>` plus paths from the unit's EnvironmentFile and ExecStart args, with owner, mode, and SELinux context
- **Data paths:** directories from unit settings, config directives (directory/path/file-like keys), and the package's `%dir` entries under `/var`, `/srv`, `/opt`
- **Network:** listening sockets for the service's PIDs (`ss -tulpn`) and established outbound connections (`ss -tupn`)
- **Host scaffolding:** SELinux booleans that differ from default plus custom modules (`semodule -l` with a priority 400 filter, `semanage -o`), firewalld zones/services/ports, cron entries and systemd timers that reference the service or its paths, logrotate configs, sudoers entries; files whose SELinux label differs from policy via `restorecon -Rnv` on data paths and /etc/<service> (read-only)
- **References:** grep across the config tree for
  - cert/key paths (`*.crt`, `*.pem`, `*.key`, `ssl_*`, `tls_*`, `SSLCertificate*`)
  - credential-like keys (`password`, `secret`, `token`, `key=`), recording **file, line, and key name only, never the value**
  - other services: unix sockets, `host:port` pairs, hostnames, and DSNs
  - private key files (by name: *.key, *.keys, *.p12, *.pfx, *.private, /etc/*/private/*; or a PEM "PRIVATE KEY" armor first line) are excluded from every content scan; the services list keeps only scheme://host[:port], unix socket paths without query strings, and host:port pairs
  - `secret_files`: config files and small files under data paths that are key-like by path, credential stores by name (sasl_passwd, htpasswd, *secret(s), *credential(s)), or contain a PEM "PRIVATE KEY" block anywhere (checked with `grep -q`, a boolean only). They are excluded from every content scan, from the dry-run copy, and from the safe view

Claude recurses on cross-service references that map to a local unit, for example amavis → spamd socket → `spamassassin.service`. Recursion stops at depth 3 and uses a visited set. References that resolve off-host get recorded as "already remote" and are not followed.

**Hard rule:** only read-only commands run against the source. Allowlist: `cat`, `ls`, `stat`, `find`, `grep`, `sed` (never `-i`), `ps`, `du`, `ss`, `systemctl show|cat|is-*|list-*`, `rpm -q*|-V`, `semanage * -l`, `semodule -l`, `getsebool`, `restorecon -n`, `firewall-cmd --list-*|--get-*`, `crontab -l`, `openssl x509 -noout` (public certs only), `tar -c` (dry-run export only).

### 2. Plan

Claude fills `plan_template.md`, keeping it to 1-2 screens per service:

1. **Summary:** service, package/version, source OS, destination verdict (VM or container), confidence
2. **Dependency graph:** a tree with each edge tagged `migrate-with`, `already-remote`, or `replace-at-dest`
3. **Carry-over manifest:** config paths, data paths, and undocumented/non-default options (from `rpm -V` plus unowned files)
4. **Host scaffolding:** SELinux recipe (mislabels, carried customizations, dry-run fixes with the denial each resolves), firewalld, cron/timers, logrotate, sudoers
5. **Cert and credential checklist:** config key → path → owner/mode → expiry. Expiry comes from public certs only. Everything in this list is re-provisioned by hand.
6. **Exposure and split-brain flags** (see below)
7. **Dry-run result:** appended after phase 3
8. **Human cutover runbook:** ordered steps, each with a rollback line

**Container verdict heuristic:**
- Suggest podman when: single process, few host hooks, state in clear volume paths.
- Suggest VM when: custom SELinux modules, multi-daemon pipelines, raw sockets, kernel tunables, or heavy cron coupling.

### 3. Dry run

- **VM (default):** provision through the sibling `digitalocean` skill with `--project Testing --ttl 4h`, using the preset closest to the source OS.
- **Container:** `podman run` of a base image matching the source OS family, kept alive with `sleep infinity`. Packages are installed with `podman exec`, and the daemon runs in the foreground from its ExecStart. `verify.sh --pid` replaces the systemd and SELinux checks; host-side AVCs and `podman port` bindings are checked separately.
- **Replay:** install packages first, then `tar` the manifest excluding private keys and credential-bearing files. Credential-bearing files are streamed through `redact-expr.sh ... replay`, which runs sed on the source, so values arrive as `CHANGEME-dryrun` and real values never leave the host. One self-signed placeholder pair is installed at every cert and key path. The SELinux baseline is applied, then `restorecon -RF`.
- Run `verify.sh <service> --listen "<proto> <addr:port>"... --since <epoch seconds>` on the target.
- Append pass/fail to the plan, then destroy the target unless the user asks to keep it.

`verify.sh` checks:
- unit is active and there are no failed dependencies
- listening sockets match the source's addresses and ports
- no new outbound connections to public IPs during a short observation window
- the service answers a basic health probe per type (`dig @localhost`, `curl -I`, `spamc -K`, or a port connect as fallback)
- no AVC denials since start (`--since` in epoch seconds, matched against the `msg=audit(EPOCH` stamp rather than `ausearch -ts`, whose date format follows the locale; all domains counted on the disposable target), with a permissive-domain + `audit2allow` fix loop on the dry-run target only

## Exposure and split-brain flags

Claude checks these during planning and `verify.sh` enforces the network ones:

- **Bind drift:** the source listens on loopback or a private IP only, but the destination config or container port mapping would bind `0.0.0.0` or publish the port.
- **Unjustified egress:** outbound connections to public IPs the service has no business making, such as open recursion on a resolver or an MTA relaying out when it only did local delivery.
- **Dual-live risk:** for stateful services (zones, mail queues, DBs), the runbook adds a "stop source before final sync" gate.
- **Identity collisions:** hostname-based configs, DKIM selectors, NS/SOA records, and SPF/MX/PTR pointing at the old IP are flagged for DNS/identity updates.

Any network drift makes the dry run fail.

## Safety rails

- The source is read-only (allowlist above). No writes and no service control.
- Credential and key values are never read or copied. Only references are recorded.
- The dry-run target always carries a TTL and gets destroyed by default.
- The `digitalocean` skill must use a scoped `--project` for provisioning.

## Repo housekeeping

- Add `tmp/` to `.gitignore`.
- Add a stub root `README.md` indexing the skills.

## Testing

A lean suite of about 24 assertions covers secret leaks, SELinux parsing (`semanage`/`semodule`/`restorecon` in both output formats), and verify's drift, egress, and denial detection. Other parsers are covered by live runs against `sshd`/`chronyd` and a droplet end-to-end run that includes a deliberate mislabel.

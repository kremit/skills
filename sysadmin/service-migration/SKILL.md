---
name: service-migration
description: Research an installed daemon or system service (named, httpd, postfix, amavis, spamassassin, etc.) on a local or SSH-reachable host, recursively map its dependencies, undocumented config, data paths, SELinux labels and policy, certs, and credential references. Then, write a condensed Markdown migration plan, and prove it with a dry-run replay into a throwaway DigitalOcean droplet or podman container. Use when the user wants to move, migrate, containerize, or rehost a system service or daemon.
---

# Service Migration

Four phases: **discover → plan → dry run → stop.** The scripts gather and check facts. You make the judgment calls: what is in scope, what is risky, VM or container, and which SELinux rules are justified.

## Hard rules

- 🛑 **The source is read-only.** Commands allowed there: `cat`, `ls`, `stat`, `find`, `grep`, `sed` (without `-i`), `ps`, `du`, `ss`, `systemctl show|cat|is-*|list-*`, `rpm -q*|-V`, `semanage * -l`, `semodule -l`, `getsebool`, `restorecon -n` (never without `-n`), `firewall-cmd --list-*|--get-*`, `crontab -l`, `openssl x509 -noout`, `tar -c`. Never stop, restart, reload, relabel, or write anything. Any temporary root-access change on the source, for SSH setup, is made by the **user**, never by Claude or the agent; it can remind the user at the end of the task to revert the change.
- 🔐 **Never read credential values or private keys.** Never `cat` a file listed in `references.credentials`; use the safe view below. Never open a `references.certs` entry of kind `key`, and never open, view, or copy a file listed in `secret_files`.
- 🧪 **The dry-run target is disposable.** It always has a TTL and is destroyed at the end unless the user asks to keep it. SELinux experiments (`audit2allow`, `semodule -i`, permissive domains) and file writing happen only there.
- ✋ **IMPORTANT: No live, automated cutover.** The plan's runbook is for the human. The agent should pause and flag to the user if it looks like any portion of a test run might end up exposed to the public internet or communicate outbound with production settings or keys (split-brain risk).

## Setup

```bash
SKILL_DIR=<directory containing this SKILL.md>
REPO=$(git -C "$SKILL_DIR" rev-parse --show-toplevel 2>/dev/null)
if [[ -n $REPO && $(git rev-parse --show-toplevel 2>/dev/null) == "$REPO" ]]; then OUT=./tmp; else OUT=./migrations; fi
mkdir -p "$OUT"
```

The target is `local` or an SSH destination. For remote targets, first run `ssh <host> sudo -n true`. If it succeeds, use `ssh <host> 'sudo bash -s -- <service>' < script` as shown below.

If it fails, password prompts won't work here: the script streams over stdin, one run makes dozens of remote calls, and a NOPASSWD rule for `bash` is equivalent to giving that account root anyway. Ask the **user** to grant temporary root key access; Claude never makes these changes on the source itself:

1. Add to `/root/.ssh/authorized_keys`:
   `from="<your-ip>",expiry-time="<YYYYMMDD>",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty <pubkey> migration-temp`
   (`expiry-time` needs OpenSSH 7.7+; on older sshd, rely on removal instead.)
2. Hardened hosts block remote root, so temporarily allow key-only root login:
   - EL8+/EL9: create `/etc/ssh/sshd_config.d/00-migration-temp.conf` containing `PermitRootLogin prohibit-password`.
   - EL7 has no `sshd_config.d`, so edit the `PermitRootLogin` line in `/etc/ssh/sshd_config` directly.
   - Then run `sshd -t && systemctl reload sshd`.
3. Use `root@<host>` as the target, with no `sudo`.

**Fallback**, when neither is possible: the user runs `sudo bash discover.sh <svc> > /tmp/x.json` on the host themselves and copies the file back. Mark the plan **partial**.

## Phase 1: Discover

```bash
# remote
ssh <host> 'sudo bash -s -- <service>' < "$SKILL_DIR/discover.sh" > "$OUT/<host>-<service>-discover.json"
# local
sudo bash "$SKILL_DIR/discover.sh" <service> > "$OUT/$(hostname -s)-<service>-discover.json"
```

Exit 2 with `{"error":"unit not found"}` means the unit name is wrong. Try `systemctl list-units --type=service | grep -i <name>` on the source. Query the JSON in slices with jq, e.g. `jq '.selinux' file`, rather than reading it whole.

**Recurse** into dependencies, capped at depth 3, with a visited set:

- `references.services` unix socket: `ss -xlpn | grep -F <path>` gives the pid, then `ps -o unit= -p <pid>` gives the unit.
- `references.services` pointing at localhost or the host's own IP: `ss -tlpn 'sport = :<port>'` gives the pid, then `ps -o unit= -p <pid>` gives the unit.
- `unit.After` / `unit.Requires` entries that are real services, not `*.target` and not `systemd-*`, are candidates.
- Other hosts are recorded as **already-remote** and not followed. The destination must still reach them.
- Skip base-system units (sshd, chronyd, dbus, NetworkManager, firewalld, auditd, rsyslog, journald) unless a config names them directly.

**Safe view** of a config file, with credential values blanked on the source and comments stripped. Never use it on a file in `secret_files`; those are key or credential stores with nothing safe to show.

```bash
bash "$SKILL_DIR/redact-expr.sh" "$OUT/<host>-<svc>-discover.json" <file> view |
    ssh <host> "sudo sed -E -f /dev/stdin <file>" | grep -vE '^[[:space:]]*(#|;|$)'
# local target
sudo sed -E -f <(bash "$SKILL_DIR/redact-expr.sh" "$OUT/<host>-<svc>-discover.json" <file> view) <file> |
    grep -vE '^[[:space:]]*(#|;|$)'
```

Use the safe view for every file in `package.modified` and every `config_files` entry with `"package": null`, except files in `secret_files`. That is where undocumented customizations live.

**SELinux facts to note:**
- Every `selinux.mislabeled` entry. These usually come from a past `chcon` that was never recorded with `semanage fcontext`.
- If `selinux.mislabeled_truncated` is true, the mislabel list is partial. Rerun `restorecon -Rnv <path>` per data path, or note it in plan section 4.
- The `selinux` customizations that relate to this service's paths, ports, and domain. The lists are host-wide, so drop anything unrelated.
- Any listen port not covered by the domain's port type: `semanage port -l | grep -w <port>`.

## Phase 2: Plan

Copy `plan_template.md` to `$OUT/<host>-<service>-<YYYY-MM-DD>.md` and fill in every section. Keep it to 1-2 screens per service and delete template lines that don't apply.

- **Verdict heuristic.** Choose a **container** for a single process, few host hooks, and state in clear volume paths. Choose a **VM** for custom SELinux modules, multi-daemon pipelines (e.g. amavis → spamd → clamd), raw sockets, kernel tunables, or heavy cron coupling. The user can override it.
- **SELinux recipe (section 4).**
  - For each mislabeled path, prefer `semanage fcontext` for the path as it is, or moving the data to a standard labeled location, over a policy module.
  - Carry only the customizations that relate to this service.
  - Leave "Dry-run fixes" empty until Phase 3.
- **Exposure checks (section 6):**
  - **Bind drift:** compare `network.listen` with what the destination config or `podman -p` mapping would bind. Loopback or private becoming `0.0.0.0` or published, for example, is a flag.
  - **Unjustified egress:** compare `network.outbound` and config intent against what the service should reach. Open recursion on a resolver and relaying out from local-delivery MTAs are flags.
  - **Dual-live risk:** zone files, mail queues, and DBs need a "stop source before final sync" gate.
  - **Identity collisions:** listen directives bound to source IPs, NS/SOA, MX/SPF/PTR, DKIM selectors, and hostname-based vhosts.
- **OS jumps** such as EL7 → EL10 change package majors, config syntax, and policy module names. Flag every directive that will break.

Show the user the plan path and a 2--4-line summary. Before Phase 3, ask which destination to use: droplet (default) or container.

## Phase 3: Dry run

### Provision

**Droplet (default)** uses the sibling `digitalocean` skill. Rocky images run SELinux enforcing, which is what makes this dry run worth doing.

```bash
DO="$SKILL_DIR/../../cloud/digitalocean/do_droplet.py"
"$DO" --project Testing create --name mig-<service> --region nyc3 --size s-1vcpu-2gb \
    --preset base-rocky9 --ssh-key <id> --ttl 4h
```

Use `s-2vcpu-4gb` for clamd or anything memory-hungry. The destination is `root@<ip>`, called `dst` below.

**Container** requires podman locally (`sudo dnf install -y podman`).

```bash
podman run -d --name mig-<service> -p 127.0.0.1:<hostport>:<port> quay.io/rockylinux/rockylinux:9 sleep infinity
podman exec mig-<service> dnf -y install iproute procps-ng <packages>
```

For containers, replace `ssh dst "<cmd>"` below with `podman exec -i mig-<service> bash -c "<cmd>"`.

### Replay

1. **Packages first**, so users, groups, and policy exist before extraction: `ssh dst "dnf -y install <packages> audit policycoreutils-python-utils"`. Add EPEL/CRB if the source had them.
2. **Tar the manifest.** The exclude file is the hard boundary: `secret_files`, key paths, and credential-bearing files never enter the tar. Carry only config that `rpm -V` reports modified or that has `"package": null`, plus data paths. Unmodified package-owned files come from the destination's own package install; tarring them, for example an EL7 unit file, overwrites the destination's versions.

   ```bash
   D="$OUT/<host>-<svc>-discover.json"
   jq -r '.secret_files[], (.references.certs[] | select(.kind == "key") | .path), .references.credentials[].file' "$D" |
       sed 's#^/##' | sort -u > "$OUT/<svc>-exclude.txt"
   jq -r '(.package.modified[] | select(test("^missing") | not) | capture("^\\S+\\s+c\\s+(?<p>/.*)$")? | .p), (.config_files[] | select(.package == null) | .path), .data_paths[].path' "$D" |
       sed 's#^/##' | sort -u > "$OUT/<svc>-paths.txt"
   ```

   The `package.modified` filter keeps only config-file lines (`rpm -V` attribute column `c`) that are not `missing`. A `missing` entry makes `tar` exit 2, and a modified binary would overwrite the destination's own package-installed copy.

   **Preflight.** Every PEM private key under the paths must already be in the exclude file. Any output here means stop, add the path to the exclude file, and list it in plan section 5:

   ```bash
   ssh <host> "sudo grep -rlIs -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' <paths with leading />" |
       sed 's#^/##' | grep -vxFf "$OUT/<svc>-exclude.txt"
   ```

   Then tar, with the exclude file streamed to the remote tar's stdin. The exclude file matches whole member names literally (`--no-wildcards --anchored`); the globs after it match at any depth. Member names carry no leading `/` under `-C /`, which is why the exclude file has none.

   ```bash
   ssh <host> "sudo tar --selinux --acls --xattrs -cpf - -C / \
       --no-wildcards --anchored --exclude-from=/dev/stdin \
       --wildcards --no-anchored --exclude='*.key' --exclude='*.keys' --exclude='*.p12' --exclude='*.pfx' --exclude='*.private' \
       --exclude='etc/*/private/*' --exclude='*sasl_passwd*' --exclude='*htpasswd*' --exclude='*secret*' \
       <paths from <svc>-paths.txt, quoted>" < "$OUT/<svc>-exclude.txt" |
       ssh dst "tar --selinux --acls --xattrs -xpf - -C /"
   ```

   Credential-bearing files still arrive through step 3. Files in `secret_files` are never copied or shown; list them in plan section 5 for manual re-provisioning. Ask before tarring anything over 1 GB. For the dry run a representative subset is fine; note it in section 7.
3. **Credential files**, redacted on the source:

   ```bash
   bash "$SKILL_DIR/redact-expr.sh" "$OUT/<host>-<svc>-discover.json" <file> replay |
       ssh <host> "sudo sed -E -f /dev/stdin <file>" | ssh dst "cat > <file>"
   ssh dst "chown <owner> <file> && chmod <mode> <file> && grep -c CHANGEME-dryrun <file>"
   ```

   Take owner and mode from `config_files`. The grep count must equal the number of flagged lines for that file.
4. **Placeholder certs.** Generate one pair and put it at every `cert` and `key` path. `ca` paths keep the copied public file.

   ```bash
   ssh dst "openssl req -x509 -newkey rsa:2048 -nodes -days 7 -subj /CN=dryrun.invalid -keyout /root/dryrun.key -out /root/dryrun.crt"
   ssh dst "install -D -m 644 /root/dryrun.crt <cert path> && install -D -m 600 /root/dryrun.key <key path>"
   ```

   Match owners with `ssh <host> "sudo stat -c '%U:%G %a' <path>"`.
5. **SELinux baseline, from the section 4 recipe:**
   - `semanage fcontext` entries for mislabeled and custom paths
   - booleans and ports
   - then `restorecon -RF` on every replayed path

   Labels copied by `tar --selinux` are not trusted; restorecon always has the final say.
6. **Other scaffolding:** open only the firewalld ports the source had, and check sudoers with `visudo -c`.
7. **Rewrite source IPs** in listen directives to the destination's equivalents, and record each change.
8. **Config test and start.** Record the start time for the denial check, then run the service's own checker and start it:

   ```bash
   T=$(ssh dst date +%s)
   ssh dst "<apachectl -t | named-checkconf -z | postfix check | spamassassin --lint> && systemctl enable --now <service>"
   ```

   Containers have no systemd. Run the ExecStartPre steps by hand, start the daemon with `podman exec -d mig-<service> bash -c 'set -a; . <EnvironmentFile>; exec <ExecStart argv, foreground>'`, and get its PID with `podman exec mig-<service> pgrep -o -f <binary>`.

### Verify

```bash
ssh dst "bash -s -- <service> --listen '<entry>' ... --probe <dns|http|https|spamc|port|none> --watch 30 --since $T [--allow-egress <ip>|all]" \
    < "$SKILL_DIR/verify.sh"
# container
podman exec -i mig-<service> bash -s -- <service> --pid <pid> --listen "<entry>" ... < "$SKILL_DIR/verify.sh"
podman port mig-<service>
sudo ausearch --input-logs -m AVC -ts recent | grep container_t    # host side, containers only
```

- `--listen` entries come from the source's `network.listen`, with rewritten IPs substituted.
- `--allow-egress` lists only the public peers the source legitimately had. Use `all` only for a recursive resolver or an outbound relay that the plan justifies.
- `--since` takes epoch seconds and defaults to 10 minutes ago. `selinux_clean` counts every AVC denial since then, not only the service's domain; read the detail to tell service denials from unrelated ones.
- For containers, any `podman port` host binding on `0.0.0.0` or `::` counts as **bind drift** unless the source listened publicly.

### SELinux fix loop (dry-run target only)

When `selinux_clean` fails:

1. **Collect every denial in one pass instead of one at a time.** Run `semanage permissive -a <domain>`, exercise the service (probe, a real request, a cron run), and then run `ausearch --input-logs -m AVC,USER_AVC -ts today | audit2allow`. `today` parses in any locale, and the target is fresh. Always pass `--input-logs` over ssh: without a tty, ausearch reads stdin instead of the audit log and silently finds nothing.
2. **Read each denial before accepting a rule.** A label problem is fixed with `semanage fcontext` + `restorecon`. A missing permission may be covered by a boolean, which `audit2allow -a` suggests. Write a module only for what remains.
3. **For a module:** `ausearch --input-logs -m AVC,USER_AVC -ts today | audit2allow -M mig_<service>`, review `mig_<service>.te`, then `semodule -i mig_<service>.pp`.
4. **Re-run strict.** `semanage permissive -d <domain>`, restart the service, reset `T`, and run verify again. Repeat until it passes.
5. **Record the result.** Copy the final commands and the `.te` into section 4 "Dry-run fixes", with each allow rule commented with the denial it fixes.

Other failures: fix what the plan can fix, re-run, and record every fix. Append the verify JSON summary to section 7. Then tear down unless the user wants to keep it: `"$DO" --project Testing destroy mig-<service> --yes` or `podman rm -f mig-<service>`.

## Phase 4: Stop

Finish section 8, the human cutover runbook, with a rollback line per step. Include, where applicable:
- lowering DNS TTL ahead of time
- the stop-source gate before the final data sync
- the SELinux recipe applied before starting the service
- cert re-provisioning from section 5
- removal of the placeholder certs
- remove the migration-temp key and the `PermitRootLogin` override, then `sshd -t && systemctl reload sshd`

Hand the plan path to the user. Done.

## Gotchas

- **`tar --selinux` labels** only survive between enforcing hosts, and can carry a wrong label forward. Always `restorecon` after adding the fcontexts.
- **Container volumes** need `:Z` (private) or `:z` (shared) on SELinux hosts. Without it, the container gets host-side denials that never show up inside the container.
- **UIDs can differ between hosts.** Installing packages before extracting lets tar map owners by name. Flag to the user if an enterprise service or local accounts are not found for: custom systemd service with the User= field; or config files that reference specific UIDs or usernames.
- **ExecStartPre matters** in containers: key generation, config checks, and directory creation all have to be done by hand.
- **Configs bound to the old IP** fail to start on the destination. Check `network.listen` addresses in the configs through the safe view.
- **No MainPID:** socket-activated and oneshot services have none, so the listen and egress checks report "service has no MainPID". Verify those by hand with `ss -tulpn` and `ss -tupn state established`.
- **Host-side container AVCs** live in the host's audit log, so the `ausearch` there needs `sudo`.
- **Paths with spaces** must be quoted inside the remote command strings (`ssh <host> "sudo tar ... '/srv/my data'"`).
- **DO droplets:** for SSH failures on a fresh droplet, see the `digitalocean` skill gotchas. `base-rocky9` already runs a package upgrade first.

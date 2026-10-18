# Service Migration Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `sysadmin/service-migration/`, a skill that discovers a systemd service's full footprint (including SELinux), writes a condensed migration plan, and proves it with a dry-run replay into a droplet or podman container.

**Architecture:** Three bash scripts do the deterministic work:
- `discover.sh` gathers read-only facts as JSON.
- `verify.sh` runs post-replay checks as JSON, including an AVC denial check.
- `redact-expr.sh` builds sed scripts that blank credential values.

`SKILL.md` is the runbook for the judgment calls: recursion, the plan, exposure analysis, and the SELinux fix loop on the dry-run target. A lean test suite of about 23 assertions covers what live runs can't prove: no secret leaks, SELinux output parsing, and the drift, egress, and denial detection. Everything else gets live smoke runs.

**Tech Stack:** bash 4.4+, GNU coreutils/grep/sed, jq (local only), openssl, audit (`ausearch`), policycoreutils (`restorecon`, `audit2allow`), podman (optional), sibling `digitalocean` skill.

**Spec:** `docs/superpowers/specs/2026-09-24-service-migration-design.md`

## Global Constraints

- Remote scripts (`discover.sh`, `verify.sh`) must not depend on jq, python, or anything beyond bash, coreutils, grep/sed/awk, iproute (`ss`), procps (`ps`), and the optional SELinux/audit tools. Each one is guarded with `have`. They stream over `ssh host 'sudo bash -s -- args' < script`.
- Both remote scripts start with `set -uo pipefail` and `export LC_ALL=C`. They don't use `set -e`, because partial facts are better than an abort.
- Credential **values** and private key contents must never appear in any script output.
- The source host is read-only: no writes, no service control. `restorecon` is only ever run with `-n`.
- Style: 4-space indentation, short `#` comments, no mdash/ndash in code or comments. All scripts are executable.
- Test budget: roughly 23 assertions total. Don't add tests for parsers that the Task 7 live runs already cover.
- Runtime output goes to `./tmp/` when the CWD is inside this skills repo, and to `./migrations/` otherwise.

## File Map

```
sysadmin/service-migration/
  SKILL.md                # Task 6
  plan_template.md        # Task 6
  discover.sh             # Tasks 1-3
  verify.sh               # Task 4
  redact-expr.sh          # Task 5
  tests/
    run.sh  lib.sh
    test_discover.sh      # Tasks 1-3 (13 assertions)
    test_verify.sh        # Task 4 (6 assertions)
    test_redact.sh        # Task 5 (4 assertions)
    stubs/                # one-screen PATH shims
    fixtures/             # canned command output
      root/               # 8-file fake filesystem for SM_ROOT
```

---

### Task 1: Test harness, fixtures, discover core

**Files:**
- Create: `sysadmin/service-migration/tests/{run.sh,lib.sh,test_discover.sh}`
- Create: `sysadmin/service-migration/tests/stubs/{systemctl,rpm,ss,getenforce,semanage,semodule,restorecon,ausearch,firewall-cmd}`
- Create: `sysadmin/service-migration/tests/fixtures/*` and `tests/fixtures/root/**`
- Create: `sysadmin/service-migration/discover.sh`

**Interfaces:**
- Test lib produces: `run_discover <svc>`, `run_verify <svc> [opts]`, `assert_jq <json> <filter> <label>`, `assert_contains`, `assert_not_contains`, `assert_eq <want> <got> <label>`, `pass`, `fail`, `finish`, `TESTS_DIR`, `SKILL_DIR`.
- `discover.sh` globals that Tasks 2-3 rely on: `SVC`, `PKG`, `U` (assoc of `systemctl show` props), `CONFIG_FILES`, `DATA_PATHS` (arrays of unprefixed paths), `FILE_PKG`, `PKGS`, `SM_ROOT`.
- `discover.sh` helpers: `have`, `json_str`, `json_arr` (lines → JSON string array), `json_raw_arr` (JSON object lines → array), `strip_root`.
- JSON keys: `schema`, `service`, `host`, `os`, `collected_at`, `unit{}`, `package{name,version,modified[]}`, `config_files[{path,owner,mode,context,package}]`, `data_paths[{path,kind,size_kb}]`.

- [ ] **Step 1: Create the fixture filesystem (8 files)**

```bash
mkdir -p sysadmin/service-migration && cd sysadmin/service-migration
R=tests/fixtures/root
mkdir -p $R/etc/fakesvc/conf.d $R/usr/lib/systemd/system $R/etc/systemd/system/fakesvc.service.d \
    $R/etc/pki/tls/certs $R/etc/pki/tls/private $R/var/lib/fakesvc/store $R/opt/fakesvc-archive tests/stubs
```

`tests/fixtures/root/etc/fakesvc/fakesvc.conf`
```
# fakesvc main config
listen 127.0.0.1:8025
data_dir /var/lib/fakesvc/store
archive_dir /opt/fakesvc-archive
tls_cert_file /etc/pki/tls/certs/fakesvc.crt
tls_key_file /etc/pki/tls/private/fakesvc.key
db_password = hunter2-SUPERSECRET
upstream smtp://relayuser:relaypass@mail.example.net:587
backend unix:/run/spamd/spamd.sock
# old_password = commented-out-secret
```

`tests/fixtures/root/etc/fakesvc/conf.d/extra.conf`
```
# extra settings, not package-owned
api_token: tok-SUPERSECRET
secret "bind-SUPERSECRET";
cache redis.internal.example:6379
```

`tests/fixtures/root/usr/lib/systemd/system/fakesvc.service`
```
[Unit]
Description=Fake service for tests

[Service]
ExecStart=/usr/sbin/fakesvc -f
User=fakesvc
StateDirectory=fakesvc
```

`tests/fixtures/root/etc/systemd/system/fakesvc.service.d/override.conf`
```
[Service]
Environment=DB_PASSWORD=envsecret-SUPERSECRET
```

`tests/fixtures/root/etc/pki/tls/private/fakesvc.key`
```
NOT-A-REAL-KEY-SENTINEL
```

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout /dev/null \
    -out $R/etc/pki/tls/certs/fakesvc.crt -days 3650 -subj /CN=fakesvc.test
printf 'data\n' > $R/var/lib/fakesvc/store/data.db
touch $R/opt/fakesvc-archive/.keep
```

- [ ] **Step 2: Canned command output**

`tests/fixtures/systemctl-show-fakesvc.txt`
```
Id=fakesvc.service
LoadState=loaded
ActiveState=active
SubState=running
FragmentPath=/usr/lib/systemd/system/fakesvc.service
DropInPaths=/etc/systemd/system/fakesvc.service.d/override.conf
ExecStart={ path=/usr/sbin/fakesvc ; argv[]=/usr/sbin/fakesvc -f ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }
User=fakesvc
Group=fakesvc
WorkingDirectory=/
StateDirectory=fakesvc
Requires=
Wants=network-online.target
MainPID=4242
```

`tests/fixtures/rpm-owned.txt`
```
/usr/sbin/fakesvc
/usr/lib/systemd/system/fakesvc.service
/etc/fakesvc/fakesvc.conf
```

`tests/fixtures/rpm-V.txt`
```
S.5....T.  c /etc/fakesvc/fakesvc.conf
```

`tests/fixtures/ss-listen.txt`
```
tcp   LISTEN 0      128        127.0.0.1:8025      0.0.0.0:*    users:(("fakesvc",pid=4242,fd=5))
tcp   LISTEN 0      128          0.0.0.0:22        0.0.0.0:*    users:(("sshd",pid=900,fd=3))
```

`tests/fixtures/ss-estab.txt`
```
tcp   0      0      10.0.0.5:41234   203.0.113.9:587    users:(("fakesvc",pid=4242,fd=7))
tcp   0      0      127.0.0.1:8025   127.0.0.1:50000    users:(("fakesvc",pid=4242,fd=8))
tcp   0      0      10.0.0.5:22      198.51.100.1:5555  users:(("sshd",pid=900,fd=4))
```

`tests/fixtures/restorecon.txt` (EL9 wording on the first line, EL7/8 wording on the second)
```
Would relabel /var/lib/fakesvc/store/data.db from unconfined_u:object_r:default_t:s0 to system_u:object_r:fakesvc_var_lib_t:s0
restorecon reset /opt/fakesvc-archive context unconfined_u:object_r:usr_t:s0->system_u:object_r:fakesvc_archive_t:s0
```

`tests/fixtures/ausearch-avc.txt`
```
----
time->Thu Sep 24 10:00:00 2026
type=AVC msg=audit(1790244000.123:456): avc:  denied  { read } for  pid=4242 comm="fakesvc" name="data.db" dev="vda1" ino=1234 scontext=system_u:system_r:fakesvc_t:s0 tcontext=unconfined_u:object_r:default_t:s0 tclass=file permissive=0
```

- [ ] **Step 3: Stubs**

`tests/stubs/systemctl`
```bash
#!/usr/bin/env bash
# test stub: systemctl show <unit> and is-failed --quiet <unit>
case $1 in
    show)
        f="$STUB_FIXTURES/systemctl-show-${2%.service}.txt"
        if [[ -f $f ]]; then cat "$f"; else echo "LoadState=not-found"; fi
        ;;
    is-failed) [[ " ${STUB_FAILED:-} " == *" ${*: -1} "* ]] ;;
esac
```

`tests/stubs/rpm`
```bash
#!/usr/bin/env bash
# test stub: rpm -qf, -q, -V
case $1 in
    -qf)
        path=${*: -1}
        if grep -qxF "$path" "$STUB_FIXTURES/rpm-owned.txt"; then
            echo fakesvc
        else
            echo "file $path is not owned by any package"
            exit 1
        fi
        ;;
    -q) printf '1.2-3.el9' ;;
    -V) cat "$STUB_FIXTURES/rpm-V.txt" ;;
esac
```

`tests/stubs/ss`
```bash
#!/usr/bin/env bash
case " $* " in
    *" established "*) cat "$STUB_FIXTURES/ss-estab.txt" ;;
    *) cat "$STUB_FIXTURES/${STUB_SS_LISTEN:-ss-listen.txt}" ;;
esac
```

`tests/stubs/getenforce`
```bash
#!/usr/bin/env bash
echo Enforcing
```

`tests/stubs/semanage`
```bash
#!/usr/bin/env bash
# test stub: only booleans carry a local customization
if [[ "$1 $2" == "boolean -l" ]]; then
    printf 'SELinux boolean                State  Default Description\n\n'
    printf 'fakesvc_can_relay              (on   ,   on)  Allow fakesvc to relay\n'
else
    printf 'SELinux %s   type   Context\n\n' "$1"
fi
```

`tests/stubs/semodule`
```bash
#!/usr/bin/env bash
printf '400 fakesvc_local pp\n100 base         pp\n'
```

`tests/stubs/restorecon`
```bash
#!/usr/bin/env bash
cat "$STUB_FIXTURES/restorecon.txt"
```

`tests/stubs/ausearch`
```bash
#!/usr/bin/env bash
if [[ -n ${STUB_AVC:-} ]]; then cat "$STUB_FIXTURES/ausearch-avc.txt"; else echo '<no matches>'; exit 1; fi
```

`tests/stubs/firewall-cmd` (keeps tests from touching the dev box's real firewalld)
```bash
#!/usr/bin/env bash
[[ $1 == --state ]] && echo "not running"
exit 252
```

```bash
chmod +x tests/stubs/*
```

- [ ] **Step 4: Test lib and runner**

`tests/lib.sh`
```bash
#!/usr/bin/env bash
# assertion helpers and script runners for service-migration tests
TESTS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
FAILS=0

run_discover() {
    SM_ROOT="$TESTS_DIR/fixtures/root" STUB_FIXTURES="$TESTS_DIR/fixtures" \
        PATH="$TESTS_DIR/stubs:$PATH" bash "$SKILL_DIR/discover.sh" "$@"
}

run_verify() {
    STUB_FIXTURES="$TESTS_DIR/fixtures" PATH="$TESTS_DIR/stubs:$PATH" \
        bash "$SKILL_DIR/verify.sh" "$@"
}

pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }

assert_jq() { # <json> <jq filter that must yield true> <label>
    if jq -e "$2" >/dev/null 2>&1 <<<"$1"; then pass "$3"; else fail "$3  [filter: $2]"; fi
}

assert_contains() { # <haystack> <needle> <label>
    if [[ $1 == *"$2"* ]]; then pass "$3"; else fail "$3  [missing: $2]"; fi
}

assert_not_contains() { # <haystack> <needle> <label>
    if [[ $1 != *"$2"* ]]; then pass "$3"; else fail "$3  [found: $2]"; fi
}

assert_eq() { # <want> <got> <label>
    if [[ $1 == "$2" ]]; then pass "$3"; else fail "$3  [got '$2', want '$1']"; fi
}

finish() { exit $((FAILS > 0)); }
```

`tests/run.sh`
```bash
#!/usr/bin/env bash
# runs every test_*.sh; exit 1 if any fail
cd "$(dirname "$0")" || exit 1
rc=0
for t in test_*.sh; do
    printf '== %s\n' "$t"
    bash "$t" || rc=1
done
exit $rc
```

```bash
chmod +x tests/run.sh tests/lib.sh
```

- [ ] **Step 5: Write the failing test**

`tests/test_discover.sh`
```bash
#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"

out=$(run_discover fakesvc)
assert_jq "$out" '.service == "fakesvc" and .unit.User == "fakesvc" and .package.name == "fakesvc" and .package.version == "1.2-3.el9"' "unit and owning package"
assert_jq "$out" '.package.modified | any(test("/etc/fakesvc/fakesvc.conf"))' "rpm -V modifications"
assert_jq "$out" '[.config_files[] | {(.path): .package}] | add | .["/etc/fakesvc/fakesvc.conf"] == "fakesvc" and .["/etc/fakesvc/conf.d/extra.conf"] == null and has("/etc/systemd/system/fakesvc.service.d/override.conf")' "config files: owned, unowned, drop-in"
assert_jq "$out" '[.data_paths[].path] == ["/opt/fakesvc-archive", "/var/lib/fakesvc"]' "data paths: config-referenced, nested pruned"

missing=$(run_discover nosuchsvc); rc=$?
assert_jq "$missing" '.error == "unit not found"' "missing unit reports error"
assert_eq 2 "$rc" "missing unit exits 2"

finish
```

- [ ] **Step 6: Run it to confirm it fails**

Run: `bash tests/test_discover.sh`
Expected: `FAIL` lines, because `discover.sh` doesn't exist yet.

- [ ] **Step 7: Write `discover.sh` core**

```bash
#!/usr/bin/env bash
# discover.sh: read-only fact gatherer for one systemd service, prints JSON.
# usage: discover.sh <service>
# SM_ROOT: optional filesystem prefix, used by tests
set -uo pipefail
export LC_ALL=C

SM_ROOT="${SM_ROOT:-}"
SVC=""
PKG=""
declare -A U=()
declare -A FILE_PKG=()
declare -A PKGS=()
CONFIG_FILES=()
DATA_PATHS=()

UNIT_PROPS=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,DropInPaths,ExecStart,User,Group,EnvironmentFiles,WorkingDirectory,StateDirectory,ConfigurationDirectory,LogsDirectory,ReadWritePaths,After,Requires,Wants,MainPID

have() { command -v "$1" >/dev/null 2>&1; }

json_str() {
    local s=$1
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\t'/\\t}
    s=${s//$'\r'/\\r}
    s=${s//$'\n'/\\n}
    s=${s//[$'\x01'-$'\x1f']/}
    printf '"%s"' "$s"
}

json_arr() {
    local first=1 line
    printf '['
    while IFS= read -r line; do
        [[ -z $line ]] && continue
        ((first)) || printf ','
        first=0
        json_str "$line"
    done
    printf ']'
}

json_raw_arr() {
    local first=1 line
    printf '['
    while IFS= read -r line; do
        [[ -z $line ]] && continue
        ((first)) || printf ','
        first=0
        printf '%s' "$line"
    done
    printf ']'
}

strip_root() {
    local line
    while IFS= read -r line; do
        printf '%s\n' "${line#"$SM_ROOT"}"
    done
}

load_unit() {
    local line
    while IFS= read -r line; do
        [[ $line == *=* ]] && U[${line%%=*}]=${line#*=}
    done < <(systemctl show "$SVC.service" -p "$UNIT_PROPS" 2>/dev/null)
}

pkg_of() {
    local out
    out=$(rpm -qf --qf '%{NAME}\n' "$1" 2>/dev/null) || return 1
    printf '%s' "${out%%$'\n'*}"
}

find_package() {
    local exec_path f
    exec_path=$(sed -n 's/.*path=\([^ ;]*\).*/\1/p' <<<"${U[ExecStart]:-}" | head -n 1)
    for f in "$exec_path" "${U[FragmentPath]:-}"; do
        [[ -n $f ]] && PKG=$(pkg_of "$f") && return 0
    done
    PKG=""
}

config_candidates() {
    local d
    printf '%s\n' "/etc/$SVC" "/etc/$SVC.conf" "/etc/$SVC.d" "/etc/sysconfig/$SVC" "/etc/default/$SVC"
    [[ -n ${U[FragmentPath]:-} ]] && printf '%s\n' "${U[FragmentPath]}"
    for d in ${U[DropInPaths]:-}; do printf '%s\n' "$d"; done
    grep -oE '(^| )/[^ ]+' <<<"${U[EnvironmentFiles]:-}" | tr -d ' '
    for d in ${U[ConfigurationDirectory]:-}; do printf '/etc/%s\n' "$d"; done
    [[ -n $PKG ]] && rpm -qc "$PKG" 2>/dev/null
    return 0
}

collect_config_files() {
    local p f pkg
    mapfile -t CONFIG_FILES < <(
        config_candidates | sort -u | while IFS= read -r p; do
            [[ -n $p && -e $SM_ROOT$p ]] || continue
            find "$SM_ROOT$p" -xdev -type f 2>/dev/null
        done | strip_root | sort -u | head -n 2000
    )
    for f in "${CONFIG_FILES[@]}"; do
        if pkg=$(pkg_of "$f"); then
            FILE_PKG[$f]=$pkg
            PKGS[$pkg]=1
        fi
    done
    [[ -n $PKG ]] && PKGS[$PKG]=1
    return 0
}

data_candidates() {
    local d
    for d in ${U[StateDirectory]:-}; do printf '/var/lib/%s\n' "$d"; done
    for d in ${U[LogsDirectory]:-}; do printf '/var/log/%s\n' "$d"; done
    for d in ${U[ReadWritePaths]:-} ${U[WorkingDirectory]:-}; do printf '%s\n' "${d#[-!]}"; done
    printf '%s\n' "/var/lib/$SVC" "/var/spool/$SVC" "/var/log/$SVC" "/var/cache/$SVC" "/srv/$SVC" "/var/$SVC"
    if ((${#CONFIG_FILES[@]})); then
        grep -hoIE '/(var|srv|opt|home|data)/[^][:space:]"'"'"';,)<>{}*]+' \
            "${CONFIG_FILES[@]/#/$SM_ROOT}" 2>/dev/null
    fi
    return 0
}

# existing paths only; children of an already-kept path are dropped
collect_data_paths() {
    local p k
    while IFS= read -r p; do
        [[ -n $p && $p != / && -e $SM_ROOT$p ]] || continue
        for k in "${DATA_PATHS[@]}"; do
            [[ $p == "$k"/* ]] && continue 2
        done
        DATA_PATHS+=("$p")
    done < <(data_candidates | sed -E 's#/+$##' | sort -u)
}

emit_unit() {
    local k first=1
    printf '{'
    while IFS= read -r k; do
        [[ -n $k ]] || continue
        ((first)) || printf ','
        first=0
        printf '%s:%s' "$(json_str "$k")" "$(json_str "${U[$k]}")"
    done < <(printf '%s\n' "${!U[@]}" | sort)
    printf '}'
}

emit_package() {
    local version="" p
    [[ -n $PKG ]] && version=$(rpm -q --qf '%{VERSION}-%{RELEASE}' "$PKG" 2>/dev/null)
    printf '{"name":%s,"version":%s,"modified":%s}' \
        "$(json_str "$PKG")" "$(json_str "$version")" \
        "$(for p in "${!PKGS[@]}"; do rpm -V "$p" 2>/dev/null; done | sort -u | json_arr)"
}

emit_config_files() {
    local f meta ctx pkg_json
    for f in "${CONFIG_FILES[@]}"; do
        meta=$(stat -c '%U:%G %a' "$SM_ROOT$f" 2>/dev/null) || continue
        ctx=$(stat -c '%C' "$SM_ROOT$f" 2>/dev/null) || ctx=""
        pkg_json=null
        [[ -n ${FILE_PKG[$f]:-} ]] && pkg_json=$(json_str "${FILE_PKG[$f]}")
        printf '{"path":%s,"owner":%s,"mode":%s,"context":%s,"package":%s}\n' \
            "$(json_str "$f")" "$(json_str "${meta% *}")" "$(json_str "${meta##* }")" \
            "$(json_str "$ctx")" "$pkg_json"
    done | json_raw_arr
}

emit_data_paths() {
    local p kind size
    for p in "${DATA_PATHS[@]}"; do
        if [[ -d $SM_ROOT$p ]]; then kind=dir; else kind=file; fi
        size=$(timeout 20 du -sk "$SM_ROOT$p" 2>/dev/null | cut -f1)
        printf '{"path":%s,"kind":"%s","size_kb":%s}\n' "$(json_str "$p")" "$kind" "${size:-null}"
    done | json_raw_arr
}

main() {
    local os="" host
    SVC=${1:?usage: discover.sh <service>}
    SVC=${SVC%.service}
    load_unit
    if [[ ${U[LoadState]:-not-found} == not-found ]]; then
        printf '{"schema":1,"service":%s,"error":"unit not found"}\n' "$(json_str "$SVC")"
        exit 2
    fi
    find_package
    collect_config_files
    collect_data_paths
    [[ -r $SM_ROOT/etc/os-release ]] &&
        os=$(sed -n 's/^PRETTY_NAME="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$SM_ROOT/etc/os-release")
    host=$(hostname -f 2>/dev/null || cat /proc/sys/kernel/hostname)
    printf '{"schema":1,"service":%s,"host":%s,"os":%s,"collected_at":%s' \
        "$(json_str "$SVC")" "$(json_str "$host")" "$(json_str "$os")" \
        "$(json_str "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
    printf ',"unit":%s' "$(emit_unit)"
    printf ',"package":%s' "$(emit_package)"
    printf ',"config_files":%s' "$(emit_config_files)"
    printf ',"data_paths":%s' "$(emit_data_paths)"
    printf '}\n'
}

[[ -n ${SM_LIB_ONLY:-} ]] || main "$@"
```

```bash
chmod +x discover.sh
```

- [ ] **Step 8: Run to confirm it passes**

Run: `bash tests/test_discover.sh`
Expected: 6 `ok` lines and exit code 0.

- [ ] **Step 9: Commit**

```bash
git add sysadmin/service-migration/discover.sh sysadmin/service-migration/tests
git commit -m "service-migration: discover.sh core facts with lean fixture harness"
```

---

### Task 2: discover references (certs, credential keys, service links)

This task is security-critical. Review it for any path by which a credential value or key material could reach stdout.

**Files:**
- Modify: `sysadmin/service-migration/discover.sh`: add the functions above `main()`, and one `printf` line in `main()`
- Modify: `sysadmin/service-migration/tests/test_discover.sh`: insert assertions before `finish`

**Interfaces:**
- Consumes `CONFIG_FILES`, `SM_ROOT`, and the JSON helpers from Task 1.
- Produces `references{certs[{file,line,key,path,kind,exists,expires}], credentials[{file,line,key}], services[]}`.
  - `kind` is one of `cert|key|ca`.
  - `credentials[].key` is a config key name, the literal `url-userinfo`, or `(unparsed)`. Task 5 depends on these values.

- [ ] **Step 1: Add the failing assertions** (insert before `finish` in `tests/test_discover.sh`)

```bash
assert_jq "$out" '(.references.certs[] | select(.key == "tls_cert_file") | .kind == "cert" and (.expires | length > 0)) and (.references.certs[] | select(.key == "tls_key_file") | .kind == "key" and .expires == "")' "certs: expiry read, key file never parsed"
assert_jq "$out" '[.references.credentials[].key] | sort == ["DB_PASSWORD","api_token","db_password","secret","url-userinfo"]' "credential key names only, comments skipped"
assert_jq "$out" '.references.services | contains(["unix:/run/spamd/spamd.sock","smtp://mail.example.net:587","redis.internal.example:6379"])' "service refs, url userinfo stripped"
leaks=""
for s in hunter2 SUPERSECRET relaypass relayuser NOT-A-REAL-KEY-SENTINEL commented-out-secret; do
    [[ $out == *"$s"* ]] && leaks+=" $s"
done
assert_eq "" "$leaks" "no secret values anywhere in output"
```

- [ ] **Step 2: Run to confirm the new assertions fail**

Run: `bash tests/test_discover.sh`
Expected: the three `references` assertions `FAIL`. The leak check already passes.

- [ ] **Step 3: Implement** (add above `main()`)

```bash
COMMENT_RE='^[[:space:]]*(#|;|//)'
CERT_RE='\.(crt|pem|cer|key|p12|pfx)([[:space:]";,]|$)|(ssl|tls)[_-]?[a-z_]*(cert|key|ca)|sslcertificate|sslcacertificate'
KW_RE='[a-z0-9_.-]*(pass(word|wd|phrase)?|secret|token|api[_-]?key|credentials?)[a-z0-9_.-]*'
CRED_RE="$KW_RE"'[[:space:]]*([=:]|[[:space:]]["'"'"'a-z0-9])|://[^/@[:space:]]+:[^/@[:space:]]+@'
URL_RE='[a-z][a-z0-9+.-]*://[^][:space:]"'"'"';,<>]+'
SOCK_RE='unix:[^][:space:]"'"'"';,]+|/[^][:space:]"'"'"';,]+\.sock(et)?\b'
HOSTPORT_RE='\b(localhost|([a-z0-9-]+\.)+[a-z0-9-]+|\[[0-9a-f:]+\]):[0-9]{2,5}\b'
CA_KEY_RE='(^|[_-])ca([_-]|$)|cacert|chain'

first_token() {
    local l=$1
    l=${l#"${l%%[![:space:]]*}"}
    l=${l%%[[:space:]=:]*}
    printf '%s' "${l:0:64}"
}

# key name only; url-userinfo or (unparsed) when the key cannot be isolated safely
cred_key() {
    local line=$1 tok out="(unparsed)"
    shopt -s nocasematch
    if [[ $line =~ ://[^/@[:space:]]+:[^/@[:space:]]+@ ]]; then
        out="url-userinfo"
    else
        tok=$(first_token "$line")
        if [[ $tok =~ ^$KW_RE$ ]]; then
            out=$tok
        elif [[ $line =~ ^[[:space:]]*(environment|export|setenv|set|env)[[:space:]=]+($KW_RE)[[:space:]]*[=:] ]]; then
            out=${BASH_REMATCH[2]}
        fi
    fi
    shopt -u nocasematch
    printf '%s' "${out:0:64}"
}

emit_certs() {
    local f n content key lkey path kind exists expires
    ((${#CONFIG_FILES[@]})) || { printf '[]'; return; }
    while IFS=: read -r f n content; do
        grep -qE "$COMMENT_RE" <<<"$content" && continue
        key=$(first_token "$content")
        lkey=${key,,}
        path=$(grep -oE '/[^][:space:]"'"'"';,]+\.(crt|pem|cer|key|p12|pfx)' <<<"$content" | head -n 1)
        kind=cert
        if [[ $path == *.key || $path == *.p12 || $path == *.pfx || $lkey == *key* ]]; then
            kind=key
        elif [[ $lkey =~ $CA_KEY_RE ]]; then
            kind=ca
        fi
        exists=false
        expires=""
        if [[ -n $path && -e $SM_ROOT$path ]]; then
            exists=true
            # key files are never opened; certs only via x509 -enddate
            if [[ $kind != key ]] && have openssl; then
                expires=$(openssl x509 -noout -enddate -in "$SM_ROOT$path" 2>/dev/null | cut -d= -f2)
            fi
        fi
        printf '{"file":%s,"line":%s,"key":%s,"path":%s,"kind":"%s","exists":%s,"expires":%s}\n' \
            "$(json_str "$f")" "$n" "$(json_str "$key")" "$(json_str "$path")" "$kind" "$exists" "$(json_str "$expires")"
    done < <(grep -nHIiE "$CERT_RE" "${CONFIG_FILES[@]/#/$SM_ROOT}" 2>/dev/null | strip_root) | json_raw_arr
}

emit_credentials() {
    local f n content
    ((${#CONFIG_FILES[@]})) || { printf '[]'; return; }
    while IFS=: read -r f n content; do
        grep -qE "$COMMENT_RE" <<<"$content" && continue
        printf '{"file":%s,"line":%s,"key":%s}\n' "$(json_str "$f")" "$n" "$(json_str "$(cred_key "$content")")"
    done < <(grep -nHIiE "$CRED_RE" "${CONFIG_FILES[@]/#/$SM_ROOT}" 2>/dev/null | strip_root) | json_raw_arr
}

# urls come from every line with userinfo and query stripped; sockets and host:port skip credential lines
emit_service_refs() {
    ((${#CONFIG_FILES[@]})) || { printf '[]'; return; }
    local files=("${CONFIG_FILES[@]/#/$SM_ROOT}")
    {
        grep -hvIE "$COMMENT_RE" "${files[@]}" 2>/dev/null | grep -oiE "$URL_RE" |
            sed -E 's#(://)[^/@]*@#\1#; s/[?#].*//'
        grep -hvIE "$COMMENT_RE" "${files[@]}" 2>/dev/null | grep -viE "$CRED_RE" |
            grep -oiE "$SOCK_RE|$HOSTPORT_RE"
    } | sort -u | json_arr
}
```

In `main()`, insert immediately before `printf '}\n'`:

```bash
    printf ',"references":{"certs":%s,"credentials":%s,"services":%s}' \
        "$(emit_certs)" "$(emit_credentials)" "$(emit_service_refs)"
```

- [ ] **Step 4: Run to confirm it passes**

Run: `bash tests/test_discover.sh`
Expected: 10 `ok` lines and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add sysadmin/service-migration/discover.sh sysadmin/service-migration/tests/test_discover.sh
git commit -m "service-migration: discover cert, credential-key, and service references without values"
```

---

### Task 3: discover host facts (network, SELinux with mislabels, firewall, scaffolding)

**Files:**
- Modify: `sysadmin/service-migration/discover.sh`: add the functions above `main()`, and four `printf` lines in `main()`
- Modify: `sysadmin/service-migration/tests/test_discover.sh`: insert assertions before `finish`

**Interfaces:**
- Consumes `U`, `SVC`, `DATA_PATHS`, `SM_ROOT`, and the helpers.
- Produces:
  - `network{running,listen[],outbound[]}`, with entries like `"tcp 127.0.0.1:8025"`. Task 6 passes listen entries verbatim to `verify.sh --listen`.
  - `selinux{mode,booleans[],fcontexts[],ports[],modules[],mislabeled[{path,current,expected}]}`
  - `firewall` (string)
  - `scaffolding{cron[],systemd_units[],logrotate[],sudoers[]}`
- Scaffolding and firewall are covered by the Task 7 live runs, not by unit tests.

- [ ] **Step 1: Add the failing assertions** (insert before `finish`)

```bash
assert_jq "$out" '.network.listen == ["tcp 127.0.0.1:8025"] and .network.outbound == ["tcp 203.0.113.9:587"]' "network: own sockets only, outbound excludes inbound"
assert_jq "$out" '.selinux.mode == "Enforcing" and .selinux.modules == ["fakesvc_local"] and (.selinux.booleans | length == 1) and .selinux.fcontexts == []' "selinux: local customizations, header lines filtered"
assert_jq "$out" '.selinux.mislabeled == [
    {"path": "/var/lib/fakesvc/store/data.db", "current": "unconfined_u:object_r:default_t:s0", "expected": "system_u:object_r:fakesvc_var_lib_t:s0"},
    {"path": "/opt/fakesvc-archive", "current": "unconfined_u:object_r:usr_t:s0", "expected": "system_u:object_r:fakesvc_archive_t:s0"}]' "selinux: restorecon mislabels, EL9 and EL7/8 formats"
```

- [ ] **Step 2: Run to confirm the new assertions fail**

Run: `bash tests/test_discover.sh`
Expected: the 3 new assertions `FAIL`.

- [ ] **Step 3: Implement** (add above `main()`)

```bash
svc_pid_pattern() {
    local pid=${U[MainPID]:-0} p pat
    [[ $pid =~ ^[0-9]+$ && $pid != 0 ]] || return 1
    pat="pid=$pid,"
    for p in $(ps -o pid= --ppid "$pid" 2>/dev/null); do pat+="|pid=$p,"; done
    printf '%s' "$pat"
}

emit_network() {
    local pat listen ports outbound
    if ! pat=$(svc_pid_pattern); then
        printf '{"running":false,"listen":[],"outbound":[]}'
        return
    fi
    listen=$(ss -H -tulpn 2>/dev/null | grep -E "$pat" | awk '{print $1" "$5}' | sort -u)
    ports=$(awk '{n = split($2, a, ":"); print a[n]}' <<<"$listen")
    outbound=$(ss -H -tupn state established 2>/dev/null | grep -E "$pat" | awk '{print $1" "$4" "$5}' |
        while read -r proto local peer; do
            grep -qxF "${local##*:}" <<<"$ports" || printf '%s %s\n' "$proto" "$peer"
        done | sort -u)
    printf '{"running":true,"listen":%s,"outbound":%s}' "$(json_arr <<<"$listen")" "$(json_arr <<<"$outbound")"
}

# restorecon -n is read-only; handles EL9 "Would relabel" and EL7/8 "reset" wording
emit_mislabeled() {
    local line p targets=("${DATA_PATHS[@]}")
    local re_new='^Would relabel (.+) from ([^ ]+) to ([^ ]+)$'
    local re_old='reset (.+) context ([^ ]+)->([^ ]+)$'
    [[ -d $SM_ROOT/etc/$SVC ]] && targets+=("/etc/$SVC")
    if ! have restorecon || ((${#targets[@]} == 0)); then
        printf '[]'
        return
    fi
    restorecon -Rnv "${targets[@]/#/$SM_ROOT}" 2>/dev/null | head -n 200 | while IFS= read -r line; do
        [[ $line =~ $re_new || $line =~ $re_old ]] || continue
        p=${BASH_REMATCH[1]}
        printf '{"path":%s,"current":%s,"expected":%s}\n' \
            "$(json_str "${p#"$SM_ROOT"}")" "$(json_str "${BASH_REMATCH[2]}")" "$(json_str "${BASH_REMATCH[3]}")"
    done | json_raw_arr
}

emit_selinux() {
    local mode=Disabled
    have getenforce && mode=$(getenforce 2>/dev/null)
    printf '{"mode":%s' "$(json_str "$mode")"
    printf ',"booleans":%s' "$({ have semanage && semanage boolean -l -C; } 2>/dev/null | grep -vE '^(SELinux|$)' | json_arr)"
    printf ',"fcontexts":%s' "$({ have semanage && semanage fcontext -l -C; } 2>/dev/null | grep -vE '^(SELinux|$)' | json_arr)"
    printf ',"ports":%s' "$({ have semanage && semanage port -l -C; } 2>/dev/null | grep -vE '^(SELinux|$)' | json_arr)"
    printf ',"modules":%s' "$({ have semodule && semodule --list-modules=full; } 2>/dev/null | awk '$1 == 400 {print $2}' | json_arr)"
    if [[ $mode == Disabled ]]; then
        printf ',"mislabeled":[]}'
    else
        printf ',"mislabeled":%s}' "$(emit_mislabeled)"
    fi
}

emit_firewall() {
    local z out=""
    if have firewall-cmd && [[ $(firewall-cmd --state 2>/dev/null) == running ]]; then
        for z in $(firewall-cmd --get-active-zones 2>/dev/null | awk '/^[^[:space:]]/ {print $1}'); do
            out+=$(firewall-cmd --zone="$z" --list-all 2>/dev/null)$'\n'
        done
    fi
    json_str "$out"
}

# files under the given roots that mention the service name or its run user as a word
scaffold_grep() {
    local t args=(-e "$SVC")
    [[ -n ${U[User]:-} && ${U[User]} != root ]] && args+=(-e "${U[User]}")
    for t in "$@"; do
        [[ -e $SM_ROOT$t ]] && grep -rlIwF "${args[@]}" "$SM_ROOT$t" 2>/dev/null
    done | strip_root | sort -u
    return 0
}

emit_scaffolding() {
    local units
    units=$(scaffold_grep /etc/systemd/system /usr/lib/systemd/system |
        grep -E '\.(timer|service|path|socket)$' |
        grep -vxF "${U[FragmentPath]:-}" |
        grep -vF "/etc/systemd/system/$SVC.service.d/")
    printf '{"cron":%s' "$(scaffold_grep /etc/crontab /etc/cron.d /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /var/spool/cron | json_arr)"
    printf ',"systemd_units":%s' "$(json_arr <<<"$units")"
    printf ',"logrotate":%s' "$(scaffold_grep /etc/logrotate.d /etc/logrotate.conf | json_arr)"
    printf ',"sudoers":%s' "$(scaffold_grep /etc/sudoers /etc/sudoers.d | json_arr)"
    printf '}'
}
```

In `main()`, insert immediately before `printf '}\n'`:

```bash
    printf ',"network":%s' "$(emit_network)"
    printf ',"selinux":%s' "$(emit_selinux)"
    printf ',"firewall":%s' "$(emit_firewall)"
    printf ',"scaffolding":%s' "$(emit_scaffolding)"
```

- [ ] **Step 4: Run to confirm it passes**

Run: `bash tests/test_discover.sh`
Expected: 13 `ok` lines and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add sysadmin/service-migration/discover.sh sysadmin/service-migration/tests/test_discover.sh
git commit -m "service-migration: discover network, SELinux mislabels and customizations, firewall, scaffolding"
```

---

### Task 4: verify.sh (with the selinux_clean check)

**Files:**
- Create: `sysadmin/service-migration/verify.sh`
- Create: `sysadmin/service-migration/tests/test_verify.sh`
- Create: `sysadmin/service-migration/tests/fixtures/ss-listen-drift.txt`

**Interfaces:**
- CLI: `verify.sh <service> [--listen "tcp ADDR:PORT"]... [--allow-egress IP|all]... [--probe dns|http|https|spamc|port|none] [--watch SECONDS] [--since AUSEARCH_TS] [--pid PID]`
  - Defaults: `--probe port`, `--watch 30`, `--since recent`.
  - `--since` is passed to `ausearch -ts` unquoted, so `"09/24/2026 10:00:00"` becomes two words.
  - `--pid` means container mode: it skips the systemd and SELinux checks, which belong to the host.
- Output: `{"service":..,"pass":bool,"checks":[{"name":..,"pass":bool,"detail":..}]}`. There are six checks, in this order: `unit_active`, `deps_ok`, `listen_match`, `egress_ok`, `probe`, `selinux_clean`. `selinux_clean` runs last, so the probe's traffic has a chance to trigger denials first.
- Exit: 0 if everything passes, 1 if any check fails, 64 on a usage error.
- Library mode (`SM_LIB_ONLY=1`) exposes `is_private_ip <addr>`.
- The network sampling logic intentionally duplicates `discover.sh`, because each script streams to remote hosts on its own.

- [ ] **Step 1: Drift fixture**

`tests/fixtures/ss-listen-drift.txt`
```
tcp   LISTEN 0      128          0.0.0.0:8025      0.0.0.0:*    users:(("fakesvc",pid=4242,fd=5))
```

- [ ] **Step 2: Write the failing test**

`tests/test_verify.sh`
```bash
#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"

base=(fakesvc --listen "tcp 127.0.0.1:8025" --probe none --watch 0)

out=$(run_verify "${base[@]}" --allow-egress 203.0.113.9); rc=$?
assert_jq "$out" '.pass and (.checks | length == 6)' "clean run passes all six checks"
assert_eq 0 "$rc" "clean run exits 0"

out=$(run_verify "${base[@]}")
assert_jq "$out" '.checks[] | select(.name == "egress_ok") | .pass == false and (.detail | test("203.0.113.9:587"))' "unexpected public egress fails"

out=$(STUB_SS_LISTEN=ss-listen-drift.txt run_verify "${base[@]}" --allow-egress all)
assert_jq "$out" '.checks[] | select(.name == "listen_match") | .pass == false and (.detail | test("BIND DRIFT"))' "wildcard bind flagged"

out=$(STUB_AVC=1 run_verify "${base[@]}" --allow-egress all)
assert_jq "$out" '.checks[] | select(.name == "selinux_clean") | .pass == false and (.detail | test("default_t"))' "AVC denial fails the run"

export SM_LIB_ONLY=1
source "$SKILL_DIR/verify.sh"
wrong=""
for ip in 10.1.2.3 172.20.0.1 fd00::5 ::ffff:10.0.0.1; do is_private_ip "$ip" || wrong+=" $ip"; done
for ip in 8.8.8.8 172.32.0.1 203.0.113.9 2001:4860::8888; do is_private_ip "$ip" && wrong+=" $ip"; done
assert_eq "" "$wrong" "private/public address classification"
finish
```

- [ ] **Step 3: Run to confirm it fails**

Run: `bash tests/test_verify.sh`
Expected: `FAIL` lines, because `verify.sh` doesn't exist yet.

- [ ] **Step 4: Implement `verify.sh`**

```bash
#!/usr/bin/env bash
# verify.sh: post-replay checks for a migrated service on the dry-run target, prints JSON.
# usage: verify.sh <service> [--listen "tcp ADDR:PORT"]... [--allow-egress IP|all]...
#          [--probe dns|http|https|spamc|port|none] [--watch SECONDS] [--since AUSEARCH_TS] [--pid PID]
set -uo pipefail
export LC_ALL=C

SVC=""
EXPECT_LISTEN=()
ALLOW_EGRESS=()
PROBE=port
WATCH=30
SINCE=recent
PID_OVERRIDE=""
CHECKS=()
declare -A U=()

have() { command -v "$1" >/dev/null 2>&1; }

json_str() {
    local s=$1
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\t'/\\t}
    s=${s//$'\r'/\\r}
    s=${s//$'\n'/\\n}
    s=${s//[$'\x01'-$'\x1f']/}
    printf '"%s"' "$s"
}

add_check() { # <name> <true|false> <detail>
    CHECKS+=("$(printf '{"name":"%s","pass":%s,"detail":%s}' "$1" "$2" "$(json_str "$3")")")
}

is_private_ip() {
    local ip=${1//[\[\]]/}
    ip=${ip#::ffff:}
    if [[ $ip =~ ^([0-9]+)\.([0-9]+)\.[0-9]+\.[0-9]+$ ]]; then
        local a=${BASH_REMATCH[1]} b=${BASH_REMATCH[2]}
        ((a == 10 || a == 127 || a == 0 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168) ||
            (a == 169 && b == 254) || (a == 100 && b >= 64 && b <= 127)))
        return
    fi
    ip=${ip,,}
    [[ $ip == ::1 || $ip == fe[89ab]* || $ip == f[cd]* ]]
}

load_unit() {
    local line
    if [[ -n $PID_OVERRIDE ]]; then
        U[MainPID]=$PID_OVERRIDE
        if [[ -d /proc/$PID_OVERRIDE ]]; then U[ActiveState]=active; else U[ActiveState]=dead; fi
        U[SubState]="pid $PID_OVERRIDE, no systemd"
        return
    fi
    while IFS= read -r line; do
        [[ $line == *=* ]] && U[${line%%=*}]=${line#*=}
    done < <(systemctl show "$SVC.service" -p ActiveState,SubState,MainPID,Requires,Wants 2>/dev/null)
}

pid_pattern() {
    local pid=${U[MainPID]:-0} p pat
    [[ $pid =~ ^[0-9]+$ && $pid != 0 ]] || return 1
    pat="pid=$pid,"
    for p in $(ps -o pid= --ppid "$pid" 2>/dev/null); do pat+="|pid=$p,"; done
    printf '%s' "$pat"
}

sample_listen() {
    ss -H -tulpn 2>/dev/null | grep -E "$1" | awk '{print $1" "$5}' | sort -u
}

sample_outbound() { # <pid pattern> <listen ports, one per line>
    ss -H -tupn state established 2>/dev/null | grep -E "$1" | awk '{print $1" "$4" "$5}' |
        while read -r proto local peer; do
            grep -qxF "${local##*:}" <<<"$2" || printf '%s %s\n' "$proto" "$peer"
        done
}

check_active() {
    if [[ ${U[ActiveState]:-} == active ]]; then
        add_check unit_active true "active (${U[SubState]:-})"
    else
        add_check unit_active false "${U[ActiveState]:-unknown} (${U[SubState]:-})"
    fi
}

check_deps() {
    local d failed=()
    if [[ -n $PID_OVERRIDE ]]; then
        add_check deps_ok true "skipped: no systemd"
        return
    fi
    for d in ${U[Requires]:-} ${U[Wants]:-}; do
        systemctl is-failed --quiet "$d" 2>/dev/null && failed+=("$d")
    done
    if ((${#failed[@]})); then
        add_check deps_ok false "failed: ${failed[*]}"
    else
        add_check deps_ok true "no failed dependencies"
    fi
}

check_listen() { # <actual listen lines>
    local want line detail missing=() extra=()
    for want in "${EXPECT_LISTEN[@]}"; do
        grep -qxF "$want" <<<"$1" || missing+=("$want")
    done
    while IFS= read -r line; do
        [[ -z $line ]] && continue
        printf '%s\n' "${EXPECT_LISTEN[@]}" | grep -qxF "$line" || extra+=("$line")
    done <<<"$1"
    if ((${#missing[@]} + ${#extra[@]} == 0)); then
        add_check listen_match true "$(tr '\n' ' ' <<<"$1")"
        return
    fi
    detail="missing: ${missing[*]:-none}; extra: ${extra[*]:-none}"
    for line in "${extra[@]}"; do
        if [[ $line == *" 0.0.0.0:"* || $line == *" *:"* || $line == *" [::]:"* ]]; then
            detail+="; BIND DRIFT: wildcard bind $line"
            break
        fi
    done
    add_check listen_match false "$detail"
}

check_egress() { # <pid pattern> <listen ports>
    local i peer addr seen="" bad=() rounds=$((WATCH / 2))
    ((rounds < 1)) && rounds=1
    for ((i = 0; i < rounds; i++)); do
        seen+=$(sample_outbound "$1" "$2")$'\n'
        ((i + 1 < rounds)) && sleep 2
    done
    if printf '%s\n' "${ALLOW_EGRESS[@]}" | grep -qxF all; then
        add_check egress_ok true "egress allowed: all"
        return
    fi
    while read -r _ peer; do
        [[ -z $peer ]] && continue
        addr=${peer%:*}
        addr=${addr//[\[\]]/}
        is_private_ip "$addr" && continue
        printf '%s\n' "${ALLOW_EGRESS[@]}" | grep -qxF "$addr" && continue
        bad+=("$peer")
    done < <(sort -u <<<"$seen")
    if ((${#bad[@]})); then
        add_check egress_ok false "UNEXPECTED PUBLIC EGRESS: ${bad[*]}"
    else
        add_check egress_ok true "no unexpected public egress in ${WATCH}s"
    fi
}

probe_target() { # prints "addr port" for the first expected listen socket
    local first=${EXPECT_LISTEN[0]:-} hostport addr
    [[ -n $first ]] || return 1
    hostport=${first#* }
    addr=${hostport%:*}
    addr=${addr//[\[\]]/}
    [[ $addr == 0.0.0.0 || $addr == '*' || $addr == :: ]] && addr=127.0.0.1
    printf '%s %s' "$addr" "${hostport##*:}"
}

port_open() { timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

check_probe() {
    local target addr port ok=false detail=""
    if [[ $PROBE == none ]]; then
        add_check probe true "skipped"
        return
    fi
    if ! target=$(probe_target); then
        add_check probe false "no --listen given to probe"
        return
    fi
    read -r addr port <<<"$target"
    case $PROBE in
        dns)
            if have dig; then
                dig +time=2 +tries=1 -p "$port" @"$addr" . NS >/dev/null 2>&1 && ok=true
                detail="dig @$addr -p $port"
            else detail="dig not installed"; PROBE=port; fi ;;
        http | https)
            if have curl; then
                curl -skI -m 5 "$PROBE://$addr:$port/" >/dev/null 2>&1 && ok=true
                detail="curl $PROBE://$addr:$port/"
            else detail="curl not installed"; PROBE=port; fi ;;
        spamc)
            if have spamc; then
                spamc -d "$addr" -p "$port" -K >/dev/null 2>&1 && ok=true
                detail="spamc -K $addr:$port"
            else detail="spamc not installed"; PROBE=port; fi ;;
    esac
    if [[ $PROBE == port ]]; then
        port_open "$addr" "$port" && ok=true
        detail="tcp connect $addr:$port${detail:+ (fallback: $detail)}"
    fi
    add_check probe "$ok" "$detail"
}

# denials are filtered to the service's domain when its pid label is readable
check_selinux() {
    local mode domain denials
    if [[ -n $PID_OVERRIDE ]]; then
        add_check selinux_clean true "skipped in container: run 'ausearch -m AVC -ts recent' on the host, look for container_t"
        return
    fi
    mode=$(getenforce 2>/dev/null) || mode=Disabled
    if [[ $mode == Disabled ]]; then
        add_check selinux_clean true "selinux disabled"
        return
    fi
    if ! have ausearch; then
        add_check selinux_clean false "ausearch missing: dnf install audit"
        return
    fi
    domain=$(ps -o label= -p "${U[MainPID]:-0}" 2>/dev/null | cut -d: -f3)
    # shellcheck disable=SC2086 # SINCE may be two words: date and time
    denials=$(ausearch -m AVC,USER_AVC -ts $SINCE 2>/dev/null | grep -E 'avc: +denied' |
        { if [[ -n $domain ]]; then grep -F ":$domain:"; else cat; fi; } |
        grep -oE 'denied +\{[^}]*\}.*tclass=[a-z_]+' | sort -u | head -n 5)
    if [[ -n $denials ]]; then
        add_check selinux_clean false "AVC denials ($mode${domain:+, $domain}): $(tr '\n' '|' <<<"$denials")"
    else
        add_check selinux_clean true "no AVC denials since $SINCE ($mode)"
    fi
}

main() {
    local pat listen ports all=true c
    SVC=${1:?usage: verify.sh <service> [options]}
    SVC=${SVC%.service}
    shift
    while (($#)); do
        case $1 in
            --listen) EXPECT_LISTEN+=("$2"); shift 2 ;;
            --allow-egress) ALLOW_EGRESS+=("$2"); shift 2 ;;
            --probe) PROBE=$2; shift 2 ;;
            --watch) WATCH=$2; shift 2 ;;
            --since) SINCE=$2; shift 2 ;;
            --pid) PID_OVERRIDE=$2; shift 2 ;;
            *) printf 'verify.sh: unknown option %s\n' "$1" >&2; exit 64 ;;
        esac
    done
    case $PROBE in
        dns | http | https | spamc | port | none) ;;
        *) printf 'verify.sh: bad --probe %s\n' "$PROBE" >&2; exit 64 ;;
    esac
    load_unit
    check_active
    check_deps
    if pat=$(pid_pattern); then
        listen=$(sample_listen "$pat")
        ports=$(awk '{n = split($2, a, ":"); print a[n]}' <<<"$listen")
        check_listen "$listen"
        check_egress "$pat" "$ports"
    else
        add_check listen_match false "service has no MainPID"
        add_check egress_ok false "service has no MainPID"
    fi
    check_probe
    check_selinux
    for c in "${CHECKS[@]}"; do
        [[ $c == *'"pass":false'* ]] && all=false
    done
    printf '{"service":%s,"pass":%s,"checks":[%s]}\n' "$(json_str "$SVC")" "$all" "$(IFS=,; printf '%s' "${CHECKS[*]}")"
    [[ $all == true ]]
}

[[ -n ${SM_LIB_ONLY:-} ]] || main "$@"
```

```bash
chmod +x verify.sh
```

- [ ] **Step 5: Run all tests**

Run: `bash tests/run.sh`
Expected: every line `ok` and exit code 0.

- [ ] **Step 6: Commit**

```bash
git add sysadmin/service-migration/verify.sh sysadmin/service-migration/tests/test_verify.sh sysadmin/service-migration/tests/fixtures/ss-listen-drift.txt
git commit -m "service-migration: verify.sh with bind-drift, egress, and SELinux denial checks"
```

---

### Task 5: redact-expr.sh

**Files:**
- Create: `sysadmin/service-migration/redact-expr.sh`
- Create: `sysadmin/service-migration/tests/test_redact.sh`

**Interfaces:**
- CLI: `redact-expr.sh <discover.json> <file> [view|replay]`. It prints a GNU `sed -E` script, which the caller runs **on the source**: `sudo sed -E -f /dev/stdin <file>`.
  - `view` → `<REDACTED>`
  - `replay` → `CHANGEME-dryrun`
- Consumes `references.credentials[{file,line,key}]` from Task 2, including the literals `url-userinfo` and `(unparsed)`. Runs locally only, so jq is allowed.

- [ ] **Step 1: Write the failing test**

`tests/test_redact.sh`
```bash
#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
run_discover fakesvc > "$tmp/d.json"
root="$TESTS_DIR/fixtures/root"

redacted() { # <file> <mode>
    sed -E -f <(bash "$SKILL_DIR/redact-expr.sh" "$tmp/d.json" "$1" "$2") "$root$1"
}

v=$(redacted /etc/fakesvc/fakesvc.conf view)
assert_contains "$v" $'db_password = <REDACTED>\nupstream smtp://dryrun:<REDACTED>@mail.example.net:587' "view: unquoted value and url userinfo blanked"
assert_eq "" "$(grep -oE 'hunter2|relaypass|relayuser' <<<"$v")" "view: no values survive"

r=$(redacted /etc/fakesvc/conf.d/extra.conf replay)
assert_contains "$r" $'api_token: CHANGEME-dryrun\nsecret "CHANGEME-dryrun";' "replay: colon separator and quoted value keep syntax"

r=$(redacted /etc/systemd/system/fakesvc.service.d/override.conf replay)
assert_contains "$r" 'Environment=DB_PASSWORD=CHANGEME-dryrun' "replay: Environment= wrapper"
finish
```

- [ ] **Step 2: Run to confirm it fails**

Run: `bash tests/test_redact.sh`
Expected: `FAIL` lines.

- [ ] **Step 3: Implement**

`redact-expr.sh`
```bash
#!/usr/bin/env bash
# redact-expr.sh: print a sed -E script that blanks flagged credential values in one config file.
# usage: redact-expr.sh <discover.json> <file> [view|replay]
#   view: values become <REDACTED>; replay: values become CHANGEME-dryrun
# run the output on the source: ssh src "sudo sed -E -f /dev/stdin <file>" < script
set -euo pipefail

usage='usage: redact-expr.sh <discover.json> <file> [view|replay]'
json=${1:?$usage}
file=${2:?$usage}
case ${3:-view} in
    view) val='<REDACTED>' ;;
    replay) val='CHANGEME-dryrun' ;;
    *) echo "$usage" >&2; exit 64 ;;
esac

jq -r --arg f "$file" '.references.credentials[] | select(.file == $f) | "\(.line) \(.key)"' "$json" |
    while read -r line key; do
        case $key in
            url-userinfo)
                printf '%ss#(://)[^/@[:space:]]+@#\\1dryrun:%s@#g\n' "$line" "$val"
                ;;
            '(unparsed)')
                printf '%ss/^([[:space:]]*[^[:space:]=:]+[[:space:]=:]+).*/\\1%s/\n' "$line" "$val"
                ;;
            *)
                k=$(sed 's/[.[\*^$/]/\\&/g' <<<"$key")
                # quoted value first, keeping its quotes; otherwise blank to ; or end of line
                printf '%s{s/(%s[[:space:]]*[=:]?[[:space:]]*)(["'"'"'])[^"'"'"']*(["'"'"'])/\\1\\2%s\\3/;t\n' "$line" "$k" "$val"
                printf 's/(%s[[:space:]]*[=:]?[[:space:]]*)[^;]*/\\1%s/\n}\n' "$k" "$val"
                ;;
        esac
    done
```

```bash
chmod +x redact-expr.sh
```

- [ ] **Step 4: Run all tests**

Run: `bash tests/run.sh`
Expected: every line `ok` (about 23 in total) and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add sysadmin/service-migration/redact-expr.sh sysadmin/service-migration/tests/test_redact.sh
git commit -m "service-migration: redact-expr.sh for safe config viewing and dry-run replay"
```

---

### Task 6: plan_template.md, SKILL.md, spec sync

**Files:**
- Create: `sysadmin/service-migration/plan_template.md`
- Create: `sysadmin/service-migration/SKILL.md`
- Modify: `docs/superpowers/specs/2026-09-24-service-migration-design.md`

**Interfaces:**
- Consumes the script CLIs exactly as documented in Tasks 1-5.

- [ ] **Step 1: Write `plan_template.md`**

````markdown
# Migration plan: {{service}} on {{source_host}}

> Generated {{date}} by service-migration. Source: {{source_os}}. Destination: {{VM|container}} ({{dest_os}}).
> Discovery files: {{discover json paths}}

## 1. Summary

- **Service:** {{service}} ({{package}} {{version}})
- **Verdict:** {{VM|container}}: {{one-line reason from the verdict heuristic}}
- **Confidence:** {{high|medium|low}}: {{what could still be missing, e.g. partial discovery without sudo}}
- **Data volume:** {{total}} across {{n}} paths

## 2. Dependency graph

```
{{service}}
├── {{dep}}  [migrate-with | already-remote | replace-at-dest]
└── {{dep}}  [...]
```

## 3. Carry-over manifest

| Path | Kind | Size | Why |
|------|------|------|-----|
| {{path}} | config/data/log | {{size}} | {{unit setting, config directive, or rpm -qc}} |

### Non-default and undocumented options

- `{{file}}:{{line}}` `{{directive}}`: {{what it does and why it matters at the destination}}

## 4. Host scaffolding

| Item | Source detail | Action at destination |
|------|---------------|-----------------------|
| Firewall | {{zone: services/ports}} | {{open only what the source had}} |
| Cron/timers | {{files}} | {{copy, adapt, or drop}} |
| Logrotate | {{files}} | {{copy}} |
| Sudoers | {{files}} | {{copy after visudo -c}} |

### SELinux recipe

Source mode: {{Enforcing|Permissive|Disabled}}

- **Mislabeled on source** (`restorecon -n`): {{path: current → expected}}. {{Fix at the destination with semanage fcontext, or move the data to a properly labeled path.}}
- **Local customizations to carry:** {{booleans, fcontexts, ports, modules relevant to this service}}
- **Dry-run fixes:** each rule with the denial it resolves

```
{{semanage fcontext -a -t <type> '<path>(/.*)?' && restorecon -RF <path>}}
{{setsebool -P <bool> on}}
{{semodule -i <name>.pp   # .te below}}
```

```
{{final .te from audit2allow, each allow line commented with the denial it fixes}}
```

## 5. 🔐 Certs and credentials (manual re-provision)

Values are never recorded here. Re-issue or copy these by hand at cutover.

| Config key | File:line | Path | Kind | Expires | Action |
|------------|-----------|------|------|---------|--------|
| {{key}} | {{file}}:{{line}} | {{path}} | cert/key/ca/credential | {{date}} | {{re-issue, copy securely, rotate}} |

## 6. ⚠️ Exposure and split-brain

- [ ] **Bind drift:** {{source listens on X; destination config/port mapping would listen on Y}}
- [ ] **Unjustified egress:** {{public peers observed or implied by config, and whether the service has business reaching them}}
- [ ] **Dual-live risk:** {{stateful paths that must stop at the source before the final sync}}
- [ ] **Identity collisions:** {{hostnames, NS/SOA, MX/SPF/PTR, DKIM selectors, IP-bound listen directives}}

## 7. Dry-run result

{{target (droplet name/IP or container), date, one line per verify.sh check with pass/fail and detail, fixes applied during replay}}

## 8. Cutover runbook (human-run)

| # | Step | Rollback |
|---|------|----------|
| 1 | {{step}} | {{rollback}} |
````

- [ ] **Step 2: Write `SKILL.md`**

````markdown
---
name: service-migration
description: Research an installed systemd service (named, httpd, postfix, amavis, spamassassin, etc.) on a local or SSH-reachable host, recursively map its dependencies, undocumented config, data paths, SELinux labels and policy, certs, and credential references, write a condensed Markdown migration plan, and prove it with a dry-run replay into a throwaway DigitalOcean droplet or podman container. Use when the user wants to move, migrate, containerize, or rehost a system service or daemon. Never performs the live cutover.
---

# Service Migration

Four phases: **discover → plan → dry run → stop.** The scripts gather and check facts. You make the judgment calls: what is in scope, what is risky, VM or container, and which SELinux rules are justified.

## Hard rules

- 🛑 **The source is read-only.** Commands allowed there: `cat`, `ls`, `stat`, `find`, `grep`, `sed` (never `-i`), `ps`, `du`, `ss`, `systemctl show|cat|is-*|list-*`, `rpm -q*|-V`, `semanage * -l`, `semodule -l`, `getsebool`, `restorecon -n` (never without `-n`), `firewall-cmd --list-*|--get-*`, `crontab -l`, `openssl x509 -noout`, `tar -c`. Never stop, restart, reload, relabel, or write anything.
- 🔐 **Never read credential values or private keys.** Never `cat` a file listed in `references.credentials`; use the safe view below. Never open a `references.certs` entry of kind `key`.
- 🧪 **The dry-run target is disposable.** It always has a TTL and is destroyed at the end unless the user asks to keep it. SELinux experiments (`audit2allow`, `semodule -i`, permissive domains) happen only there.
- ✋ **No live cutover.** The plan's runbook is for the human.

## Setup

```bash
SKILL_DIR=<directory containing this SKILL.md>
REPO=$(git -C "$SKILL_DIR" rev-parse --show-toplevel 2>/dev/null)
if [[ -n $REPO && $(git rev-parse --show-toplevel 2>/dev/null) == "$REPO" ]]; then OUT=./tmp; else OUT=./migrations; fi
mkdir -p "$OUT"
```

The target is `local` or an SSH destination. For remote targets, confirm non-interactive sudo first with `ssh <host> sudo -n true`. If that fails, ask for root SSH or a NOPASSWD rule. If neither is available, run without sudo and mark confidence **low (partial discovery)**. For a local target without passwordless sudo, ask the user to run the discover command with the `!` prefix.

## Phase 1: Discover

```bash
# remote
ssh <host> 'sudo bash -s -- <service>' < "$SKILL_DIR/discover.sh" > "$OUT/<host>-<service>-discover.json"
# local
sudo bash "$SKILL_DIR/discover.sh" <service> > "$OUT/$(hostname -s)-<service>-discover.json"
```

Exit 2 with `"error":"unit not found"` means the unit name is wrong. Try `systemctl list-units --type=service | grep -i <name>` on the source. Query the JSON in slices with jq, e.g. `jq '.selinux' file`, rather than reading it whole.

**Recurse** into dependencies, capped at depth 3, with a visited set:

- `references.services` unix socket: `ss -xlpn | grep -F <path>` gives the pid, then `ps -o unit= -p <pid>` gives the unit.
- `references.services` pointing at localhost or the host's own IP: `ss -tlpn 'sport = :<port>'` gives the pid, then `ps -o unit= -p <pid>` gives the unit.
- `unit.After` / `unit.Requires` entries that are real services, not `*.target` and not `systemd-*`, are candidates.
- Other hosts are recorded as **already-remote** and not followed. The destination must still reach them.
- Skip base-system units (sshd, chronyd, dbus, NetworkManager, firewalld, auditd, rsyslog, journald) unless a config names them directly.

**Safe view** of a config file, with credential values blanked on the source and comments stripped:

```bash
bash "$SKILL_DIR/redact-expr.sh" "$OUT/<host>-<svc>-discover.json" <file> view |
    ssh <host> "sudo sed -E -f /dev/stdin <file>" | grep -vE '^[[:space:]]*(#|;|$)'
```

Use the safe view for every file in `package.modified` and every `config_files` entry with `"package": null`. That is where undocumented customizations live.

**SELinux facts to note:**
- Every `selinux.mislabeled` entry. These usually come from a past `chcon` that was never recorded with `semanage fcontext`.
- The `selinux` customizations that relate to this service's paths, ports, and domain. The lists are host-wide, so drop anything unrelated.
- Any listen port not covered by the domain's port type: `semanage port -l | grep -w <port>`.

## Phase 2: Plan

Copy `plan_template.md` to `$OUT/<host>-<service>-<YYYY-MM-DD>.md` and fill in every section. Keep it to 1-2 screens per service and delete template lines that don't apply.

- **Verdict heuristic.** Choose a **container** for a single process, few host hooks, and state in clear volume paths. Choose a **VM** for custom SELinux modules, multi-daemon pipelines (amavis → spamd → clamd), raw sockets, kernel tunables, or heavy cron coupling. The user can override it.
- **SELinux recipe (section 4).**
  - For each mislabeled path, prefer `semanage fcontext` for the path as it is, or moving the data to a standard labeled location, over a policy module.
  - Carry only the customizations that relate to this service.
  - Leave "Dry-run fixes" empty until Phase 3.
- **Exposure checks (section 6):**
  - **Bind drift:** compare `network.listen` with what the destination config or `podman -p` mapping would bind. Loopback or private becoming `0.0.0.0` or published is a flag.
  - **Unjustified egress:** compare `network.outbound` and config intent against what the service should reach. Open recursion on a resolver and relaying out from local-delivery MTAs are flags.
  - **Dual-live risk:** zone files, mail queues, and DBs need a "stop source before final sync" gate.
  - **Identity collisions:** listen directives bound to source IPs, NS/SOA, MX/SPF/PTR, DKIM selectors, and hostname-based vhosts.
- **OS jumps** such as EL7 → EL9 change package majors, config syntax, and policy module names. Flag every directive that will break.

Show the user the plan path and a 3-line summary. Before Phase 3, ask which destination to use: droplet (default) or container.

## Phase 3: Dry run

### Provision

**Droplet (default)** uses the sibling `digitalocean` skill. Rocky images run SELinux enforcing, which is what makes this dry run worth doing.

```bash
DO="$SKILL_DIR/../../digitalocean/do_droplet.py"
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
2. **Tar the manifest.** Exclude every `references.certs` path of kind `key` and every file listed in `references.credentials`:

   ```bash
   ssh <host> "sudo tar --selinux --acls --xattrs -cpf - -C / --exclude=<path> ... <paths without leading />" |
       ssh dst "tar --selinux --acls --xattrs -xpf - -C /"
   ```

   Ask before tarring anything over 1 GB. For the dry run a representative subset is fine; note it in section 7.
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
   T=$(ssh dst "LC_ALL=C date +'%m/%d/%Y %H:%M:%S'")
   ssh dst "<apachectl -t | named-checkconf -z | postfix check | spamassassin --lint> && systemctl enable --now <service>"
   ```

   Containers have no systemd. Run the ExecStartPre steps by hand, start the daemon with `podman exec -d mig-<service> bash -c 'set -a; . <EnvironmentFile>; exec <ExecStart argv, foreground>'`, and get its PID with `podman exec mig-<service> pgrep -o -f <binary>`.

### Verify

```bash
ssh dst "bash -s -- <service> --listen '<entry>' ... --probe <dns|http|https|spamc|port> --watch 30 --since '$T' [--allow-egress <ip>|all]" \
    < "$SKILL_DIR/verify.sh"
# container
podman exec -i mig-<service> bash -s -- <service> --pid <pid> --listen "<entry>" ... < "$SKILL_DIR/verify.sh"
podman port mig-<service>
ausearch -m AVC -ts recent | grep container_t    # host side, containers only
```

- `--listen` entries come from the source's `network.listen`, with rewritten IPs substituted.
- `--allow-egress` lists only the public peers the source legitimately had. Use `all` only for a recursive resolver or an outbound relay that the plan justifies.
- For containers, any `podman port` host binding on `0.0.0.0` or `::` counts as **bind drift** unless the source listened publicly.

### SELinux fix loop (dry-run target only)

When `selinux_clean` fails:

1. **Collect every denial in one pass instead of one at a time.** Run `semanage permissive -a <domain>`, exercise the service (probe, a real request, a cron run), and then run `ausearch -m AVC -ts "$T" | audit2allow -a`.
2. **Read each denial before accepting a rule.** A label problem is fixed with `semanage fcontext` + `restorecon`. A missing permission may be covered by a boolean, which `audit2allow -a` suggests. Write a module only for what remains.
3. **For a module:** `ausearch -m AVC -ts "$T" | audit2allow -M mig_<service>`, review `mig_<service>.te`, then `semodule -i mig_<service>.pp`.
4. **Re-run strict.** `semanage permissive -d <domain>`, restart the service, reset `T`, and run verify again. Repeat until it passes.
5. **Record the result.** Copy the final commands and the `.te` into section 4 "Dry-run fixes", with each allow rule commented with the denial it fixes.

Other failures: fix what the plan can fix, re-run, and record every fix. Append the verify JSON summary to section 7. Then tear down unless the user wants to keep it: `"$DO" --project Testing destroy mig-<service> --yes` or `podman rm -f mig-<service>`.

## Phase 4: Stop

Finish section 8, the human cutover runbook, with a rollback line per step. Include:
- lowering DNS TTL ahead of time
- the stop-source gate before the final data sync
- the SELinux recipe applied before starting the service
- cert re-provisioning from section 5
- removal of the placeholder certs

Hand the plan path to the user. Done.

## Gotchas

- **`tar --selinux` labels** only survive between enforcing hosts, and can carry a wrong label forward. Always `restorecon` after adding the fcontexts.
- **Container volumes** need `:Z` (private) or `:z` (shared) on SELinux hosts. Without it, the container gets host-side denials that never show up inside the container.
- **UIDs differ between hosts.** Installing packages before extracting lets tar map owners by name.
- **ExecStartPre matters** in containers: key generation, config checks, and directory creation all have to be done by hand.
- **Configs bound to the old IP** fail to start on the destination. Check `network.listen` addresses in the configs through the safe view.
- **DO droplets:** for SSH failures on a fresh droplet, see the `digitalocean` skill gotchas. `base-rocky9` already runs a package upgrade first.
````

- [ ] **Step 3: Sync the spec**

Edit `docs/superpowers/specs/2026-09-24-service-migration-design.md`:

- **Layout:** add a line after `verify.sh`: `  redact-expr.sh     # builds sed scripts that blank credential values (view or replay)`
- **Phase 1, Host scaffolding bullet:** append `; files whose SELinux label differs from policy via `restorecon -Rnv` on data paths and /etc/<service> (read-only)`.
- **Phase 1 Hard rule:** replace the allowlist sentence with: `**Hard rule:** only read-only commands run against the source. Allowlist: `cat`, `ls`, `stat`, `find`, `grep`, `sed` (never `-i`), `ps`, `du`, `ss`, `systemctl show|cat|is-*|list-*`, `rpm -q*|-V`, `semanage * -l`, `semodule -l`, `getsebool`, `restorecon -n`, `firewall-cmd --list-*|--get-*`, `crontab -l`, `openssl x509 -noout` (public certs only), `tar -c` (dry-run export only).`
- **Phase 2, item 4:** change it to `**Host scaffolding:** SELinux recipe (mislabels, carried customizations, dry-run fixes with the denial each resolves), firewalld, cron/timers, logrotate, sudoers`.
- **Phase 3, Container bullet:** replace it with `- **Container:** `podman run` of a base image matching the source OS family, kept alive with `sleep infinity`. Packages are installed with `podman exec`, and the daemon runs in the foreground from its ExecStart. `verify.sh --pid` replaces the systemd and SELinux checks; host-side AVCs and `podman port` bindings are checked separately.`
- **Phase 3, Replay bullet:** replace it with `- **Replay:** install packages first, then `tar` the manifest excluding private keys and credential-bearing files. Credential-bearing files are streamed through `redact-expr.sh ... replay`, which runs sed on the source, so values arrive as `CHANGEME-dryrun` and real values never leave the host. One self-signed placeholder pair is installed at every cert and key path. The SELinux baseline is applied, then `restorecon -RF`.`
- **verify.sh checks list:** add `- no AVC denials for the service's domain since start (`ausearch`), with a permissive-domain + `audit2allow` fix loop on the dry-run target only`
- **Testing section:** replace it with: `A lean suite of about 23 assertions covers secret leaks, SELinux parsing (`semanage`/`semodule`/`restorecon` in both output formats), and verify's drift, egress, and denial detection. Other parsers are covered by live runs against `sshd`/`chronyd` and a droplet end-to-end run that includes a deliberate mislabel.`

- [ ] **Step 4: Check the docs against the scripts**

Run:
```bash
grep -oE -- '--(listen|allow-egress|probe|watch|since|pid)' sysadmin/service-migration/SKILL.md | sort -u
grep -oE -- '--(listen|allow-egress|probe|watch|since|pid)\)' sysadmin/service-migration/verify.sh | sort -u
```
Expected: the same six flags in both.

- [ ] **Step 5: Commit**

```bash
git add sysadmin/service-migration/SKILL.md sysadmin/service-migration/plan_template.md docs/superpowers/specs/2026-09-24-service-migration-design.md
git commit -m "service-migration: SKILL.md runbook with SELinux fix loop, plan template, spec sync"
```

---

### Task 7: Verification

**Files:**
- Modify: `README.md`: drop "(in progress)" once Steps 1-3 pass

- [ ] **Step 1: Test suite**

Run: `bash sysadmin/service-migration/tests/run.sh`
Expected: about 23 `ok` lines and exit code 0.

- [ ] **Step 2: shellcheck (if available)**

Run: `command -v shellcheck && shellcheck -x sysadmin/service-migration/*.sh sysadmin/service-migration/tests/*.sh sysadmin/service-migration/tests/stubs/*`
Expected: no errors. If shellcheck is missing, ask the user to run `! sudo dnf install -y ShellCheck`, or record it as skipped. Fix warnings instead of blanket-disabling them.

- [ ] **Step 3: Live local smoke test (covers scaffolding, firewall, and real SELinux tool output)**

```bash
cd sysadmin/service-migration
for s in sshd chronyd; do
    if sudo -n true 2>/dev/null; then sudo bash discover.sh "$s"; else bash discover.sh "$s"; fi > "/tmp/claude-sm-$s.json"
    jq -e '.schema == 1 and (.config_files | length > 0) and (.selinux | has("mislabeled"))' "/tmp/claude-sm-$s.json" >/dev/null && echo "ok $s" || echo "FAIL $s"
done
bash discover.sh nosuchsvc; echo "rc=$?"
jq '.references.credentials, .selinux.mode, .scaffolding' /tmp/claude-sm-sshd.json
```
Expected: `ok sshd`, `ok chronyd`, `rc=2`. The credentials list shows key names only. Without sudo, `network`, `sudoers`, and some `selinux` fields are partial, which is fine here. Put the JSON files in the session scratchpad rather than `/tmp` if one is available, and delete them afterwards.

- [ ] **Step 4: End-to-end droplet test (optional, costs money; ask the user first)**

This only runs if `DIGITALOCEAN_TOKEN` is set and the user approves.
1. Create a source with `do_droplet.py --project Testing create --name mig-src --preset node-apache --ttl 2h ...`. Add `mod_ssl` and a self-signed cert on it.
2. Plant a mislabel on the source: `mkdir /srv/site && echo hi > /srv/site/index.html && chcon -R -t httpd_sys_content_t /srv/site`, then point a vhost `DocumentRoot` at it. This works on the source, but `restorecon -n` flags it.
3. Run the skill end to end against `root@<src-ip>` for `httpd`. Confirm:
   - section 5 lists `SSLCertificateFile` and `SSLCertificateKeyFile`
   - section 4 lists `/srv/site` as mislabeled
   - the dry run passes once the recipe (`semanage fcontext -a -t httpd_sys_content_t '/srv/site(/.*)?'`) is applied
4. Negative checks on the destination:
   - `chcon -t default_t /srv/site/index.html`, then run `verify.sh ... --probe http`. Expect `selinux_clean` to fail with a `default_t` denial.
   - `echo 'Listen 0.0.0.0:8080' > /etc/httpd/conf.d/drift.conf && systemctl restart httpd`. Expect `listen_match` to fail with `BIND DRIFT`.
5. Destroy both droplets.

- [ ] **Step 5: Update the README and commit**

In `README.md`, change `dry-run it into a VM or podman container (in progress)` to `dry-run it into a VM or podman container`.

```bash
git add README.md
git commit -m "README: service-migration skill ready"
```

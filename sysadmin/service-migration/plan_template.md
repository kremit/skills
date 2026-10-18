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

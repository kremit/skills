# Claude Code Skills

Personal collection of Claude Code skills. Each directory with a `SKILL.md` is a skill; some also bundle helper scripts.

## Skills

### Claude Code utilities

- `sessions/`: list and manage Claude Code sessions
- `transcribe/`: write a running chat transcript to Markdown
- `test-harnesses/`: smoke-test harnesses, e.g. Playwright web checks
- `safety/`: behavior feedback notes

### Web

- `web/web-fetch/`: fetch pages through your own headless Chrome or Firefox with sentinel checks, cloned profiles, a warm daemon and robots.txt reporting

### Productivity

- `trello/`, `trello-daily-summary/`, `defer/`, `defer-trello/`: Trello access, daily summary, and deferred tasks
- `notification-checkup/`: daily notification review ritual
- `mailsubjects/`, `vpa/`, `project-review/`: workflow docs and scripts

### Data

- `data/dataset-recon/`: reconnaissance on an unfamiliar dataset: gather scattered docs, decode a sample (GDAL, wgrib2, ncdump, pandas) and write a short profile of contents, grid, size, cadence and quirks

### Sysadmin

- `sysadmin/service-migration/`: research a system service and its dependencies, write a migration plan, and dry-run it into a VM or podman container

### Cloud VMs

- `cloud/digitalocean/`: spin up, inspect, and destroy droplets with cloud-init presets and TTL tagging
- `cloud/aws/`: EC2 instances with cloud-init presets and tag-based TTL
- `cloud/atlanticnet/`: Atlantic.net servers with shell bootstrap presets and TTL tracking
- `lib/cloud/`: shared HTTP, TTL, and cloud-init helpers for the provider skills

## Conventions

- Scratch output from skills run inside this repo goes to `./tmp/`, which is gitignored.
- Design specs live in `docs/superpowers/specs/`.

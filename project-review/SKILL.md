---
name: project-review
description: Registry based review of every project the user admins. Finds GitHub work waiting on them (review requests, mentions, assigned issues, stale own PRs, red CI), checks the registry repos, and checks local clones for uncommitted or unpushed work. Use when the user runs /project-review, asks what is waiting on them, asks whether anything was forgotten to commit or push, or the daily summary shows a PROJECT REVIEW day. Args: scan, fetch, local, hosted, update.
---

# Project Review

Purpose: assigned work arrives by email, text, and notifications and gets missed. This review pulls it into one list.

## Run

    ~/.claude/skills/project-review/run.sh [scan|fetch|local|hosted|update]

- no arg: all layers. Repo discovery runs only on review days.
- `scan`: all layers with full discovery of repos missing from the registry
- `fetch`: all layers, local trees run `git fetch` first
- `local`: local tree check only
- `hosted`: registry repo report only
- `update`: refresh CSV git HEADs and DNS, then the registry repo report

## Layers

1. Registry scan (`$VPA_DIR/registry_scan.sh`): waiting on you, assigned issues, your open PRs, CI, noise count, repos not in the registry, GitLab stub.
2. Registry repos (`project_review.sh`): last push and open PRs for each GitHub repo in the CSV.
3. Local trees (`local_tree_check.sh`): work clones, as configured in `$VPA_DIR/local_tree_check.sh`.

Self-hosted GitLab is not wired yet. Token setup is planned; until then the scan prints "GitLab: not configured".

## Rules

- Registry is `~/dev/projects/meta/projects_report.csv`. Do not add repos unless the user says so. The scan only proposes rows; never write the CSV from this skill.
- Report only. Never commit, push, stash, fetch outside `fetch`, merge, comment, or mark notifications read without being asked.
- Lead with WAITING ON YOU. Items marked "likely resolved" are unread notifications only, so say so.
- Subscribed notifications are noise and stay suppressed.
- Jira is out of scope. Bugs and feature work found here go to Jira, not Trello cards.
- Never read credential directories next to the clones.

## Files

- Outputs: `out/registry_scan_latest.txt`, `out/project_review_latest.txt`, `out/local_tree_latest.txt` under `$VPA_DIR`
- Review days: `project_review_days` in `$VPA_DIR/vpa_config.json`
- `VPA_DIR` defaults to `~/vpa`; export it to point elsewhere

---
name: project-security
description: Project security review. Finds git repos with package.json or composer.json in local project dirs and in the project-review registry, then runs npm audit or composer audit on their lockfiles and lists critical and high advisories. Remote GitHub repos are audited from committed lockfiles without cloning. Use when the user asks for a project security review, a dependency or vulnerability audit across projects, whether a project has known npm or Composer vulnerabilities, or when project-review runs its security layer.
---

# Project security review

```bash
SKILL_DIR=<directory containing this SKILL.md>
"$SKILL_DIR/audit.py"                              # local clones and registry repos
"$SKILL_DIR/audit.py" --project my-app             # one project, by repo or registry id
"$SKILL_DIR/audit.py" --local-only                 # skip the registry
"$SKILL_DIR/audit.py" --root ~/src --root ~/work   # other local roots
```

Also runs as layer 4 of project-review: `~/.claude/skills/project-review/run.sh security`.

## What it scans

- **Local:** git repos at depth 1-2 under `~/dev/company/repo` and `~/dev/projects`, or `--root` (repeatable), or `PROJECT_AUDIT_ROOTS` as a colon-separated list. Manifests are found up to 3 levels inside each repo, skipping `vendor` and `node_modules`.
- **Registry:** every `git repo` in `~/dev/projects/meta/projects_report.csv`, or `--registry`, or `PROJECT_REGISTRY`. GitHub repos are audited from their lockfiles through `gh api`; a repo already cloned locally is audited once, from the clone. Other hosts, such as GitLab, are listed as skipped.
- **Audits:**
  - `npm audit --package-lock-only --ignore-scripts` on `package-lock.json` or `npm-shrinkwrap.json`
  - `composer audit --locked --no-plugins --no-scripts` on `composer.lock`
  - yarn, pnpm and bun lockfiles are reported but not audited

Exit codes: 0 no critical or high findings, 3 critical or high findings, 5 every audit failed.

## Reading the report

The table lists one row per manifest, worst first, then each critical and high advisory with the installed version and the fix or affected range. The full report, including medium and low findings and abandoned Composer packages, is written to `tmp/project-security/latest.json` inside this repo, else `./project-security/latest.json`.

| Status | Meaning |
|---|---|
| `ok` | audited; the counts are the result |
| `no lock` | a manifest with no lockfile, so nothing could be audited |
| `skipped` | an unsupported lockfile, a missing tool, or a repo not on GitHub |
| `error` | the audit failed; the note has the reason |
| `lockfile not committed` | audited from a local lock that isn't in git, so the remote repo can't be audited and other clones may resolve different versions |

When reporting:
- Lead with critical and high, naming the project, package, installed version and fix.
- Group repeats of one package across projects.
- Say which projects couldn't be audited and why.

A remote audit covers the default branch as committed, not what's deployed on a server. For a host's running packages, use security-recon.

## Safety

- Read only. It never installs, updates or runs package scripts and Composer plugins, and remote repos are never cloned.
- The audits send each project's dependency names and versions to the npm registry and to Packagist.
- Never run `npm audit fix` or `composer update` from this skill. Propose the fix and let the user apply it in the project.

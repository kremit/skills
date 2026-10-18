---
name: notification-checkup
description: Daily notification review ritual. Sweeps common push-notification sources (GitHub, Trello, Google Calendar, Gmail, Claude Scheduler) and surfaces anything actionable that may have been missed. Intended as a close-of-day or start-of-day ritual step within a Resume Stack workflow.
---

# Notification Check-Up Skill

A **skill-of-skills** for a daily notification sweep. The goal is not to process everything — it's to make sure nothing *important* slipped through the cracks since the last check. Run this as part of a daily open or close ritual.

This skill composes other skills and tools. Run each section that applies to your setup.

---

## Concept: Resume Stack Integration

This check-up is designed to slot into a "Daily Close Ritual" or "Daily Open" card in your task system. The sweep order below is intentional — highest-signal sources first.

```
1. Claude Scheduler   → Did any agents run? Did they succeed or need attention?
2. GitHub             → PRs, reviews, CI failures, @mentions
3. Trello             → Recent activity, due dates, comments directed at you
4. Google Calendar    → What's coming up today / tomorrow?
5. Gmail              → (optional) Starred, flagged, or high-priority unread
```

---

## 1. Claude Scheduler

Check whether any scheduled remote agents ran and whether they need follow-up.

**In a Claude Code session**, use the `RemoteTrigger` tool (load via ToolSearch first):

```
List all triggers and their last-run status.
Flag any that are disabled, errored, or haven't run when expected.
```

Or use the web UI: https://claude.ai/code/scheduled

**What to look for:**
- Triggers that are enabled but show no recent run
- Agents that ran and produced output worth reviewing
- Any disabled triggers that should be re-enabled

---

## 2. GitHub

Requires `gh` CLI authenticated (`gh auth status`).

### Notifications (all unread)
```bash
gh api notifications --paginate | jq '.[] | {repo: .repository.full_name, type: .subject.type, title: .subject.title, reason: .reason, unread: .unread}'
```

### PR review requests assigned to you
```bash
gh search prs --review-requested=@me --state=open --json title,url,repository,updatedAt | jq '.[] | {title, url, repo: .repository.nameWithOwner}'
```

### Open PRs you authored
```bash
gh search prs --author=@me --state=open --json title,url,repository,updatedAt | jq '.[] | {title, url, repo: .repository.nameWithOwner, updated: .updatedAt}'
```

### Recent mentions across your orgs
```bash
gh api notifications | jq '[.[] | select(.reason == "mention" or .reason == "review_requested")]'
```

### CI failures on your recent commits
```bash
# Per repo — check the last N workflow runs
gh run list --repo OWNER/REPO --limit 5 --json status,conclusion,displayTitle,headBranch | jq '.[] | select(.conclusion == "failure" or .conclusion == "cancelled")'
```

**What to look for:**
- Unread review requests or @mentions
- Failed CI on open PRs
- PRs stale >48h without comment

---

## 3. Trello

Uses the Trello API skill (`trello/SKILL.md`) and the local `trello_api.py` helper.

### Recent @mentions in comments (any board)
```bash
# Actions where your name appears in a comment — scope to relevant board IDs
python3 -c "
import trello_api as t, os
board_id = os.environ['TRELLO_BOARD_ID']
actions = t.get_recent_actions(board_id, since_hours=24, filters='commentCard')
for a in actions:
    print(a['memberCreator']['fullName'], ':', a['data'].get('text','')[:100])
"
```

### Cards due today or overdue (main board)
```bash
python3 -c "
import trello_api as t, os
from datetime import datetime, timezone
cards = t.get_cards_on_board(os.environ['TRELLO_BOARD_ID'])
now = datetime.now(timezone.utc)
for c in cards:
    if c.get('due'):
        from datetime import datetime
        d = datetime.fromisoformat(c['due'].replace('Z','+00:00'))
        if d <= now and not c.get('dueComplete'):
            print('OVERDUE:', c['name'], '|', c['_list_name'])
"
```

### Run the full daily summary
```bash
python ~/dev/projects/skills/trello-daily-summary/trello_daily_summary.py
```
Or use the alias: `vpa-review`

**What to look for:**
- Comments directed at you on shared boards
- Cards that became overdue since your last check
- Cards moved into active lists by collaborators

---

## 4. Google Calendar

Requires Google Calendar MCP connector (connect at https://claude.ai/settings/connectors).

Once connected, ask Claude in-session:

```
Check my Google Calendar for:
1. Any events added or modified in the last 24 hours
2. Events starting in the next 12 hours I may not have reviewed
3. Any event invites still pending a response
```

**What to look for:**
- Newly added or rescheduled meetings
- Pending invites (accepted/declined?)
- Back-to-back conflicts that appeared since last check

---

## 5. Gmail *(optional — skip if not used)*

> Note: If Gmail is not your primary email, skip this section or adapt for your mail client.

Requires Gmail MCP connector (connect at https://claude.ai/settings/connectors).

Once connected, ask Claude in-session:

```
Check Gmail for:
1. Unread messages marked important or starred since [timestamp]
2. Any unread threads from known contacts or domains
3. Any emails with subject lines matching: invoice, urgent, action required, deadline
```

**What to look for:**
- Starred/important unread
- Thread replies you're CC'd on that may need action
- Time-sensitive subject lines

---

## Quick Sweep Mode

For a fast close-of-day check when time is short, run only these:

```bash
# 1. GitHub: any unread notifications?
gh api notifications | jq 'length'

# 2. Trello: anything overdue or new activity?
python ~/dev/projects/skills/trello-daily-summary/trello_daily_summary.py | grep -A5 "Cards Due\|Recent Activity"

# 3. Claude Scheduler: check at https://claude.ai/code/scheduled
```

Then ask Claude: *"Summarize anything actionable from these results."*

---

## Extending This Skill

Additional services to add as connectors or CLI tools become available:

| Service | Method | What to check |
|---|---|---|
| Slack | MCP connector | DMs, @mentions, starred messages |
| Linear / Jira | MCP or API | Issues assigned, status changes |
| PagerDuty / Nagios | API or CLI | Alert status, resolved incidents |
| SMS/Voicemail | Manual | (flag as manual step in ritual card) |
| Zabbix | Web UI or API | New alerts since last check |

---

## Notes

- This skill is intentionally **read-only**. If something requires action, handle it via the relevant skill (`trello/SKILL.md`, `gh` commands, etc.).
- The sweep should take **5 minutes or less**. If it's taking longer, the signal-to-noise ratio of a source is too high — triage that source's notification settings separately.
- For shared boards (family, property manager), a separate sweep cadence (e.g. weekly) may be more appropriate than daily.

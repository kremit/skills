---
name: defer
description: Pattern for deferring a task, reminder, or research check to a future date. Describes the concept of "snooze as a workflow" — capturing intent now, acting later — and how to implement it in common task systems.
---

# Defer Skill

**Deferring** means capturing something you can't or won't act on now, attaching a future date to it, and trusting your system to resurface it at the right time. It's distinct from a to-do (act soon) and a backlog item (act someday) — a deferred item has a specific *when*.

Common uses:

- Follow up with someone after a reasonable wait ("check in with X in 6 weeks")
- Re-evaluate a decision after more information is available ("revisit pricing model in Q3")
- Watch for external events ("check if new version of Y is out in 3 months")
- Time-box a recurring personal task ("replace filter in 90 days")

---

## The Pattern

1. **Capture intent** — write a clear title that describes the action, not just the topic. "Check if NOAA released updated boundary layer paper" beats "NOAA paper."
2. **Attach a future date** — the date should reflect when you want to *be reminded*, not necessarily when the thing happens.
3. **Park it** — put it somewhere your system already scans. A backlog list with a due date is enough.
4. **Trust the system** — don't keep it in your head. The whole point is to offload the "remember to check" loop.

---

## Implementation Examples

### Trello (simplest)

Create a card with a future due date in a list your daily summary scans. It surfaces automatically as the date approaches. In the VPA setup, 30 days or less goes to Backlog on Daily List, and 31 days or more goes to the Junkyard board (list Long Range Reminders). See `defer-trello/SKILL.md` for the routing and the helper.

```
Title:  "Follow up with Dan about insurance renewal"
List:   Backlog
Due:    2026-06-01
```

### GitHub Issues

Create an issue with a future milestone, or add a scheduled label + comment with a date reference. Less elegant for personal reminders, better for team-visible defers.

### Calendar event (no-code)

A 15-minute calendar block with the action in the title. Works well for hard commitments or anything tied to a specific time of day.

### Scheduled remote agent (autonomous)

For defers that involve *checking* something (an API, a search, a feed), a scheduled Claude Code agent can do the checking for you and only create a follow-up card if something new is found. See the `/schedule` skill.

---

## Relative Date Reference

When writing defer scripts or prompts, common offsets:

| Human phrase | Approximate offset |
|---|---|
| next week | +7 days |
| 2 weeks | +14 days |
| 1 month | +30 days |
| 6 weeks | +42 days |
| 2 months | +60 days |
| 1 quarter | +90 days |
| 6 months | +180 days |

In the VPA Trello setup, "1 month" (+30) and shorter go to Backlog; "6 weeks" (+42) and longer go to Junkyard.

---

## Anti-patterns

- **Deferring without a title that implies action** — "NOAA" is not deferrable. "Check NOAA for updated QPF data" is.
- **Deferring to a list your system doesn't scan** — the card does nothing if it never resurfaces.
- **Over-deferring** — if you keep pushing the same item forward, it's a signal to either commit or delete it.

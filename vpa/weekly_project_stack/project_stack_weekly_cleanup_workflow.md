---
name: weekly-project-stack
description: Weekly and periodic maintenance rhythm for the Resume Stack (Active Card Stack) system - Friday Extended Close, Sunday/Monday Intake + Backlog Review, Parking Lot/Backlog/Junkyard expiration mechanics, and periodic rituals (Deep Cleaning, Backlog Grooming). Companion to project_stack_operating_model.md, which covers daily mechanics.
---

# Weekly Project Stack Maintenance Workflow

This document defines the weekly maintenance rhythm for the Resume Stack (Active Card Stack) system,
including expiration mechanics and automation hooks.

## HIGH-LEVEL FLOW (ASCII DIAGRAM)

```text
                 DAILY FLOW
                 ----------
  PARKING LOT  -->  PAUSED STACK  -->  TODAY – ACTIVE  -->  DONE/CONTEXT
      |                 |                  |
      |                 |                  |
      |                 v                  |
      |              BACKLOG  <------------|
      |
      v
  (Expire after 7 days if not promoted)
```

## WEEKLY RHYTHM

### FRIDAY – EXTENDED CLOSE (20–30 min)

```text
  TODAY – ACTIVE
        |
        v
  Reduce to ≤2 cards
        |
        v
  PAUSED STACK (compress to ≤4)
        |
        +-- Keep (clear NEXT)
        |
        +-- Move to BACKLOG
        |
        +-- Archive (delete)

  Quick skim of PARKING LOT
        |
        +-- Delete obvious junk
```


### SUNDAY / MONDAY – INTAKE + BACKLOG REVIEW (30–45 min)

```text
  PARKING LOT (Intake)
        |
        Light scan daily; heavy review Sunday/Monday
        |
        +-- If < 7 days old:
        |        Review as needed (Sunday review; decisive, aggressive)
        |
        +-- If 7–14 days old:
        |        Still in review window — promote or hold
        |
        +-- If ≥ 14 days old:
        |        Must move: Paused Stack (actionable and refined)
        |                   Backlog (actionable, no timeline)
        |                   Junkyard (long defer, >30 days out)
        |                   DELETE (no longer compelling)
        |
        v
  PAUSED STACK
        |
        +-- Promote to TODAY – ACTIVE (max 3 total)
        |
        +-- Move to BACKLOG
        |
        +-- Delete


  BACKLOG
        |
        +-- Strategic (keep)
        +-- Interesting but non-actionable -> Move to Junkyard Board
        +-- Dead / unrealistic -> DELETE
```


## EXPIRATION MECHANICS

PARKING LOT:
- Add custom field: "Added Date"
- < 7 days: normal review cadence (daily light, Sunday heavy)
- 7–14 days: still eligible, but flag for decision on Sunday
- ≥ 14 days: must exit — Backlog, Junkyard, or DELETE

BACKLOG:
- No automatic expiration
- Reviewed weekly during Intake + Backlog Review

JUNKYARD:
- Searchable, messy allowed
- Never reviewed weekly
- No ACTIVE cards ever

## CALENDAR INTEGRATION

FRIDAY (End of Day)
- Event: "Extended Close – Stack Compression"
- Duration: 20–30 min

SUNDAY or MONDAY (Preferred)
- Event: "Weekly Intake + Backlog Review"
- Duration: 30–45 min

These calendar events are protective boundaries, not optional tasks.

## PERIODIC RITUALS (as-needed — not part of the weekly rhythm)

These rituals live outside the weekly rhythm. Both are Operate-mode, one-block-only. Promoted from Backlog when the weekly review surfaces the need.

### DEEP CLEANING RITUAL

For accumulated digital clutter that the daily close doesn't reach. The weekly rhythm handles Trello board compression — this handles the workstation and tools.

**Scope:**
- Close or bookmark lingering browser tabs
- Email: process flagged/starred, unsubscribe, archive bulk
- Desktop notes files → extract anything actionable to VPA/Jira, then delete
- Tickets: triage any backlog of support/ops tickets
- Downloads, temp files, scratch docs

**Rules:**
- Allowed: delete, archive, move to Jira, move to VPA Parking Lot
- Not allowed: refine work, start new threads, resume paused items
- When the block ends: return card to Backlog — the weekly review will surface it again when needed

### BACKLOG GROOMING

Routes Backlog items that have ripened or need cold storage. Distinct from the weekly Intake review, which handles inflow — grooming handles items already in cold storage.

**Goal:** route items to the right place, not delete them.

- Ready to work soon → promote to Paused Stack
- Still cold but valid → leave in Backlog
- Genuinely long-term / no clear timeline → move to Junkyard
- Dead / unrealistic → DELETE

**Rules:**
- No new cards created during this session
- No refinement — if a card needs more thought to evaluate, leave it or move to Junkyard

> Backlog is a staging area. Junkyard is cold storage — searchable, never reviewed weekly.

---

## GUIDING PRINCIPLE

The system is designed to delete more than it preserves.

If an item survives multiple weekly reviews, it has earned its place.

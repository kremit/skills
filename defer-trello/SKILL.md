---
name: defer-trello
description: Create a deferred Trello card with a future due date so it surfaces automatically in the daily summary. Uses trello_api.py's create_deferred_card() wrapper. Defers of 30 days or less go to Backlog on Daily List; longer defers go to the Junkyard board. Invoke when the user says things like "remind me to check X in N weeks/months" or "follow up on Y in Z days."
---

# Defer to Trello Skill

Creates a card in a target Trello list with a future due date. The card surfaces in `vpa-review` as its due date approaches (see "How cards resurface").

See `../defer/SKILL.md` for the general concept.

---

## Routing: the 30 day rule

| due_in_days | Destination |
|---|---|
| 0 to 30 | Daily List (3YBA5AXK), usually the `Backlog 🔵` list |
| 31 or more | Junkyard (WvBjy5ml), list `Long Range Reminders` |

- Exactly 30 days stays on Daily List. 31 is the first Junkyard day.
- `create_deferred_card()` enforces this. If the board is Daily List and `due_in_days > 30`, it redirects to Junkyard / Long Range Reminders, ignores the `list_name` you passed, and returns the card with a `_defer_note` key. Always tell the user when `_defer_note` is present.
- Naming the Junkyard board yourself is fine; the `list_name` you pass is used as given.
- Other boards are not redirected (for example Cat Farm `Consumables & Filters` at 90 days).
- Junkyard lists today: Dashboards/AI Projects, Projects - holder, Parking Lot A - General, Parking Lot B - Dashboards, Parking Lot C - Weather, Long Range Reminders. Use Long Range Reminders for dated defers; the others are idea storage.

---

## Due time

- Default: 18:00 UTC on the target date (`DEFAULT_DUE_HOUR_UTC = 18`). That is 11:00 PDT or 10:00 PST, so the date never flips for the user.
- `local_noon=True`: 12:00 wall-clock time in the system time zone (or `tz="America/Los_Angeles"`), converted to UTC, DST aware. Use it only when the hour matters to a human reading the card.
- "Today" is the local calendar date, so an evening request does not skip a day.
- Trello has no time zone setting for due dates. The API takes and returns UTC instants; the web UI shows them in the viewer's local zone. A fixed 18:00Z therefore reads as 11:00 AM in summer and 10:00 AM in winter. That is expected.

---

## Quick Usage

```python
import trello_api as t

# 3 weeks: stays on Daily List Backlog
card = t.create_deferred_card(
    board_id="3YBA5AXK",
    list_name="Backlog 🔵",
    title="Follow up with Dan Engel about insurance renewal",
    due_in_days=21,
    description="Reached out 2026-04-02. Check if he has reviewed the proposal.",
)
print(card["shortUrl"], card.get("_defer_note"))

# 2 months: go straight to Junkyard
card = t.create_deferred_card("WvBjy5ml", "Long Range Reminders",
    "Check NOAA for updated boundary layer parameterization paper", due_in_days=60)
```

---

## Helper: `create_deferred_card()`

Defined in `trello_api.py`. Resolves board and list by name, computes the due instant, applies the 30 day rule, and creates the card at the bottom of the list.

```python
create_deferred_card(
    board_id,       # (required) full ID, short ID, or board name
    list_name,      # (required) list name, case-insensitive; a unique prefix also matches ("Backlog")
    title,          # (required) card title, action-oriented
    due_in_days,    # (required) int >= 0, days from today
    description,    # (optional) context, links, notes
    local_noon,     # (optional) True = 12:00 local time instead of 18:00Z
    tz,             # (optional) IANA zone for local_noon, default is the system zone
)
```

Returns the full Trello card dict including `shortUrl`, plus `_defer_note` when redirected. Raises `ValueError` for a negative or non-integer `due_in_days`, or a missing or ambiguous list.

`t.compute_due(due_in_days, local_noon=False, tz=None)` returns just the UTC datetime if you need the date math alone.

---

## Common Defers

```python
# Follow up with a person in 3 weeks (30 days or less, Daily List Backlog)
t.create_deferred_card("3YBA5AXK", "Backlog 🔵",
    "Follow up with Bob re: API data domains",
    due_in_days=21,
    description="Last discussed 2026-04-02. They were reviewing PROJ-14.")

# Check for a release in 3 months (over 30 days, Junkyard)
t.create_deferred_card("WvBjy5ml", "Long Range Reminders",
    "Check NOAA for updated boundary layer parameterization paper",
    due_in_days=90)

# Re-evaluate a paused decision in 4 months (Junkyard)
t.create_deferred_card("WvBjy5ml", "Long Range Reminders",
    "Revisit GPU rental service, check pricing again",
    due_in_days=120)

# Recurring maintenance reminder (Cat Farm, not subject to the rule)
t.create_deferred_card("lgbO7Vzo", "Consumables & Filters",
    "Replace Roborock filter",
    due_in_days=90)
```

---

## How cards resurface

Source: `trello_daily_summary.py` and `vpa_config.json` (`recurring_boards`).

- Daily List cards appear in "Cards Due Within 5 Days" when the due time is between now and 5 days ahead. Cards already past due are not in that section.
- Every board in `recurring_boards` is also scanned: incomplete cards due within 7 days or already overdue. This covers Daily List (all lists), Cat Farm (`Consumables & Filters` only), WeatherUSA (`Recurring Tasks` only), Junkyard (all lists), and SkyEye Camera Network (`Sites: Tracking/Pipeline` only).
- Lists named `Done*`, `Base Modes`, and `FAILURE MODES` are skipped. Do not defer into them.
- Boards and lists not in `recurring_boards` never surface (for example other SkyEye lists, including `Sites: Live`). Add them to the config before deferring there.
- A card keeps surfacing as overdue until its due date is marked complete (`t.mark_due_complete(card_id)`).
- The summary also prints "⚠️ Due Reminders That Won't Push" for open cards with a due date and a reminder where nobody watches the card, its list, or its board (`t.untracked_due_reminders()`). This catches cards made or edited outside `create_card`. Fix by watching the card, the list, or the board.
- So the first appearance is 5 days before due on Daily List, 7 days before due on every other board. A Daily List card shows in both sections.

---

## Notes

- Cards land at the bottom of the list (`pos="bottom"`, per the card position rule).
- Due notifications: Trello pushes the due reminder to members or watchers of the card, its list, or its board (any one works), and API-created cards have neither and no reminder. Any card created or updated with a due date through `create_card`, `update_card`, or `set_due` is watched (`subscribed=true`, no member added) and gets `dueReminder=0` if unset. An existing reminder, including -1, is kept. `t.ensure_due_alerts(card_id)` does this alone.
- Once created, the card is passive. Clear or complete the due date when the task is done.
- Do not modify existing cards on satellite boards (Junkyard included) unless the user asks. Creating a new deferred card there at their request is fine.
- To defer a card already on the board, compute the date with `t.compute_due(days)` and set it with `t.set_due(card_id, iso)`. If it is 30 days or less, `t.move_card(card_id, backlog_list_id)`. If longer, it must move to Junkyard / Long Range Reminders. A cross-board move needs `idBoard` plus `idList` in `t.update_card(...)`; this has not been tested, so confirm with the user or move it by hand.
- A deferred card whose date keeps getting pushed is a signal to commit or delete it.

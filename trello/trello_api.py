#!/usr/bin/env python3
"""
Trello API helper library.

Provides functions for common read/write operations against the Trello REST API.
Intended for use in interactive sessions (Claude Code) and as an import for other scripts.

Required env vars:
  TRELLO_API_KEY
  TRELLO_TOKEN

Usage as a CLI tool:
  python trello_api.py boards
  python trello_api.py lists <boardId>
  python trello_api.py cards <listId>
  python trello_api.py search <query>
  python trello_api.py card <cardId>
"""

import os
import sys
import json
import requests
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

BASE = "https://api.trello.com/1"
AUTH = {
    "key": os.environ["TRELLO_API_KEY"],
    "token": os.environ["TRELLO_TOKEN"],
}

# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _get(path: str, **params) -> any:
    r = requests.get(f"{BASE}{path}", params={**AUTH, **params})
    r.raise_for_status()
    return r.json()

def _post(path: str, data: dict) -> any:
    r = requests.post(f"{BASE}{path}", params=AUTH, data=data)
    r.raise_for_status()
    return r.json()

def _put(path: str, data: dict) -> any:
    r = requests.put(f"{BASE}{path}", params=AUTH, data=data)
    r.raise_for_status()
    return r.json()

# ---------------------------------------------------------------------------
# ID Lookups
# ---------------------------------------------------------------------------

def get_boards(fields="name,id,shortLink") -> list[dict]:
    """Return all boards for the authenticated user."""
    return _get("/members/me/boards", fields=fields)

def get_lists(board_id: str) -> list[dict]:
    """Return all lists for a board."""
    return _get(f"/boards/{board_id}/lists")

def get_list_id(board_id: str, list_name: str) -> str | None:
    """Return the ID of a list by name (case-insensitive). Returns None if not found."""
    for lst in get_lists(board_id):
        if lst["name"].strip().lower() == list_name.strip().lower():
            return lst["id"]
    return None

def get_board_id(board_name: str) -> str | None:
    """Return the ID of a board by name (case-insensitive). Returns None if not found."""
    for board in get_boards():
        if board["name"].strip().lower() == board_name.strip().lower():
            return board["id"]
    return None

def get_labels(board_id: str) -> list[dict]:
    """Return all labels for a board."""
    return _get(f"/boards/{board_id}/labels")

def get_label_id(board_id: str, label_name: str) -> str | None:
    """Return a label ID by name (case-insensitive)."""
    for label in get_labels(board_id):
        if (label.get("name") or "").strip().lower() == label_name.strip().lower():
            return label["id"]
    return None

# ---------------------------------------------------------------------------
# READ: Cards
# ---------------------------------------------------------------------------

def get_cards_in_list(list_id: str, fields="name,id,due,desc,idChecklists") -> list[dict]:
    """Return all open cards in a list."""
    return _get(f"/lists/{list_id}/cards", fields=fields)

def get_cards_on_board(board_id: str) -> list[dict]:
    """Return all open cards on a board, each annotated with _list_name and _list_id."""
    lists = _get(f"/boards/{board_id}/lists", cards="open", card_fields="name,id,due,desc")
    cards = []
    for lst in lists:
        for card in lst.get("cards", []):
            cards.append({**card, "_list_name": lst["name"], "_list_id": lst["id"]})
    return cards

def get_card(card_id: str) -> dict:
    """Return a single card by ID."""
    return _get(f"/cards/{card_id}")

def resolve_board_id(board_id: str) -> str:
    """
    Resolve a board_id to a full 24-char Trello ID.
    Accepts: full ID, short ID (8 chars from URL), or board name (case-insensitive).
    Raises ValueError if no match found.
    """
    if len(board_id) == 24:
        return board_id
    boards = get_boards()
    # Match by short ID (shortLink) or name
    for b in boards:
        if b.get("shortLink") == board_id or b["name"].strip().lower() == board_id.strip().lower():
            return b["id"]
    raise ValueError(f"Could not resolve board '{board_id}' to a full ID")

def search_cards(query: str, board_id: str | None = None) -> list[dict]:
    """
    Search for cards by name/text. Optionally scope to a single board.
    Accepts full board ID, short board ID, or board name for board_id.
    """
    params = {"query": query, "modelTypes": "cards", "card_fields": "name,id,idList,due,desc"}
    if board_id:
        params["idBoards"] = resolve_board_id(board_id)
    results = _get("/search", **params)
    return results.get("cards", [])

def find_card(query: str, board_id: str | None = None) -> dict | None:
    """
    Return the first card matching a search query, or None.
    Accepts full board ID, short board ID, or board name for board_id.
    Prefers exact name matches over partial matches.
    """
    cards = search_cards(query, board_id=board_id)
    if not cards:
        return None
    exact = [c for c in cards if c["name"].strip().lower() == query.strip().lower()]
    return exact[0] if exact else cards[0]

# ---------------------------------------------------------------------------
# READ: Checklists
# ---------------------------------------------------------------------------

def get_checklists(card_id: str) -> list[dict]:
    """Return all checklists on a card."""
    return _get(f"/cards/{card_id}/checklists")

def get_checklist_id(card_id: str, checklist_name: str) -> str | None:
    """Return a checklist ID by name (case-insensitive)."""
    for cl in get_checklists(card_id):
        if cl["name"].strip().lower() == checklist_name.strip().lower():
            return cl["id"]
    return None

# ---------------------------------------------------------------------------
# READ: Actions
# ---------------------------------------------------------------------------

def get_recent_actions(board_id: str, since_hours: int = 48, filters: str | None = None, limit: int = 100) -> list[dict]:
    """Return recent board actions."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
    params = {"since": since, "limit": limit}
    if filters:
        params["filter"] = filters
    return _get(f"/boards/{board_id}/actions", **params)

# ---------------------------------------------------------------------------
# WRITE: Cards
# ---------------------------------------------------------------------------

DEFAULT_DUE_REMINDER = 0                   # minutes before due; 0 = push at the due time

def ensure_due_alerts(card_id: str) -> dict:
    """
    Make sure a dated card will push a due notification: watch the card (no member added)
    and set dueReminder to DEFAULT_DUE_REMINDER only when it is unset. An existing reminder,
    including -1 (explicitly none), is left as is. Returns {subscribed, dueReminder}.
    """
    card = _get(f"/cards/{card_id}", fields="subscribed,dueReminder")
    data = {}
    if not card.get("subscribed"):
        data["subscribed"] = "true"
    if card.get("dueReminder") is None:
        data["dueReminder"] = DEFAULT_DUE_REMINDER
    if data:
        card = _put(f"/cards/{card_id}", data)
    return {"subscribed": card.get("subscribed"), "dueReminder": card.get("dueReminder")}

def untracked_due_reminders(board_id: str, list_filter: str | None = None, skip_lists: set | None = None) -> list[dict]:
    """
    Find open cards with a due date and a reminder that Trello will never push, because
    nobody watches the card, its list, or its board (tested 2026-10-18: any one is enough).
    A watched board returns [] without scanning lists.

    board_id: (required) full ID, short ID, or board name
    list_filter: (optional) only check this list name, case-insensitive
    skip_lists: (optional) lowercase list names to skip; lists named Done* are always skipped
    Returns card dicts with an added "_list_name".
    """
    skip = skip_lists or set()
    if _get(f"/boards/{board_id}", fields="subscribed").get("subscribed"):
        return []
    lists = _get(
        f"/boards/{board_id}/lists",
        fields="name,subscribed",
        cards="open",
        card_fields="name,due,dueComplete,dueReminder,subscribed,shortUrl",
    )
    hits = []
    for lst in lists:
        lname = lst["name"].strip().lower()
        if lname.startswith("done") or lname in skip:
            continue
        if list_filter and lname != list_filter.strip().lower():
            continue
        if lst.get("subscribed"):
            continue
        for card in lst.get("cards", []):
            reminder = card.get("dueReminder")
            if not card.get("due") or card.get("dueComplete") or card.get("subscribed"):
                continue
            if reminder is None or reminder == -1:
                continue
            hits.append({**card, "_list_name": lst["name"]})
    return hits

def create_card(list_id: str, name: str, desc: str = "", due: str = "", pos: str = "bottom") -> dict:
    """
    Create a card in a list. Returns the created card.
    due: ISO 8601 string e.g. "2026-04-01T17:00:00Z", or "" to omit.
        With a due date the card is also watched and gets dueReminder 0.
    pos: "top", "bottom", or a float.
    """
    data = {"idList": list_id, "name": name, "pos": pos}
    if desc:
        data["desc"] = desc
    if due:
        data["due"] = due
    card = _post("/cards", data)
    if due:
        card.update(ensure_due_alerts(card["id"]))
    return card

def move_card(card_id: str, list_id: str, pos: str = "bottom") -> dict:
    """Move a card to a different list."""
    return _put(f"/cards/{card_id}", {"idList": list_id, "pos": pos})

def update_card(card_id: str, **fields) -> dict:
    """
    Update arbitrary card fields. Common fields:
      name, desc, due (ISO string or ""), dueComplete (bool str), closed (bool str)
    Setting a non-empty due also runs ensure_due_alerts().
    """
    card = _put(f"/cards/{card_id}", fields)
    if fields.get("due"):
        card.update(ensure_due_alerts(card_id))
    return card

def archive_card(card_id: str) -> dict:
    """Archive (close) a card."""
    return _put(f"/cards/{card_id}", {"closed": "true"})

def set_due(card_id: str, due_iso: str) -> dict:
    """Set a due date. Pass "" to clear. A new date also runs ensure_due_alerts()."""
    card = _put(f"/cards/{card_id}", {"due": due_iso})
    if due_iso:
        card.update(ensure_due_alerts(card_id))
    return card

def mark_due_complete(card_id: str, complete: bool = True) -> dict:
    """Mark a card's due date as complete or incomplete."""
    return _put(f"/cards/{card_id}", {"dueComplete": "true" if complete else "false"})

def add_comment(card_id: str, text: str) -> dict:
    """Add a comment to a card."""
    return _post(f"/cards/{card_id}/actions/comments", {"text": text})

def add_label(card_id: str, label_id: str) -> dict:
    """Add a label to a card by label ID."""
    return _post(f"/cards/{card_id}/idLabels", {"value": label_id})

# ---------------------------------------------------------------------------
# WRITE: Checklists
# ---------------------------------------------------------------------------

def create_checklist(card_id: str, name: str) -> dict:
    """Add a new checklist to a card. Returns the checklist."""
    return _post("/checklists", {"idCard": card_id, "name": name})

def add_checklist_item(checklist_id: str, name: str, checked: bool = False, pos: str = "bottom") -> dict:
    """Add an item to a checklist."""
    return _post(f"/checklists/{checklist_id}/checkItems", {
        "name": name,
        "checked": "true" if checked else "false",
        "pos": pos,
    })

def set_checklist_item_state(card_id: str, checkitem_id: str, complete: bool) -> dict:
    """Check or uncheck a checklist item."""
    return _put(f"/cards/{card_id}/checkItem/{checkitem_id}", {
        "state": "complete" if complete else "incomplete"
    })

# ---------------------------------------------------------------------------
# WRITE: Lists & Boards
# ---------------------------------------------------------------------------

def create_list(board_id: str, name: str, pos: str = "bottom") -> dict:
    """Create a new list on a board."""
    return _post("/lists", {"idBoard": board_id, "name": name, "pos": pos})

# ---------------------------------------------------------------------------
# WRITE: Deferred cards
# ---------------------------------------------------------------------------

DEFAULT_DUE_HOUR_UTC = 18                  # 1800Z = 11:00 PDT / 10:00 PST
LOCAL_DUE_HOUR = 12                        # used by local_noon=True
DAILY_LIST_BOARD = "3YBA5AXK"              # Daily List
JUNKYARD_BOARD = "WvBjy5ml"                # long-term defers
JUNKYARD_DEFAULT_LIST = "Long Range Reminders"
MAX_BACKLOG_DEFER_DAYS = 30                # <= 30 stays on Daily List, > 30 goes to Junkyard


@lru_cache(maxsize=None)
def _full_board_id(board_ref: str) -> str:
    """Resolve and cache a board reference (full ID, short ID, or name)."""
    return resolve_board_id(board_ref)


def _find_list_id(board_id: str, list_name: str) -> str | None:
    """
    Find a list by name, case-insensitive. Exact match first, then a unique
    prefix match so "Backlog" finds "Backlog 🔵". Returns None if missing or ambiguous.
    """
    lists = get_lists(board_id)
    want = list_name.strip().lower()
    for lst in lists:
        if lst["name"].strip().lower() == want:
            return lst["id"]
    hits = [l for l in lists if l["name"].strip().lower().startswith(want)]
    return hits[0]["id"] if len(hits) == 1 else None


def compute_due(
    due_in_days: int,
    local_noon: bool = False,
    tz: str | None = None,
    today: date | None = None,
) -> datetime:
    """
    Return the due instant as an aware UTC datetime.

    due_in_days: (required) whole days from today, >= 0
    local_noon: (optional) False = DEFAULT_DUE_HOUR_UTC on the target date,
        True = 12:00 wall-clock time in the local zone, converted to UTC
    tz: (optional) IANA name such as "America/Los_Angeles"; default is the system zone
    today: (optional) base date, for tests; default is today's date in the local zone

    The day count uses the local calendar date, so an evening request does not skip a day.
    Days are added as calendar days before attaching a zone, so DST shifts are handled.
    """
    if isinstance(due_in_days, bool) or not isinstance(due_in_days, int) or due_in_days < 0:
        raise ValueError(f"due_in_days must be a whole number >= 0, got {due_in_days!r}")
    zone = ZoneInfo(tz) if tz else None
    base = today or datetime.now(zone).date()
    target = base + timedelta(days=due_in_days)
    if local_noon:
        naive = datetime.combine(target, time(LOCAL_DUE_HOUR))
        # naive.astimezone() reads the system zone rules for that date, so DST is applied
        aware = naive.replace(tzinfo=zone) if zone else naive.astimezone()
        return aware.astimezone(timezone.utc)
    return datetime.combine(target, time(DEFAULT_DUE_HOUR_UTC), tzinfo=timezone.utc)


def create_deferred_card(
    board_id: str,
    list_name: str,
    title: str,
    due_in_days: int,
    description: str | None = None,
    local_noon: bool = False,
    tz: str | None = None,
) -> dict:
    """
    Create a card in a named list with a future due date, placed at the bottom.

    board_id: (required) full ID, short ID, or board name
    list_name: (required) list name, case-insensitive; a unique prefix also matches
    title: (required) card title, action-oriented
    due_in_days: (required) whole days from today until the card should resurface
    description: (optional) context, links, or notes for the card body
    local_noon: (optional) due at 12:00 local time instead of DEFAULT_DUE_HOUR_UTC
    tz: (optional) IANA zone for local_noon; default is the system zone

    30 day rule: on the Daily List board, due_in_days > MAX_BACKLOG_DEFER_DAYS is
    redirected to the Junkyard board, list JUNKYARD_DEFAULT_LIST (list_name is ignored).
    Exactly 30 days stays on the Daily List. Other boards are never redirected, and a
    caller who names the Junkyard board directly gets the list they asked for.

    Returns the Trello card dict including shortUrl. If the card was redirected, the
    dict also has "_defer_note" describing the move; report it to the user.
    """
    due_dt = compute_due(due_in_days, local_noon=local_noon, tz=tz)
    resolved = _full_board_id(board_id)
    note = None
    if due_in_days > MAX_BACKLOG_DEFER_DAYS and resolved == _full_board_id(DAILY_LIST_BOARD):
        resolved = _full_board_id(JUNKYARD_BOARD)
        note = (
            f"Redirected to Junkyard / {JUNKYARD_DEFAULT_LIST}: {due_in_days} days is over "
            f"the {MAX_BACKLOG_DEFER_DAYS} day limit for the Daily List (asked for '{list_name}')."
        )
        list_name = JUNKYARD_DEFAULT_LIST
    list_id = _find_list_id(resolved, list_name)
    if not list_id:
        raise ValueError(f"List '{list_name}' not found (or ambiguous) on board '{board_id}'")
    card = create_card(
        list_id=list_id,
        name=title,
        desc=description or "",
        due=due_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        pos="bottom",
    )
    if note:
        card["_defer_note"] = note
    return card

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print(data):
    print(json.dumps(data, indent=2))

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(0)

    cmd = args[0]

    if cmd == "boards":
        _print([{"id": b["id"], "name": b["name"]} for b in get_boards()])

    elif cmd == "lists" and len(args) == 2:
        _print([{"id": l["id"], "name": l["name"]} for l in get_lists(args[1])])

    elif cmd == "cards" and len(args) == 2:
        _print(get_cards_in_list(args[1]))

    elif cmd == "search" and len(args) >= 2:
        board = args[2] if len(args) == 3 else None
        _print(search_cards(" ".join(args[1:2]), board_id=board))

    elif cmd == "card" and len(args) == 2:
        _print(get_card(args[1]))

    else:
        print(__doc__)
        sys.exit(1)

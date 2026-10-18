#!/usr/bin/env python3
"""
Trello → Claude Sonnet 4.6: Daily board summary + suggested work items.

Required env vars:
  ANTHROPIC_API_KEY   — console.anthropic.com
  TRELLO_API_KEY      — trello.com/power-ups/admin
  TRELLO_TOKEN        — trello.com/power-ups/admin

Config file (JSON, -c/--config):
  model             — Claude model ID
  primary_board     — Trello short board ID (replaces TRELLO_BOARD_ID env var)
  ignored_lists     — list of list names to skip
  recurring_boards  — list of {id, label, list_filter} dicts

  Default config path: $VPA_DIR/vpa_config.json (VPA_DIR defaults to ~/vpa)

Install deps:
  pip install anthropic requests

Usage:
  python trello_daily_summary.py                           # print context to stdout
  python trello_daily_summary.py --claude-api              # call Anthropic API directly
  python trello_daily_summary.py -c /path/to/config.json  # use alternate config
"""

import os
import sys
import json
import argparse
import anthropic
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'trello'))
import trello_api as trello

# --- Config ---

DEFAULT_CONFIG = os.path.join(os.environ.get("VPA_DIR", os.path.expanduser("~/vpa")), "vpa_config.json")

def load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)

def _is_ignored_list(name: str, ignored: set) -> bool:
    n = name.strip().lower()
    return n.startswith("done") or n in ignored

# --- Trello ---

DETAIL_LISTS = {"today", "paused stack"}

def get_board_lists(board_id: str):
    return trello._get(
        f"/boards/{board_id}/lists",
        cards="open",
        card_fields="name,due,dueComplete,desc",
        card_checklists="all",
    )

def _desc_snippet(desc: str) -> list[str]:
    """Extract NEXT/CONTEXT lines from an Active Mode Task card, or fall back to first 2 non-empty lines."""
    if not desc or not desc.strip():
        return []
    lines = [l.strip() for l in desc.splitlines()]
    tagged = [l for l in lines if l.lower().startswith(("next:", "context:"))]
    if tagged:
        return [l[:140] for l in tagged]
    non_empty = [l for l in lines if l][:2]
    return [l[:140] for l in non_empty]

def get_recurring_cards(board_id: str, list_filter: str | None, ignored: set) -> list[dict]:
    """Return incomplete cards that are past due or due within 7 days.
    Optionally scoped to a specific list name. Ignores Done/ignored lists."""
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=7)
    lists = trello._get(f"/boards/{board_id}/lists", cards="open", card_fields="name,due,dueComplete")
    cards = []
    for lst in lists:
        if _is_ignored_list(lst["name"], ignored):
            continue
        if list_filter and lst["name"].strip().lower() != list_filter.lower():
            continue
        for card in lst.get("cards", []):
            if card.get("dueComplete") or not card.get("due"):
                continue
            due_dt = datetime.fromisoformat(card["due"].replace("Z", "+00:00"))
            if due_dt <= cutoff:
                cards.append({**card, "_list_name": lst["name"], "_due_dt": due_dt})
    return cards

def get_recent_actions(board_id: str, since_hours=48):
    return trello.get_recent_actions(
        board_id,
        since_hours=since_hours,
        filters="updateCard,createCard,moveCardToBoard,commentCard,addChecklistToCard,createCheckItem,updateCheckItemStateOnCard",
    )



def format_action(a) -> str | None:
    who = a["memberCreator"]["fullName"]
    data = a.get("data", {})
    card_name = data.get("card", {}).get("name", "")
    atype = a["type"]

    if atype == "commentCard":
        text = data.get("text", "")
        snippet = text[:80].replace("\n", " ") + ("…" if len(text) > 80 else "")
        return f"  {who} commented on '{card_name}': {snippet}"

    if atype == "createCard":
        list_name = data.get("list", {}).get("name", "")
        return f"  {who} created '{card_name}' in [{list_name}]"

    if atype == "moveCardToBoard":
        return f"  {who} moved '{card_name}' to this board"

    if atype == "updateCard":
        old = data.get("old", {})
        if "listBefore" in data and "listAfter" in data:
            before = data["listBefore"]["name"]
            after = data["listAfter"]["name"]
            return f"  {who} moved '{card_name}': [{before}] → [{after}]"
        if "due" in old:
            new_due_raw = data.get("card", {}).get("due")
            if new_due_raw:
                new_due = datetime.fromisoformat(new_due_raw.replace("Z", "+00:00")).strftime("%b %d")
                return f"  {who} set due date on '{card_name}' → {new_due}"
            else:
                return f"  {who} removed due date from '{card_name}'"

    if atype == "addChecklistToCard":
        checklist_name = data.get("checklist", {}).get("name", "")
        return f"  {who} added checklist '{checklist_name}' to '{card_name}'"

    if atype == "createCheckItem":
        checklist_name = data.get("checklist", {}).get("name", "")
        item_name = data.get("checkItem", {}).get("name", "")
        return f"  {who} added item '{item_name}' to checklist '{checklist_name}' on '{card_name}'"

    if atype == "updateCheckItemStateOnCard":
        item_name = data.get("checkItem", {}).get("name", "")
        state = data.get("checkItem", {}).get("state", "")
        symbol = "☑" if state == "complete" else "☐"
        return f"  {who} {symbol} '{item_name}' on '{card_name}'"

    return None

# --- Prompt context ---

def build_context(cfg: dict, since_hours: int = 48):
    ignored = set(s.lower() for s in cfg.get("ignored_lists", []))
    board_id = cfg["primary_board"]
    recurring_boards = cfg.get("recurring_boards", [])

    lists = get_board_lists(board_id)
    actions = get_recent_actions(board_id, since_hours=since_hours)

    now = datetime.now(timezone.utc)
    due_cutoff = now + timedelta(days=5)

    board_lines = []
    due_soon = []
    for lst in lists:
        if _is_ignored_list(lst["name"], ignored):
            continue
        cards = lst.get("cards", [])
        board_lines.append(f"  [{lst['name']}]:")
        show_desc = any(key in lst["name"].lower() for key in DETAIL_LISTS)
        if not cards:
            board_lines.append("    (empty)")
        for card in cards:
            checklists = card.get("checklists", [])
            incomplete_items = [
                item["name"]
                for cl in checklists
                for item in cl.get("checkItems", [])
                if item.get("state") != "complete"
            ]
            checklist_summary = ""
            if checklists:
                total = sum(len(cl.get("checkItems", [])) for cl in checklists)
                done = total - len(incomplete_items)
                checklist_summary = f" [{done}/{total} checklist items done]"
            board_lines.append(f"    - {card['name']}{checklist_summary}")
            if show_desc:
                for snippet in _desc_snippet(card.get("desc", "")):
                    board_lines.append(f"      ↳ {snippet}")
            for item in incomplete_items:
                board_lines.append(f"      ☐ {item}")
            if card.get("due") and not card.get("dueComplete"):
                due_dt = datetime.fromisoformat(card["due"].replace("Z", "+00:00"))
                if now <= due_dt <= due_cutoff:
                    days_left = (due_dt.date() - now.date()).days
                    label = "today" if days_left == 0 else f"in {days_left}d"
                    due_soon.append(f"  '{card['name']}' [{lst['name']}] — due {due_dt.strftime('%b %d')} ({label})")

    # --- Recurring items from other boards ---
    recurring_lines = []
    for board in recurring_boards:
        cards = get_recurring_cards(board["id"], board.get("list_filter"), ignored)
        if not cards:
            continue
        source = board["label"]
        if board["list_filter"]:
            source += f" / {board['list_filter']}"
        recurring_lines.append(f"  [{source}]:")
        for card in cards:
            due_dt = card["_due_dt"]
            days_left = (due_dt.date() - now.date()).days
            if days_left < 0:
                due_str = f" — overdue ({due_dt.strftime('%b %d')})"
            elif days_left == 0:
                due_str = " — due today"
            else:
                due_str = f" — due {due_dt.strftime('%b %d')} (in {days_left}d)"
            recurring_lines.append(f"    - {card['name']}{due_str}")

    # --- Due reminders Trello will not push (card and list both unwatched) ---
    untracked_lines = []
    seen = set()
    scans = [(board_id, "Daily List", None)] + [(b["id"], b["label"], b.get("list_filter")) for b in recurring_boards]
    for scan_id, label, list_filter in scans:
        for card in trello.untracked_due_reminders(scan_id, list_filter, ignored):
            if card["id"] in seen:
                continue
            seen.add(card["id"])
            due_str = datetime.fromisoformat(card["due"].replace("Z", "+00:00")).strftime("%b %d")
            untracked_lines.append(f"  - {card['name']} [{label} / {card['_list_name']}] due {due_str} {card['shortUrl']}")

    action_lines = []
    for a in actions:
        line = format_action(a)
        if line:
            action_lines.append(line)

    untracked_block = ""
    if untracked_lines:
        untracked_block = (
            "## ⚠️ Due Reminders That Won't Push (watch the card or its list):\n"
            f"{chr(10).join(untracked_lines)}\n\n"
        )

    due_section = "\n".join(due_soon) if due_soon else "  (none)"
    recurring_section = "\n".join(recurring_lines) if recurring_lines else "  (none)"

    now_local = datetime.now()
    today = now_local.strftime("%A, %B %d, %Y @ %H:%M")
    weekday = now_local.strftime("%A")

    ritual_lines = {
        "Friday": (
            "⚡ FRIDAY — Extended Close due\n"
            "  Compress Active to ≤2 cards. Compress Paused Stack to ≤4.\n"
            "  Quick skim Parking Lot — delete obvious junk.\n"
        ),
        "Sunday": (
            "⚡ SUNDAY — Weekly Intake Review\n"
            "  Heavy review: Parking Lot + Paused Stack + light Backlog scan.\n"
            "  Parking Lot 7–14 days: decide today. ≥14 days: must exit (Paused Stack, Backlog, Junkyard, or delete).\n"
        ),
        "Monday": (
            "⚡ MONDAY — Weekly Intake Review (if not done Sunday)\n"
            "  Same as Sunday review — Parking Lot + Paused Stack + light Backlog scan.\n"
        ),
    }
    ritual_block = f"{ritual_lines[weekday]}\n" if weekday in ritual_lines else ""

    if weekday in cfg.get("project_review_days", []):
        ritual_block += (
            "🔎 PROJECT REVIEW day\n"
            "  Run /project-review. Shows what is waiting on you in GitHub, registry repo status,\n"
            "  and uncommitted or unpushed work in local clones. Offer to run it after triage.\n\n"
        )

    return (
        f"The time is {today}.\n\n"
        f"{ritual_block}"
        f"## Current Board State (open cards by list):\n"
        f"{chr(10).join(board_lines)}\n\n"
        f"## Cards Due Within 5 Days:\n"
        f"{due_section}\n\n"
        f"## Recurring Items (Other Boards):\n"
        f"{recurring_section}\n\n"
        f"{untracked_block}"
        f"## Recent Activity (last {since_hours}h):\n"
        f"{chr(10).join(action_lines) or '  (none)'}"
    )

# --- Claude ---

def daily_summary(context: str, model: str) -> str:
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": (
                    "You are a personal productivity assistant. "
                    "Based on this Trello board snapshot and recent activity, provide:\n"
                    "1. A brief summary of what changed recently\n"
                    "2. A suggested Daily Task List for today (5–8 items, prioritized)\n"
                    "3. Any blockers or stale items worth flagging\n\n"
                    f"{context}"
                ),
            }
        ],
    )
    return message.content[0].text

# --- Main ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude-api", action="store_true", help="Call Anthropic API directly instead of printing context")
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG, help=f"Path to JSON config file (default: {DEFAULT_CONFIG})")
    parser.add_argument("--days", type=int, default=2, metavar="N", help="Recent activity window in days (default: 2)")
    args = parser.parse_args()

    cfg = load_config(args.config)
    context = build_context(cfg, since_hours=args.days * 24)

    if args.claude_api:
        print("Fetching Trello data...")
        print(f"\n--- Sending to {cfg.get('model', 'claude-sonnet-4-6')} ---\n")
        print(daily_summary(context, cfg.get("model", "claude-sonnet-4-6")))
    else:
        print(context)

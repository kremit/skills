---
name: trello
description: Read and write to Trello boards, lists, cards, checklists, and comments via the REST API. Use this skill when the user asks to create, move, update, archive, or comment on Trello cards, or to inspect board/list state. Includes Trello Inbox (global capture) read access.
homepage: https://developer.atlassian.com/cloud/trello/rest/
---

# Trello Skill

Full read/write access to Trello via the REST API.

## Environment

```bash
export TRELLO_API_KEY="your-api-key"   # https://trello.com/app-key
export TRELLO_TOKEN="your-token"       # click "Token" on the same page
```

---

## Trello Inbox (Global Capture)

The Trello Inbox is a special hidden board. It is not returned by `members/me/boards`, but accessible via the member's `inbox` field in `members/me`.

Slash command: `/trello inbox` — reads and summarizes current Inbox cards.

### Discover your Inbox list ID

```bash
source ~/.bash_profile
curl -s "https://api.trello.com/1/members/me?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&fields=inbox" | jq '.inbox'
# Returns: { idBoard, idList, idOrganization }
```

### Read all cards in the Inbox

```bash
source ~/.bash_profile
INBOX_LIST="{idList from inbox field}"
curl -s "https://api.trello.com/1/lists/$INBOX_LIST/cards?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&fields=name,id,desc,due,dateLastActivity" \
  | jq '.[] | {id, name, desc, due, updated: .dateLastActivity}'
```

### Move an Inbox card to another list

```bash
source ~/.bash_profile
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "idList={targetListId}" \
  -d "pos=bottom"
```

---

## READ Operations

### List all boards

```bash
curl -s "https://api.trello.com/1/members/me/boards?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&fields=name,id,shortLink" | jq '.[] | {id, name}'
```

### List lists in a board

```bash
curl -s "https://api.trello.com/1/boards/{boardId}/lists?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" | jq '.[] | {id, name}'
```

### List open cards in a list

```bash
curl -s "https://api.trello.com/1/lists/{listId}/cards?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&fields=name,id,due,desc" | jq '.[] | {id, name, due}'
```

### Get all open cards on a board (with list context)

```bash
curl -s "https://api.trello.com/1/boards/{boardId}/lists?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&cards=open&card_fields=name,id,due" | jq '.[] | {list: .name, cards: [.cards[].name]}'
```

### Get a single card

```bash
curl -s "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" | jq '{id, name, desc, due, idList}'
```

### Search for cards by name

```bash
curl -s "https://api.trello.com/1/search?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&query={term}&modelTypes=cards&card_fields=name,id,idList" | jq '.cards[] | {id, name}'
```

### Get checklists on a card

```bash
curl -s "https://api.trello.com/1/cards/{cardId}/checklists?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" | jq '.[] | {name, items: [.checkItems[] | {name, state}]}'
```

### Get recent actions on a board

```bash
curl -s "https://api.trello.com/1/boards/{boardId}/actions?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&filter=updateCard,createCard,commentCard&limit=50" | jq '.[] | {type, card: .data.card.name, who: .memberCreator.fullName}'
```

---

## WRITE Operations

### Create a card

```bash
curl -s -X POST "https://api.trello.com/1/cards?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "idList={listId}" \
  -d "name=Card title" \
  -d "desc=Optional description" \
  -d "due=2026-04-01T17:00:00Z" \
  -d "pos=bottom" | jq '{id, name, shortUrl}'
```

### Move a card to another list

```bash
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "idList={newListId}" \
  -d "pos=bottom"
```

### Update card name or description

```bash
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "name=New title" \
  -d "desc=Updated description"
```

### Set or clear a due date

```bash
# Set
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" -d "due=2026-04-15T17:00:00Z"
# Clear
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" -d "due="
# After setting a due date: watch the card and push at due time (only set dueReminder if it is null)
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" -d "subscribed=true" -d "dueReminder=0"
```

Trello pushes due reminders to members or watchers of the card, its list, or its board; any one is enough (tested 2026-10-18). API-created cards have no reminder set. `trello_api.py` handles this automatically in `create_card`, `update_card` and `set_due` via `ensure_due_alerts()`; it never adds a member.

**Warning check:** `untracked_due_reminders(board_id, list_filter=None)` returns open cards that have a due date and a reminder but whose card, list, and board are all unwatched, so Trello never pushes the reminder. The daily summary runs it on the primary and recurring boards and lists hits under "⚠️ Due Reminders That Won't Push". When you read such a card back, warn the user and suggest watching the card, its list, or its board.

### Mark due date complete/incomplete

```bash
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" -d "dueComplete=true"
```

### Archive a card

```bash
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" -d "closed=true"
```

### Add a comment to a card

```bash
curl -s -X POST "https://api.trello.com/1/cards/{cardId}/actions/comments?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "text=Comment text here"
```

### Add a label to a card

```bash
# Get label IDs first
curl -s "https://api.trello.com/1/boards/{boardId}/labels?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" | jq '.[] | {id, name, color}'

# Apply label
curl -s -X POST "https://api.trello.com/1/cards/{cardId}/idLabels?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" -d "value={labelId}"
```

### Create a checklist on a card

```bash
curl -s -X POST "https://api.trello.com/1/checklists?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "idCard={cardId}" \
  -d "name=Checklist name" | jq '{id, name}'
```

### Add an item to a checklist

```bash
curl -s -X POST "https://api.trello.com/1/checklists/{checklistId}/checkItems?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "name=Item text" \
  -d "checked=false" \
  -d "pos=bottom"
```

### Check or uncheck a checklist item

```bash
# complete or incomplete
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}/checkItem/{checkItemId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "state=complete"
```

### Create a new list on a board

```bash
curl -s -X POST "https://api.trello.com/1/lists?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "name=List name" \
  -d "idBoard={boardId}" \
  -d "pos=bottom" | jq '{id, name}'
```

---

## Rate Limits

- 300 requests / 10s per API key
- 100 requests / 10s per token
- `/1/members` endpoints: 100 requests / 900s

## API Reference

Full docs: https://developer.atlassian.com/cloud/trello/rest/api-group-actions/

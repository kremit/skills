# Skill: Maildir Cleanup Workflow

## Context

The user manages a Dovecot Maildir and uses `mailsubjects.sh` to produce a
frequency-sorted subject/header list for bulk mail triage. The workflow is:

1. Run `mailsubjects.sh` to produce `subjects.txt` (or similar)
2. Share the output here for LLM-assisted regex generation
3. Use generated regexes in a manual filter in the mail client (Evolution or similar), 
   or in a find script against `Maildir/cur/`

## Output format to expect

```
  COUNT  [BADGES] Subject line text
```

- `[*]` = Flagged (attention - to add to Trello Intake, keep-candidate, or outdated), `[R]` = Replied (likely keep/sort)
- Blank subject lines are valid (empty `Subject:` header)
- Counts reflect duplicates across the mailbox

## Default triage workflow (unless user specifies otherwise)

Work through these phases in order:

1. **Cleanup**: identify patterns safe to bulk-delete:
   - Spam, newsletters, marketing/promotions
   - Automated notifications, cron output, system alerts
   - Social media notifications
   - 2FA/OTP codes
   - Duplicates and Recurring Mail (high count, identical or near-identical subjects)

2. **Prioritize**: flag for attention, especially last ~7 days:
   - `[*]` flagged mail: needs a decision (Trello intake, action, or delete)
   - `[R]` replied mail: likely keep or sort
   - Personal correspondence, important subjects with no [R], anything requiring follow-up

3. **Sort**: identify patterns worth moving to folders:
   - Receipts / order confirmations
   - Replies and threads
   - CS tickets and support responses
   - Any other clear category the user mentions
   - Folder names/structure are the user's concern — just identify the patterns

## Your job when given this output

The user will paste `subjects.txt` content or a file path. Unless they specify
a phase, work through all three above. Produce regexes suitable for `grep -E`
or Python `re` against raw `Subject:` header lines. Prefer anchored, specific
patterns. Group by category with a comment explaining what each targets.
Treat `[*]`/`[R]` lines as do-not-delete by default unless the user says otherwise.

## Default file names

| File | Purpose |
|---|---|
| `grep_subject.txt` | Raw `grep -r "^Subject:" Maildir/cur/` output (with filenames) |
| `grep_from.txt` | Raw `grep -r "^From:" Maildir/cur/` output |
| `sort_subject.txt` | Deduplicated, badge-annotated output from `mailsubjects.sh` |
| `search_subject.txt` | Normalized top-N regex patterns (LLM output) |
| `search_subject_simple.txt` | Simplified Evolution-compatible search terms |
| `triage.ods` | Spreadsheet generated from `search_subject_simple.txt` |

## Optional: triage spreadsheet

After producing `search_subject_simple.txt`, generate a checklist ODS with:

```bash
python3 make_triage_ods.py [search_subject_simple.txt] [triage.ods]
```

Columns: Mode | Category | Search Term | ~Count | Done | Notes

Open in LibreOffice Calc. Check off the **Done** column as each search is run
in Evolution. Use **Notes** to record exceptions or folder decisions. The
`~Count` column reflects approximate match counts from `sort_subject.txt` to
help prioritize high-volume searches first.

## Related files

- `mailsubjects.sh`: the extraction script
- `make_triage_ods.py`: spreadsheet generator
- `doc/mailsubjects.md`: full usage reference

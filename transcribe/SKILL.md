---
name: transcribe
description: Use when user invokes /transcribe to write a running chat transcript to a markdown file in the current project. Supports verbatim (exact messages) or summary (periodic summaries every ~20k tokens) modes.
argument-hint: "[verbatim|v | summary]"
allowed-tools: Write, Read, Edit, Bash
---

# Transcribe

Records the current chat session to a Markdown file in the project directory.

## On Activation

1. Parse argument: `verbatim` or `v` = verbatim mode; `summary` = summary mode; no arg defaults to verbatim
2. Pick the transcript directory `TDIR`, then `mkdir -p "$TDIR"`. Inside the skills repo it goes under the gitignored `tmp/`; anywhere else it is `transcripts/` in the current directory:

   ```bash
   SKILL_DIR=<directory containing this SKILL.md>
   REPO=$(git -C "$SKILL_DIR" rev-parse --show-toplevel 2>/dev/null)
   if [[ -n $REPO && $(git rev-parse --show-toplevel 2>/dev/null) == "$REPO" ]]; then TDIR=tmp/transcripts; else TDIR=transcripts; fi
   mkdir -p "$TDIR"
   ```

   Outside the skills repo, tell the user a new transcript is starting and that `transcripts/` is not gitignored there, so it is not committed by accident.
3. Generate filename: `$TDIR/YYYY-MM-DD-HHmmss.md` using current date/time
4. Write the file header (see below)
5. Confirm to user: `Transcribing this session to $TDIR/YYYY-MM-DD-HHmmss.md ([mode] mode).`
6. Immediately write the activating message + your confirmation as the first entry

## File Header

```markdown
# Chat Transcript
- **Date:** YYYY-MM-DD HH:MM
- **Mode:** verbatim | summary
- **Project:** /path/to/project

---
```

## Verbatim Mode

After every exchange for the rest of the session, append to the file:

```markdown
## User

[exact message, preserving code blocks and formatting]

## Claude

[exact response, preserving code blocks and formatting]

---
```

To append: use Bash with `cat >> "$TDIR"/filename.md << 'BLOCK'` ... `BLOCK` to safely append multi-line content without overwriting.

## Summary Mode

Track exchange count internally. After every **~20 exchanges** (a rough proxy for 20k tokens), append a summary block:

```markdown
## Summary — Exchanges N to M
_[timestamp]_

- [key point or decision]
- [key point or decision]
- ...

---
```

At session end (user says done/goodbye/exit, or explicitly asks for final summary), append:

```markdown
## Final Summary
_[timestamp]_

[3-5 paragraph or bulleted comprehensive summary covering all topics, decisions, and outcomes]

---
```

## Append Pattern

Use heredoc via Bash to append safely:

```bash
cat >> "$TDIR"/2025-01-01-120000.md << 'EOF'
## User

Message text here...

## Claude

Response text here...

---
EOF
```

## Important Notes

- Never overwrite the transcript file; always append
- Shell variables do not persist between Bash calls; resolve `$TDIR` once at activation and write the literal path in every later append
- In verbatim mode, include tool call descriptions and outputs where relevant
- If the user switches modes mid-session (e.g. `/transcribe summary`), close out the current transcript and start a new file
- Transcript files persist after the session and can be committed to git, but this is not the default, as sessions may include private or privileged content.

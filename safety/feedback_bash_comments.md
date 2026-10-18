---
name: No inline comments in Bash tool calls
description: Avoid # comments in Bash tool command strings; only use them in written script files
type: feedback
---

Do not put # comments inside Bash tool `command` strings (inline bash). They trigger Claude Code's permission prompt because they resemble validation bypass attempts (e.g. `--no-verify` style hacks).

**Why:** The Claude CLI flags inline # comment strings as potential hidden bypass attempts and asks for permission, interrupting flow unnecessarily.

**How to apply:** Only use # comments inside script files written with the Write tool (e.g. /tmp/myscript.py, /tmp/myscript.sh). For Bash tool calls, use the `description` field to explain the command instead of inline comments. If a multi-step bash command needs explanation, split it into separate Bash tool calls or write a script file first.

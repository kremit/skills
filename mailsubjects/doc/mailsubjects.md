# mailsubjects.sh

Extracts and deduplicates mail headers from a Dovecot Maildir, producing a
frequency-sorted list suitable for LLM-assisted regex generation.

## Usage

```
mailsubjects.sh [-f HEADER] [-i INPUT_FILE] [-o OUTPUT_FILE] [MAILDIR]
```

Searches `MAILDIR` (default: `Maildir/cur`) or a pre-grepped `-i FILE` (use
`-i -` for stdin). Defaults to `Subject:` header; pass `-f From` etc. for
others. Output goes to `subjects.txt` by default.

## Output format

```
  COUNT  [BADGES] VALUE
```

Badges (only present if flags set on the Maildir message file):
- `[*]` — Flagged (F)
- `[R]` — Replied (R)

## Notes

- Decodes RFC 2047 encoded-word headers (`=?utf-8?Q?...?=`, `=?...?B?...?=`)
- Parses Maildir `:2,FLAGS:` filename format correctly (colons in filenames
  don't confuse the header extractor)
- Dovecot filename flags decoded: F=Flagged, R=Replied, S=Seen, T=Trashed,
  D=Draft. Sender-set importance (`X-Priority`, `Importance` headers) is
  stored inside the message and requires a separate `-f` pass.

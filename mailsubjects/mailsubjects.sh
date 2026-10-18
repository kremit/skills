#!/bin/bash

HEADER="Subject"
INPUT=""
OUTPUT="subjects.txt"

usage() {
    cat <<EOF
Usage: $0 [-f HEADER] [-i INPUT_FILE] [-o OUTPUT_FILE] [MAILDIR]

  -f HEADER     Header to extract (default: Subject)
  -i FILE       Pre-grepped input file instead of searching MAILDIR
                (can also pipe via -i -)
  -o FILE       Output file (default: subjects.txt)
  MAILDIR       Directory to search (default: Maildir/cur)

NOTE: When pre-grepping, do NOT use grep -h — filenames must be preserved so
  the script can read Maildir :2,FLAGS: and emit [*] (Flagged) / [R] (Replied).
  grep -r "^Subject:" Maildir/cur/ > grep_subject.txt   # correct
  grep -rh "^Subject:" Maildir/cur/                     # WRONG: strips flags

Examples:
  $0
  $0 -f From
  $0 -i grep_subject.txt
  grep -r "^Subject:" ~/Maildir/cur | $0 -i -
EOF
    exit 1
}

[[ "$1" == "--help" ]] && usage

while getopts "f:i:o:h" opt; do
    case $opt in
        f) HEADER="$OPTARG" ;;
        i) INPUT="$OPTARG" ;;
        o) OUTPUT="$OPTARG" ;;
        h) usage ;;
        *) usage ;;
    esac
done
shift $((OPTIND - 1))

MAILDIR="${1:-Maildir/cur}"

# Build the input stream
if [[ -n "$INPUT" ]]; then
    if [[ "$INPUT" == "-" ]]; then
        RAW_INPUT="cat"           # read stdin
    else
        RAW_INPUT="cat \"$INPUT\""
    fi
    SOURCE_DESC="$INPUT"
else
    RAW_INPUT="grep -rh \"^${HEADER}:\" \"$MAILDIR\" 2>/dev/null"
    SOURCE_DESC="$MAILDIR"
fi

eval "$RAW_INPUT" \
  | python3 -c "
import sys, re
from email.header import decode_header, make_header

header = sys.argv[1]

# Matches Maildir filename lines: ...:2,FLAGS:Header: value
# Captures flags group and header value separately
maildir_re = re.compile(r':2,([A-Z]*):' + re.escape(header) + r': ?(.*)', re.IGNORECASE)
# Plain header line (grep -h output or pre-grepped without filename)
plain_re   = re.compile(r'^' + re.escape(header) + r': ?(.*)', re.IGNORECASE)

for line in sys.stdin:
    line = line.rstrip()
    flags = ''
    value = None

    m = maildir_re.search(line)
    if m:
        flags = m.group(1)
        value = m.group(2)
    else:
        m = plain_re.match(line)
        if m:
            value = m.group(1)

    if value is None:
        continue

    try:
        decoded = str(make_header(decode_header(value.strip())))
    except Exception:
        decoded = value.strip()

    badges = (''.join(['[*]' if 'F' in flags else '', '[R]' if 'R' in flags else '']))
    if badges:
        decoded = badges + ' ' + decoded

    print(decoded)
" "$HEADER" \
  | sort \
  | uniq -c \
  | sort -rn \
  | awk '{count=$1; $1=""; sub(/^ /, ""); printf "%6d  %s\n", count, $0}' \
  > "$OUTPUT"

echo "$(wc -l < "$OUTPUT") unique ${HEADER}: values written to $OUTPUT"

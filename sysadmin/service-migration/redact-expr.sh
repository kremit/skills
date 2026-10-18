#!/usr/bin/env bash
# redact-expr.sh: print a sed -E script that blanks flagged credential values in one config file.
# usage: redact-expr.sh <discover.json> <file> [view|replay]
#   view: values become <REDACTED>; replay: values become CHANGEME-dryrun
# run the output on the source: ssh src "sudo sed -E -f /dev/stdin <file>" < script
set -euo pipefail

usage='usage: redact-expr.sh <discover.json> <file> [view|replay]'
json=${1:?$usage}
file=${2:?$usage}
mode=${3:-view}
case $mode in
	view) val='<REDACTED>' ;;
	replay) val='CHANGEME-dryrun' ;;
	*) echo "$usage" >&2; exit 64 ;;
esac

q="'"
# keyword = value anywhere on a line; the value stops at space, quote, ; or &
kw_assign="s/([a-z0-9_.-]*(pass(word|wd|phrase)?|secret|token|api[_-]?key|credentials?|masterauth|authorization|bearer)[a-z0-9_.-]*[[:space:]]*[=:][[:space:]]*)[^[:space:]\"$q;&]+/\\1$val/Ig"
query="s/([?&][^=&[:space:]]*(pass|secret|token|key|auth)[^=&[:space:]]*=)[^&[:space:]\"$q]*/\\1$val/Ig"

value_block() { # <line> <prefix group>: double-quoted, single-quoted, to a trailing ;, to end of line
	printf '%s{s/%s"[^"]*"/\\1"%s"/;t\n' "$1" "$2" "$val"
	printf 's/%s%s[^%s]*%s/\\1%s%s%s/;t\n' "$2" "$q" "$q" "$q" "$q" "$val" "$q"
	printf 's/%s.*;[[:space:]]*$/\\1%s;/;t\n' "$2" "$val"
	printf 's/%s.*$/\\1%s/\n}\n' "$2" "$val"
}

jq -r --arg f "$file" '.references.credentials[] | select(.file == $f) | "\(.line) \(.key)"' "$json" |
	while read -r line key; do
		case $key in
			url-userinfo)
				printf '%s{s|(://)[^?#[:space:]]*@|\\1dryrun:%s@|g\n%s\n%s\n}\n' "$line" "$val" "$query" "$kw_assign"
				;;
			'(unparsed)')
				value_block "$line" '^([[:space:]]*[^[:space:]=:]+[[:space:]=:]+)'
				;;
			*)
				k=$(sed 's/[.[\*^$/]/\\&/g' <<<"$key")
				value_block "$line" "($k[[:space:]]*[=:]?[[:space:]]*)"
				;;
		esac
	done

# view only: catches flagged values whose line numbers went stale
[[ $mode == view ]] && printf '%s\n' "$kw_assign"
exit 0

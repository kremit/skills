#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
run_discover fakesvc > "$tmp/d.json"
root="$TESTS_DIR/fixtures/root"

redacted() { # <file> <mode>
	sed -E -f <(bash "$SKILL_DIR/redact-expr.sh" "$tmp/d.json" "$1" "$2") "$root$1"
}

v=$(redacted /etc/fakesvc/fakesvc.conf view; redacted /etc/fakesvc/conf.d/extra.conf view)
assert_contains "$v" $'db_password = <REDACTED>\nupstream smtp://dryrun:<REDACTED>@mail.example.net:587' "view: unquoted value and url userinfo blanked"
assert_eq "" "$(grep -oE 'hunter2|relaypass|relayuser|w0rd|pkipass|LEAK' <<<"$v")" "view: no values survive"

r=$(redacted /etc/fakesvc/conf.d/extra.conf replay)
assert_contains "$r" $'api_token: CHANGEME-dryrun\nsecret "CHANGEME-dryrun";' "replay: colon separator and quoted value keep syntax"
assert_contains "$r" "\$sql_password = 'CHANGEME-dryrun';" "replay: sigil key quoted value keeps syntax"

r=$(redacted /etc/systemd/system/fakesvc.service.d/override.conf replay)
assert_contains "$r" 'Environment=DB_PASSWORD=CHANGEME-dryrun' "replay: Environment= wrapper"
finish

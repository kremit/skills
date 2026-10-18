#!/usr/bin/env bash
# assertion helpers and script runners for service-migration tests
TESTS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SKILL_DIR=$(dirname "$TESTS_DIR")
FAILS=0

run_discover() {
	SM_ROOT="$TESTS_DIR/fixtures/root" STUB_FIXTURES="$TESTS_DIR/fixtures" \
		PATH="$TESTS_DIR/stubs:$PATH" bash "$SKILL_DIR/discover.sh" "$@"
}

run_verify() {
	STUB_FIXTURES="$TESTS_DIR/fixtures" PATH="$TESTS_DIR/stubs:$PATH" \
		bash "$SKILL_DIR/verify.sh" "$@"
}

pass() { printf 'ok   %s\n' "$1"; }
fail() { printf 'FAIL %s\n' "$1"; FAILS=$((FAILS + 1)); }

assert_jq() { # <json> <jq filter that must yield true> <label>
	if jq -e "$2" >/dev/null 2>&1 <<<"$1"; then pass "$3"; else fail "$3  [filter: $2]"; fi
}

assert_contains() { # <haystack> <needle> <label>
	if [[ $1 == *"$2"* ]]; then pass "$3"; else fail "$3  [missing: $2]"; fi
}

assert_not_contains() { # <haystack> <needle> <label>
	if [[ $1 != *"$2"* ]]; then pass "$3"; else fail "$3  [found: $2]"; fi
}

assert_eq() { # <want> <got> <label>
	if [[ $1 == "$2" ]]; then pass "$3"; else fail "$3  [got '$2', want '$1']"; fi
}

finish() { exit $((FAILS > 0)); }

#!/usr/bin/env bash
# runs every test_*.sh; exit 1 if any fail
cd "$(dirname "$0")" || exit 1
rc=0
for t in test_*.sh; do
	printf '== %s\n' "$t"
	bash "$t" || rc=1
done
exit $rc

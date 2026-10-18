#!/bin/bash
#
# Wrapper for the /project-review skill. Runs every layer of the project review.
#
# Layers, in output order:
#   1. registry scan   inbound work waiting on you (GitHub), CI, unregistered repos
#   2. registry repos  last push and open PRs for every GitHub repo in the CSV
#   3. local trees     uncommitted work, unpushed commits, stale branches
#   Self-hosted GitLab is not wired yet; the registry scan prints a stub line.
#
# Usage:
#   run.sh            all layers; repo discovery only on review days
#   run.sh scan       all layers with full repo discovery
#   run.sh fetch      all layers, local trees run git fetch first
#   run.sh local      local tree check only
#   run.sh hosted     registry repo report only
#   run.sh update     refresh CSV git HEADs and DNS first, then the registry repo report
#
# Review days: project_review_days in $VPA_DIR/vpa_config.json (VPA_DIR defaults to ~/vpa)
# Registry: ~/dev/projects/meta/projects_report.csv (this script never writes it)

VPA_DIR="${VPA_DIR:-$HOME/vpa}"
CONFIG="$VPA_DIR/vpa_config.json"
CSV="$HOME/dev/projects/meta/projects_report.csv"
OUT="$VPA_DIR/out"
SCAN_OUT="$OUT/registry_scan_latest.txt"
HOSTED_OUT="$OUT/project_review_latest.txt"
TREE_OUT="$OUT/local_tree_latest.txt"

is_review_day() {
	local today
	today=$(date +%A)
	jq -e --arg d "$today" '(.project_review_days // []) | index($d) != null' "$CONFIG" > /dev/null 2>&1
}

run_scan() {
	local args=(--csv "$CSV" --output "$SCAN_OUT")
	[ "$1" = "discover" ] || args+=(--no-discovery)
	"$VPA_DIR/registry_scan.sh" "${args[@]}"
}

run_hosted() {
	bash "$VPA_DIR/project_review.sh" --csv "$CSV" --output "$HOSTED_OUT" > /dev/null 2>&1
	cat "$HOSTED_OUT"
}

run_tree() {
	local args=(--output "$TREE_OUT")
	[ "$1" = "fetch" ] && args+=(--fetch)
	"$VPA_DIR/local_tree_check.sh" "${args[@]}"
}

section() {
	echo
	echo "===== $1 ====="
	echo
}

mode="${1:-all}"
discover="no"
is_review_day && discover="discover"

case "$mode" in
	all)
		section "1 of 3: Registry scan"
		run_scan "$discover"
		section "2 of 3: Registry repos"
		run_hosted
		section "3 of 3: Local trees"
		run_tree
		;;
	scan)
		section "1 of 3: Registry scan (full discovery)"
		run_scan discover
		section "2 of 3: Registry repos"
		run_hosted
		section "3 of 3: Local trees"
		run_tree
		;;
	fetch)
		section "1 of 3: Registry scan"
		run_scan "$discover"
		section "2 of 3: Registry repos"
		run_hosted
		section "3 of 3: Local trees (fetched)"
		run_tree fetch
		;;
	local)
		run_tree
		;;
	hosted)
		run_hosted
		;;
	update)
		bash "$VPA_DIR/update_projects.sh" --csv "$CSV"
		section "Registry repos"
		run_hosted
		;;
	*)
		echo "Usage: run.sh [scan|fetch|local|hosted|update]" >&2
		exit 2
		;;
esac

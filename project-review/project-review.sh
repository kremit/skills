#!/bin/bash
# Project Review Skill - Daily code activity dashboard
# Usage: skill project-review
# Prompts for repo paths, fetches recent commits and open PRs

set -e

OUTPUT_DIR="${HOME}/.claude/projects/$(echo "$HOME/dev" | tr / -)/tracking"
OUTPUT_FILE="${OUTPUT_DIR}/project-review-$(date +%Y%m%d_%H%M%S).txt"
SUMMARY_FILE="${OUTPUT_DIR}/project-review-latest.txt"

mkdir -p "$OUTPUT_DIR"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Helper: Extract GitHub token from ~/.github-oauth
get_github_token() {
	if [ -f ~/.git-credentials ]; then
		grep 'https://' ~/.git-credentials | head -1 | sed 's|https://[^:]*:||' | sed 's|@.*||'
	fi
}

# Helper: Extract repo owner/name from URL
parse_repo_ref() {
	local url="$1"
	# Handle both git@github.com:owner/repo.git and https://github.com/owner/repo.git
	if [[ "$url" =~ git@[^:]+:([^/]+)/(.+)\.git$ ]]; then
		echo "${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
	elif [[ "$url" =~ https://[^/]+/([^/]+)/(.+)\.git$ ]]; then
		echo "${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
	elif [[ "$url" =~ https://[^/]+/([^/]+)/(.+)$ ]]; then
		echo "${BASH_REMATCH[1]}/${BASH_REMATCH[2]}"
	fi
}

# Helper: Check if command exists
has_cmd() {
	command -v "$1" &>/dev/null
}

# Helper: Get recent commits using git
get_commits() {
	local repo_path="$1"
	local count="${2:-5}"

	if [ ! -d "$repo_path/.git" ]; then
		echo "(not a git repo)"
		return 1
	fi

	git -C "$repo_path" log --oneline -n "$count" 2>/dev/null || echo "(unable to read commits)"
}

# Helper: Get open PRs via gh or API
get_open_prs() {
	local repo_ref="$1"

	if has_cmd gh; then
		# Use GitHub CLI if available
		gh pr list -R "$repo_ref" --state open --limit 10 --json number,title,author,state 2>/dev/null | \
			jq -r '.[] | "#\(.number) \(.title) (@\(.author.login)) - \(.state)"' || echo "(gh failed)"
	else
		# Fallback to curl with token
		local token=$(get_github_token)
		if [ -z "$token" ]; then
			echo "(no GitHub token found)"
			return 1
		fi

		curl -s -H "Authorization: token $token" \
			"https://api.github.com/repos/${repo_ref}/pulls?state=open&per_page=10" | \
			jq -r '.[] | "#\(.number) \(.title) (@\(.user.login)) - \(.state)"' 2>/dev/null || echo "(API call failed)"
	fi
}

# Main
main() {
	echo -e "${BLUE}=== Project Review Tool ===${NC}"
	echo "Enter paths to repos (one per line, empty line to finish):"
	echo "(supports local paths or GitHub 'org/repo' references)"
	echo

	repos=()
	while read -p "Repo path: " repo_input; do
		[ -z "$repo_input" ] && break
		repos+=("$repo_input")
	done

	if [ ${#repos[@]} -eq 0 ]; then
		echo -e "${YELLOW}No repos provided.${NC}"
		exit 0
	fi

	# Build output
	output=""
	output+="Project Review - $(date)\n"
	output+="================================================\n\n"

	for repo in "${repos[@]}"; do
		output+="${BLUE}${repo}${NC}\n"

		# Determine if local path or remote reference
		if [ -d "$repo" ]; then
			# Local repo
			origin_url=$(git -C "$repo" config --get remote.origin.url 2>/dev/null || echo "no remote")
			repo_ref=$(parse_repo_ref "$origin_url")

			output+="  Origin: ${origin_url}\n"
			output+="  Recent commits:\n"

			commits=$(get_commits "$repo" 5)
			while IFS= read -r line; do
				output+="    $line\n"
			done <<< "$commits"

			if [ -n "$repo_ref" ]; then
				output+="  Open PRs:\n"
				prs=$(get_open_prs "$repo_ref")
				if [ -z "$prs" ] || [[ "$prs" == *"failed"* ]]; then
					output+="    (unable to fetch)\n"
				else
					while IFS= read -r line; do
						[ -n "$line" ] && output+="    $line\n"
					done <<< "$prs"
				fi
			fi
		else
			# Assume it's a GitHub reference (org/repo)
			output+="  Type: GitHub reference\n"
			output+="  Open PRs:\n"
			prs=$(get_open_prs "$repo")
			if [ -z "$prs" ] || [[ "$prs" == *"failed"* ]]; then
				output+="    (unable to fetch)\n"
			else
				while IFS= read -r line; do
					[ -n "$line" ] && output+="    $line\n"
				done <<< "$prs"
			fi
		fi

		output+="\n"
	done

	# Display
	echo -e "$output"

	# Save
	echo -e "$output" > "$OUTPUT_FILE"
	echo -e "$output" > "$SUMMARY_FILE"
	echo -e "\n${GREEN}✓ Saved to: $SUMMARY_FILE${NC}"
}

main "$@"

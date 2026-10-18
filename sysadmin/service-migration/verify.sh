#!/usr/bin/env bash
# verify.sh: post-replay checks for a migrated service on the dry-run target, prints JSON.
# usage: verify.sh <service> [--listen "tcp ADDR:PORT"]... [--allow-egress IP|all]...
#          [--probe dns|http|https|spamc|port|none] [--watch SECONDS] [--since EPOCH] [--pid PID]
set -uo pipefail
export LC_ALL=C

SVC=""
EXPECT_LISTEN=()
ALLOW_EGRESS=()
PROBE=port
WATCH=30
SINCE=$(( $(date +%s) - 600 ))
PID_OVERRIDE=""
CHECKS=()
declare -A U=()
# ss address forms differ by release: :::22, *:80 and %iface scopes become [::]:22, 0.0.0.0:80, no scope
NORM_AWK='function norm(a,   n, x, p, h) { sub(/%[^:\]]*/, "", a); n = split(a, x, ":"); p = x[n]; h = substr(a, 1, length(a) - length(p) - 1); if (h == "*") h = "0.0.0.0"; if (h ~ /:/ && h !~ /^\[/) h = "[" h "]"; return h ":" p }'

# short-lived UDP sockets (named, chronyd) land in the ephemeral range and churn on every run
ephemeral_port_range() {
	local range
	range=$(cat /proc/sys/net/ipv4/ip_local_port_range 2>/dev/null)
	if [[ $range =~ ^([0-9]+)[[:space:]]+([0-9]+)$ ]]; then
		printf '%s %s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]}"
	else
		printf '32768 60999'
	fi
}
read -r EPH_LO EPH_HI < <(ephemeral_port_range)

have() { command -v "$1" >/dev/null 2>&1; }

usage() { # [detail]
	printf 'usage: verify.sh <service> [options]\n' >&2
	(($#)) && printf '%s\n' "$1" >&2
	exit 64
}

json_str() {
	local s=$1
	s=${s//\\/\\\\}
	s=${s//\"/\\\"}
	s=${s//$'\t'/\\t}
	s=${s//$'\r'/\\r}
	s=${s//$'\n'/\\n}
	s=${s//[$'\x01'-$'\x1f']/}
	printf '"%s"' "$s"
}

add_check() { # <name> <true|false> <detail>
	CHECKS+=("$(printf '{"name":"%s","pass":%s,"detail":%s}' "$1" "$2" "$(json_str "$3")")")
}

is_private_ip() {
	local ip=${1//[\[\]]/}
	ip=${ip#::ffff:}
	if [[ $ip =~ ^([0-9]+)\.([0-9]+)\.[0-9]+\.[0-9]+$ ]]; then
		local a=${BASH_REMATCH[1]} b=${BASH_REMATCH[2]}
		((a == 10 || a == 127 || a == 0 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168) ||
			(a == 169 && b == 254) || (a == 100 && b >= 64 && b <= 127)))
		return
	fi
	ip=${ip,,}
	[[ $ip == ::1 || $ip == fe[89ab]* || $ip == f[cd]* ]]
}

load_unit() {
	local line
	if [[ -n $PID_OVERRIDE ]]; then
		U[MainPID]=$PID_OVERRIDE
		if [[ -d /proc/$PID_OVERRIDE ]]; then U[ActiveState]=active; else U[ActiveState]=dead; fi
		U[SubState]="pid $PID_OVERRIDE, no systemd"
		return
	fi
	while IFS= read -r line; do
		[[ $line == *=* ]] && U[${line%%=*}]=${line#*=}
	done < <(systemctl show "$SVC.service" -p ActiveState,SubState,MainPID,Requires,Wants 2>/dev/null)
}

pid_pattern() {
	local pid=${U[MainPID]:-0} p pat
	[[ $pid =~ ^[0-9]+$ && $pid != 0 ]] || return 1
	pat="pid=$pid,"
	for p in $(ps -o pid= --ppid "$pid" 2>/dev/null); do pat+="|pid=$p,"; done
	printf '%s' "$pat"
}

sample_listen() {
	ss -tulpn 2>/dev/null | grep -E "$1" |
		awk -v lo="$EPH_LO" -v hi="$EPH_HI" "$NORM_AWK"' {
			a = norm($5); n = split(a, x, ":"); p = x[n] + 0
			if ($1 == "udp" && p >= lo && p <= hi) next
			print $1" "a
		}' | sort -u
}

sample_outbound() { # <pid pattern> <listen ports, one per line>
	ss -tupn state established 2>/dev/null | grep -E "$1" | awk "$NORM_AWK"' {print $1" "norm($4)" "norm($5)}' |
		while read -r proto local peer; do
			grep -qxF "${local##*:}" <<<"$2" || printf '%s %s\n' "$proto" "$peer"
		done
}

check_active() {
	if [[ ${U[ActiveState]:-} == active ]]; then
		add_check unit_active true "active (${U[SubState]:-})"
	else
		add_check unit_active false "${U[ActiveState]:-unknown} (${U[SubState]:-})"
	fi
}

check_deps() {
	local d failed=()
	if [[ -n $PID_OVERRIDE ]]; then
		add_check deps_ok true "skipped: no systemd"
		return
	fi
	for d in ${U[Requires]:-} ${U[Wants]:-}; do
		systemctl is-failed --quiet "$d" 2>/dev/null && failed+=("$d")
	done
	if ((${#failed[@]})); then
		add_check deps_ok false "failed: ${failed[*]}"
	else
		add_check deps_ok true "no failed dependencies"
	fi
}

check_listen() { # <actual listen lines>
	local want line detail missing=() extra=()
	for want in ${EXPECT_LISTEN[@]+"${EXPECT_LISTEN[@]}"}; do
		grep -qxF "$want" <<<"$1" || missing+=("$want")
	done
	while IFS= read -r line; do
		[[ -z $line ]] && continue
		printf '%s\n' ${EXPECT_LISTEN[@]+"${EXPECT_LISTEN[@]}"} | grep -qxF "$line" || extra+=("$line")
	done <<<"$1"
	if ((${#missing[@]} + ${#extra[@]} == 0)); then
		add_check listen_match true "$(tr '\n' ' ' <<<"$1")"
		return
	fi
	detail="missing: ${missing[*]:-none}; extra: ${extra[*]:-none}"
	for line in ${extra[@]+"${extra[@]}"}; do
		if [[ $line == *" 0.0.0.0:"* || $line == *" *:"* || $line == *" [::]:"* ]]; then
			detail+="; BIND DRIFT: wildcard bind $line"
			break
		fi
	done
	add_check listen_match false "$detail"
}

check_egress() { # <pid pattern> <listen ports>
	local i peer addr seen="" bad=() rounds=$((WATCH / 2))
	((rounds < 1)) && rounds=1
	for ((i = 0; i < rounds; i++)); do
		seen+=$(sample_outbound "$1" "$2")$'\n'
		((i + 1 < rounds)) && sleep 2
	done
	if printf '%s\n' ${ALLOW_EGRESS[@]+"${ALLOW_EGRESS[@]}"} | grep -qxF all; then
		add_check egress_ok true "egress allowed: all"
		return
	fi
	while read -r _ peer; do
		[[ -z $peer ]] && continue
		addr=${peer%:*}
		addr=${addr//[\[\]]/}
		is_private_ip "$addr" && continue
		printf '%s\n' ${ALLOW_EGRESS[@]+"${ALLOW_EGRESS[@]}"} | grep -qxF "$addr" && continue
		bad+=("$peer")
	done < <(sort -u <<<"$seen")
	if ((${#bad[@]})); then
		add_check egress_ok false "UNEXPECTED PUBLIC EGRESS: ${bad[*]}"
	else
		add_check egress_ok true "no unexpected public egress in ${WATCH}s"
	fi
}

probe_target() { # prints "addr port" for the first expected listen socket
	local first=${EXPECT_LISTEN[0]:-} hostport addr
	[[ -n $first ]] || return 1
	hostport=${first#* }
	addr=${hostport%:*}
	addr=${addr//[\[\]]/}
	[[ $addr == 0.0.0.0 || $addr == '*' || $addr == :: ]] && addr=127.0.0.1
	printf '%s %s' "$addr" "${hostport##*:}"
}

port_open() { timeout 3 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; }

check_probe() {
	local target addr port code ok=false detail=""
	if [[ $PROBE == none ]]; then
		add_check probe true "skipped"
		return
	fi
	if ! target=$(probe_target); then
		add_check probe false "no --listen given to probe"
		return
	fi
	read -r addr port <<<"$target"
	case $PROBE in
		dns)
			if have dig; then
				dig +time=2 +tries=1 -p "$port" @"$addr" . NS >/dev/null 2>&1 && ok=true
				detail="dig @$addr -p $port"
			else detail="dig not installed"; PROBE=port; fi ;;
		http | https)
			if have curl; then
				code=$(curl -sk -o /dev/null -m 5 -w '%{http_code}' "$PROBE://$addr:$port/" 2>/dev/null)
				[[ $code =~ ^[23][0-9][0-9]$ ]] && ok=true
				detail="curl $PROBE://$addr:$port/ -> ${code:-no response}"
			else detail="curl not installed"; PROBE=port; fi ;;
		spamc)
			if have spamc; then
				spamc -d "$addr" -p "$port" -K >/dev/null 2>&1 && ok=true
				detail="spamc -K $addr:$port"
			else detail="spamc not installed"; PROBE=port; fi ;;
	esac
	if [[ $PROBE == port ]]; then
		port_open "$addr" "$port" && ok=true
		detail="tcp connect $addr:$port${detail:+ (fallback: $detail)}"
	fi
	add_check probe "$ok" "$detail"
}

# every AVC since --since counts; the target is disposable, so unrelated denials are rare and worth seeing
check_selinux() {
	local mode domain denials out rc
	if [[ -n $PID_OVERRIDE ]]; then
		add_check selinux_clean true "skipped in container: run 'sudo ausearch --input-logs -m AVC -ts recent' on the host, look for container_t"
		return
	fi
	if have getenforce; then
		mode=$(getenforce 2>/dev/null) || mode=Disabled
	else
		mode=Disabled
	fi
	if [[ $mode == Disabled ]]; then
		add_check selinux_clean true "selinux disabled"
		return
	fi
	if ! have ausearch; then
		add_check selinux_clean false "ausearch missing: dnf install audit"
		return
	fi
	domain=$(ps -o label= -p "${U[MainPID]:-0}" 2>/dev/null | cut -d: -f3)
	# no -ts: its date format follows the locale; filter on the audit epoch instead
	# --input-logs: without it ausearch reads stdin, which is this script when streamed over ssh
	out=$(ausearch --input-logs -m AVC,USER_AVC 2>&1)
	rc=$?
	if ((rc != 0)) && [[ $out != *'<no matches>'* ]]; then
		add_check selinux_clean false "ausearch error: ${out%%$'\n'*}"
		return
	fi
	denials=$(grep -E 'avc: +denied' <<<"$out" |
		awk -v s="$SINCE" 'match($0, /msg=audit\([0-9]+/) { e = substr($0, RSTART + 10, RLENGTH - 10); if (e + 0 >= s) print }' |
		grep -oE 'denied +\{[^}]*\}.*tclass=[a-z_]+' | sort -u | head -n 5)
	if [[ -n $denials ]]; then
		add_check selinux_clean false "AVC denials ($mode${domain:+, $domain}): $(tr '\n' '|' <<<"$denials")"
	else
		add_check selinux_clean true "no AVC denials since $SINCE ($mode)"
	fi
}

main() {
	local pat listen ports all=true c
	if (($# == 0)) || [[ $1 == -* ]]; then
		usage
	fi
	SVC=$1
	SVC=${SVC%.service}
	shift
	while (($#)); do
		case $1 in
			--listen) (($# >= 2)) || usage "$1 needs a value"; EXPECT_LISTEN+=("$2"); shift 2 ;;
			--allow-egress) (($# >= 2)) || usage "$1 needs a value"; ALLOW_EGRESS+=("$2"); shift 2 ;;
			--probe) (($# >= 2)) || usage "$1 needs a value"; PROBE=$2; shift 2 ;;
			--watch) (($# >= 2)) || usage "$1 needs a value"; WATCH=$2; shift 2 ;;
			--since) (($# >= 2)) || usage "$1 needs a value"; SINCE=$2; shift 2 ;;
			--pid) (($# >= 2)) || usage "$1 needs a value"; PID_OVERRIDE=$2; shift 2 ;;
			*) printf 'verify.sh: unknown option %s\n' "$1" >&2; exit 64 ;;
		esac
	done
	[[ $SINCE =~ ^[0-9]+$ ]] || usage "--since takes epoch seconds"
	[[ $WATCH =~ ^[0-9]+$ ]] || usage "--watch takes seconds"
	case $PROBE in
		dns | http | https | spamc | port | none) ;;
		*) printf 'verify.sh: bad --probe %s\n' "$PROBE" >&2; exit 64 ;;
	esac
	load_unit
	check_active
	check_deps
	if pat=$(pid_pattern); then
		listen=$(sample_listen "$pat")
		ports=$(awk '{n = split($2, a, ":"); print a[n]}' <<<"$listen")
		check_listen "$listen"
		check_egress "$pat" "$ports"
	else
		add_check listen_match false "service has no MainPID"
		add_check egress_ok false "service has no MainPID"
	fi
	check_probe
	check_selinux
	for c in ${CHECKS[@]+"${CHECKS[@]}"}; do
		[[ $c == *'"pass":false'* ]] && all=false
	done
	printf '{"service":%s,"pass":%s,"checks":[%s]}\n' "$(json_str "$SVC")" "$all" "$(IFS=,; printf '%s' "${CHECKS[*]}")"
	[[ $all == true ]]
}

[[ -n ${SM_LIB_ONLY:-} ]] || main "$@"

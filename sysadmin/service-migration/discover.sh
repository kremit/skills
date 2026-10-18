#!/usr/bin/env bash
# discover.sh: read-only fact gatherer for one systemd service, prints JSON.
# usage: discover.sh <service>
# SM_ROOT: optional filesystem prefix, used by tests
set -uo pipefail
export LC_ALL=C

SM_ROOT="${SM_ROOT:-}"
SVC=""
PKG=""
declare -A U=()
declare -A FILE_PKG=()
declare -A PKGS=()
declare -A IS_SECRET=()
CONFIG_FILES=()
DATA_PATHS=()
SECRET_FILES=()

UNIT_PROPS=Id,LoadState,ActiveState,SubState,UnitFileState,FragmentPath,DropInPaths,ExecStart,User,Group,EnvironmentFiles,WorkingDirectory,StateDirectory,ConfigurationDirectory,LogsDirectory,ReadWritePaths,After,Requires,Wants,MainPID

have() { command -v "$1" >/dev/null 2>&1; }

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

json_arr() {
	local first=1 line
	printf '['
	while IFS= read -r line; do
		[[ -z $line ]] && continue
		((first)) || printf ','
		first=0
		json_str "$line"
	done
	printf ']'
}

json_raw_arr() {
	local first=1 line
	printf '['
	while IFS= read -r line; do
		[[ -z $line ]] && continue
		((first)) || printf ','
		first=0
		printf '%s' "$line"
	done
	printf ']'
}

strip_root() {
	local line
	while IFS= read -r line; do
		printf '%s\n' "${line#"$SM_ROOT"}"
	done
}

load_unit() {
	local line
	while IFS= read -r line; do
		[[ $line == *=* ]] && U[${line%%=*}]=${line#*=}
	done < <(systemctl show "$SVC.service" -p "$UNIT_PROPS" 2>/dev/null)
}

pkg_of() {
	local out
	out=$(rpm -qf --qf '%{NAME}\n' "$1" 2>/dev/null) || return 1
	printf '%s' "${out%%$'\n'*}"
}

find_package() {
	local exec_path f
	exec_path=$(sed -n 's/.*path=\([^ ;]*\).*/\1/p' <<<"${U[ExecStart]:-}" | head -n 1)
	for f in "$exec_path" "${U[FragmentPath]:-}"; do
		[[ -n $f ]] && PKG=$(pkg_of "$f") && return 0
	done
	PKG=""
}

config_candidates() {
	local d
	printf '%s\n' "/etc/$SVC" "/etc/$SVC.conf" "/etc/$SVC.d" "/etc/sysconfig/$SVC" "/etc/default/$SVC"
	[[ -n ${U[FragmentPath]:-} ]] && printf '%s\n' "${U[FragmentPath]}"
	for d in ${U[DropInPaths]:-}; do printf '%s\n' "$d"; done
	grep -oE '(^| )/[^ ]+' <<<"${U[EnvironmentFiles]:-}" | tr -d ' '
	for d in ${U[ConfigurationDirectory]:-}; do printf '/etc/%s\n' "$d"; done
	[[ -n $PKG ]] && rpm -qc "$PKG" 2>/dev/null
	return 0
}

collect_config_files() {
	local p f pkg
	mapfile -t CONFIG_FILES < <(
		config_candidates | sort -u | while IFS= read -r p; do
			[[ -n $p && -e $SM_ROOT$p ]] || continue
			find "$SM_ROOT$p" -xdev -type f 2>/dev/null
		done | strip_root | sort -u | head -n 2000
	)
	for f in ${CONFIG_FILES[@]+"${CONFIG_FILES[@]}"}; do
		if pkg=$(pkg_of "$f"); then
			FILE_PKG[$f]=$pkg
			PKGS[$pkg]=1
		fi
	done
	[[ -n $PKG ]] && PKGS[$PKG]=1
	return 0
}

data_candidates() {
	local d files
	for d in ${U[StateDirectory]:-}; do printf '/var/lib/%s\n' "$d"; done
	for d in ${U[LogsDirectory]:-}; do printf '/var/log/%s\n' "$d"; done
	for d in ${U[ReadWritePaths]:-} ${U[WorkingDirectory]:-}; do printf '%s\n' "${d#[-!]}"; done
	printf '%s\n' "/var/lib/$SVC" "/var/spool/$SVC" "/var/log/$SVC" "/var/cache/$SVC" "/srv/$SVC" "/var/$SVC"
	mapfile -t files < <(scan_files)
	if ((${#files[@]})); then
		grep -hoIE '/(var|srv|opt|home|data)/[^][:space:]"'"'"';,)<>{}*]+' "${files[@]}" 2>/dev/null
	fi
	return 0
}

# existing paths only; children of an already-kept path are dropped
collect_data_paths() {
	local p k
	while IFS= read -r p; do
		[[ -n $p && $p != / && -e $SM_ROOT$p ]] || continue
		for k in ${DATA_PATHS[@]+"${DATA_PATHS[@]}"}; do
			[[ $p == "$k"/* ]] && continue 2
		done
		DATA_PATHS+=("$p")
	done < <(data_candidates | sed -E 's#/+$##' | sort -u)
}

emit_unit() {
	local k v first=1
	printf '{'
	while IFS= read -r k; do
		[[ -n $k ]] || continue
		((first)) || printf ','
		first=0
		v=${U[$k]}
		printf '%s:%s' "$(json_str "$k")" "$(json_str "$v")"
	done < <(printf '%s\n' "${!U[@]}" | sort)
	printf '}'
}

emit_package() {
	local version="" p
	[[ -n $PKG ]] && version=$(rpm -q --qf '%{VERSION}-%{RELEASE}' "$PKG" 2>/dev/null)
	printf '{"name":%s,"version":%s,"modified":%s}' \
		"$(json_str "$PKG")" "$(json_str "$version")" \
		"$({ ((${#PKGS[@]})) && for p in "${!PKGS[@]}"; do rpm -V "$p" 2>/dev/null; done; } | sort -u | json_arr)"
}

emit_config_files() {
	local f meta ctx pkg_json
	for f in ${CONFIG_FILES[@]+"${CONFIG_FILES[@]}"}; do
		meta=$(stat -c '%U:%G %a' "$SM_ROOT$f" 2>/dev/null) || continue
		ctx=$(stat -c '%C' "$SM_ROOT$f" 2>/dev/null) || ctx=""
		pkg_json=null
		[[ -n ${FILE_PKG[$f]:-} ]] && pkg_json=$(json_str "${FILE_PKG[$f]}")
		printf '{"path":%s,"owner":%s,"mode":%s,"context":%s,"package":%s}\n' \
			"$(json_str "$f")" "$(json_str "${meta% *}")" "$(json_str "${meta##* }")" \
			"$(json_str "$ctx")" "$pkg_json"
	done | json_raw_arr
}

emit_data_paths() {
	local p kind size
	for p in ${DATA_PATHS[@]+"${DATA_PATHS[@]}"}; do
		if [[ -d $SM_ROOT$p ]]; then kind=dir; else kind=file; fi
		size=$(timeout 20 du -sk "$SM_ROOT$p" 2>/dev/null | cut -f1)
		printf '{"path":%s,"kind":"%s","size_kb":%s}\n' "$(json_str "$p")" "$kind" "${size:-null}"
	done | json_raw_arr
}

COMMENT_RE='^[[:space:]]*(#|;|//)'
CERT_RE='\.(crt|pem|cer|key|p12|pfx)([[:space:]";,]|$)|(ssl|tls)[_-]?[a-z_]*(certificate|cert|key|ca)(_?file|_?path|_?dir|s)?\b|sslcertificate|sslcacertificate'
KW_RE='[a-z0-9_.-]*(pass(word|wd|phrase)?|secret|token|api[_-]?key|credentials?|masterauth|authorization|bearer)[a-z0-9_.-]*'
USERINFO_RE='://[^?#[:space:]@]*:[^?#[:space:]]*@'
CRED_RE="$KW_RE"'[[:space:]]*([=:]|[[:space:]]["'"'"'a-z0-9])|'"$USERINFO_RE"
URL_RE='[a-z][a-z0-9+.-]*://[^][:space:]"'"'"';,<>]+'
SOCK_RE='unix:[^][:space:]"'"'"';,?#]+|/[^][:space:]"'"'"';,?#]+\.sock(et)?\b'
HOSTPORT_RE='\b(localhost|([a-z0-9-]+\.)+[a-z0-9-]+|\[[0-9a-f:]+\]):[0-9]{2,5}\b'
CA_KEY_RE='(^|[_-])ca([_-]|$)|cacert|chain'
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
KEY_PATH_RE='\.(keys?|p12|pfx|private)$|^/etc/.*/private/'
# module and include directives name files, never credentials (proxy_fdpass_module)
CRED_SKIP_RE='^[[:space:]]*(loadmodule|include|includeoptional)[[:space:]]'
SECRET_NAME_RE='(^|[._-])(sasl_passwd|htpasswd|passwd|shadow)|secrets?$|\.secret$|credentials?$'

first_token() {
	local l=$1
	l=${l#"${l%%[![:space:]]*}"}
	l=${l%%[[:space:]=:]*}
	printf '%s' "${l:0:64}"
}

# key name only; url-userinfo or (unparsed) when the key cannot be isolated safely
cred_key() {
	local line=$1 tok out="(unparsed)"
	shopt -s nocasematch
	if [[ $line =~ $USERINFO_RE ]]; then
		out="url-userinfo"
	else
		tok=$(first_token "$line")
		if [[ $tok =~ ^$KW_RE$ ]]; then
			out=$tok
		elif [[ $tok =~ ^([\$@%])(.+)$ && ${BASH_REMATCH[2]} =~ ^$KW_RE$ ]]; then
			out=$tok
		elif [[ $line =~ ^[[:space:]]*(environment|export|setenv|set|env)[[:space:]=]+($KW_RE)[[:space:]]*[=:] ]]; then
			out=${BASH_REMATCH[2]}
		fi
	fi
	shopt -u nocasematch
	printf '%s' "${out:0:64}"
}

# reads only the PEM armor line, never key material
is_private_key_file() {
	local first
	first=$(head -n 1 "$1" 2>/dev/null)
	[[ $first == *"PRIVATE KEY-----"* ]]
}

# key-like path, credential-file name, or PEM private key anywhere; grep -q returns only a boolean
is_secret_file() {
	local f=$1 hit=1
	[[ $f =~ $KEY_PATH_RE ]] && return 0
	shopt -s nocasematch
	[[ ${f##*/} =~ $SECRET_NAME_RE ]] && hit=0
	shopt -u nocasematch
	((hit == 0)) && return 0
	grep -qsE -- '-----BEGIN [A-Z ]*PRIVATE KEY-----' "$SM_ROOT$f"
}

# unprefixed paths on stdin; each is checked once
mark_secret_files() {
	local f
	while IFS= read -r f; do
		[[ -n $f && -z ${IS_SECRET[$f]:-} ]] || continue
		is_secret_file "$f" && IS_SECRET[$f]=1
	done
	return 0
}

# config files first (data path discovery scans them), then small files under data paths
collect_secret_files() {
	if (($#)) && ((${#DATA_PATHS[@]})); then
		mark_secret_files < <(timeout 30 find "${DATA_PATHS[@]/#/$SM_ROOT}" -xdev -type f -size -1024k 2>/dev/null |
			head -n 5000 | strip_root)
	elif ((${#CONFIG_FILES[@]})); then
		mark_secret_files < <(printf '%s\n' "${CONFIG_FILES[@]}")
	fi
	((${#IS_SECRET[@]})) && mapfile -t SECRET_FILES < <(printf '%s\n' "${!IS_SECRET[@]}" | sort -u)
	return 0
}

emit_secret_files() {
	((${#SECRET_FILES[@]})) || { printf '[]'; return; }
	printf '%s\n' "${SECRET_FILES[@]}" | json_arr
}

# config files minus secret files; used by every content scan
scan_files() {
	local f
	((${#CONFIG_FILES[@]})) || return 0
	for f in "${CONFIG_FILES[@]}"; do
		[[ -n ${IS_SECRET[$f]:-} ]] && continue
		[[ $f =~ $KEY_PATH_RE ]] && continue
		is_private_key_file "$SM_ROOT$f" && continue
		printf '%s\n' "$SM_ROOT$f"
	done
}

emit_certs() {
	local f n content key lkey path kind exists expires paths files second
	mapfile -t files < <(scan_files)
	((${#files[@]})) || { printf '[]'; return; }
	while IFS= read -r -d '' f && IFS=: read -r n content; do
		f=${f#"$SM_ROOT"}
		grep -qE "$COMMENT_RE" <<<"$content" && continue
		key=$(first_token "$content")
		# postfix master.cf -o overrides: key is the next token, cut at =
		if [[ $key == -o ]]; then
			read -r _ second _ <<<"$content"
			key=${second%%=*}
		fi
		lkey=${key,,}
		# a path must start after line start, whitespace, =, comma or a quote, never inside a url
		mapfile -t paths < <(grep -oE '(^|[[:space:]=,"'"'"'])/[^/][^][:space:]"'"'"';,]*\.(crt|pem|cer|key|p12|pfx)' <<<"$content" | sed -E 's#^[^/]##')
		((${#paths[@]})) || paths=("")
		for path in "${paths[@]}"; do
			if [[ -n $path && $path =~ $KEY_PATH_RE ]]; then
				kind=key
			elif [[ $lkey == *key* && ${#paths[@]} -le 1 ]]; then
				kind=key
			elif [[ $lkey =~ $CA_KEY_RE ]]; then
				kind=ca
			else
				kind=cert
			fi
			exists=false
			expires=""
			if [[ -n $path && -e $SM_ROOT$path ]]; then
				exists=true
				is_private_key_file "$SM_ROOT$path" && kind=key
				# key files are never opened; certs only via x509 -enddate
				if [[ $kind != key ]] && have openssl; then
					expires=$(openssl x509 -noout -enddate -in "$SM_ROOT$path" 2>/dev/null | cut -d= -f2)
				fi
			fi
			printf '{"file":%s,"line":%s,"key":%s,"path":%s,"kind":"%s","exists":%s,"expires":%s}\n' \
				"$(json_str "$f")" "$n" "$(json_str "$key")" "$(json_str "$path")" "$kind" "$exists" "$(json_str "$expires")"
		done
	done < <(grep -nHZIiE "$CERT_RE" "${files[@]}" 2>/dev/null) | json_raw_arr
}

emit_credentials() {
	local f n content files
	mapfile -t files < <(scan_files)
	((${#files[@]})) || { printf '[]'; return; }
	while IFS= read -r -d '' f && IFS=: read -r n content; do
		f=${f#"$SM_ROOT"}
		grep -qE "$COMMENT_RE" <<<"$content" && continue
		grep -qiE "$CRED_SKIP_RE" <<<"$content" && continue
		printf '{"file":%s,"line":%s,"key":%s}\n' "$(json_str "$f")" "$n" "$(json_str "$(cred_key "$content")")"
	done < <(grep -nHZIiE "$CRED_RE" "${files[@]}" 2>/dev/null) | json_raw_arr
}

# urls come from every line with userinfo and query stripped; sockets and host:port skip credential lines
emit_service_refs() {
	local files
	mapfile -t files < <(scan_files)
	((${#files[@]})) || { printf '[]'; return; }
	{
		grep -hvIE "$COMMENT_RE" "${files[@]}" 2>/dev/null | grep -oiE "$URL_RE" |
			sed -E 's#(://)[^?#[:space:]]*@#\1#; s#^([a-z][a-z0-9+.-]*://[^/?#]*).*#\1#I'
		grep -hvIE "$COMMENT_RE" "${files[@]}" 2>/dev/null | grep -viE "$CRED_RE" |
			grep -oiE "$SOCK_RE|$HOSTPORT_RE" | sed -E 's/[?#].*//'
	} | sort -u | json_arr
}

svc_pid_pattern() {
	local pid=${U[MainPID]:-0} p pat
	[[ $pid =~ ^[0-9]+$ && $pid != 0 ]] || return 1
	pat="pid=$pid,"
	for p in $(ps -o pid= --ppid "$pid" 2>/dev/null); do pat+="|pid=$p,"; done
	printf '%s' "$pat"
}

emit_network() {
	local pat listen ports outbound
	if ! pat=$(svc_pid_pattern); then
		printf '{"running":false,"listen":[],"outbound":[]}'
		return
	fi
	listen=$(ss -tulpn 2>/dev/null | grep -E "$pat" |
		awk -v lo="$EPH_LO" -v hi="$EPH_HI" "$NORM_AWK"' {
			a = norm($5); n = split(a, x, ":"); p = x[n] + 0
			if ($1 == "udp" && p >= lo && p <= hi) next
			print $1" "a
		}' | sort -u)
	ports=$(awk '{n = split($2, a, ":"); print a[n]}' <<<"$listen")
	outbound=$(ss -tupn state established 2>/dev/null | grep -E "$pat" | awk "$NORM_AWK"' {print $1" "norm($4)" "norm($5)}' |
		while read -r proto local peer; do
			grep -qxF "${local##*:}" <<<"$ports" || printf '%s %s\n' "$proto" "$peer"
		done | sort -u)
	printf '{"running":true,"listen":%s,"outbound":%s}' "$(json_arr <<<"$listen")" "$(json_arr <<<"$outbound")"
}

# restorecon -n is read-only; handles EL9 "Would relabel" and EL7/8 "reset" wording
# stdout: a "true"/"false" truncated line, then the mislabeled JSON array
emit_mislabeled() {
	local line p targets=(${DATA_PATHS[@]+"${DATA_PATHS[@]}"}) out rc lines truncated=false
	local re_new='^Would relabel (.+) from ([^ ]+) to ([^ ]+)$'
	local re_old='reset (.+) context ([^ ]+)->([^ ]+)$'
	[[ -d $SM_ROOT/etc/$SVC ]] && targets+=("/etc/$SVC")
	if ! have restorecon || ((${#targets[@]} == 0)); then
		printf 'false\n[]'
		return
	fi
	# capture output before piping, so the exit status is restorecon's own and not the last stage of a pipe
	out=$(timeout 120 restorecon -Rnv "${targets[@]/#/$SM_ROOT}" 2>/dev/null)
	rc=$?
	((rc == 124)) && truncated=true
	if [[ -n $out ]]; then
		lines=$(grep -c '' <<<"$out")
		((lines > 200)) && truncated=true
	fi
	printf '%s\n' "$truncated"
	head -n 200 <<<"$out" | while IFS= read -r line; do
		[[ $line =~ $re_new || $line =~ $re_old ]] || continue
		p=${BASH_REMATCH[1]}
		printf '{"path":%s,"current":%s,"expected":%s}\n' \
			"$(json_str "${p#"$SM_ROOT"}")" "$(json_str "${BASH_REMATCH[2]}")" "$(json_str "${BASH_REMATCH[3]}")"
	done | json_raw_arr
}

emit_selinux() {
	local mode=Disabled ml trunc json
	have getenforce && mode=$(getenforce 2>/dev/null)
	printf '{"mode":%s' "$(json_str "$mode")"
	printf ',"booleans":%s' "$({ have semanage && semanage boolean -l -C; } 2>/dev/null | grep -vE '^(SELinux|$)' | json_arr)"
	printf ',"fcontexts":%s' "$({ have semanage && semanage fcontext -l -C; } 2>/dev/null | grep -vE '^(SELinux|$)' | json_arr)"
	printf ',"ports":%s' "$({ have semanage && semanage port -l -C; } 2>/dev/null | grep -vE '^(SELinux|$)' | json_arr)"
	printf ',"modules":%s' "$({ have semodule && semodule --list-modules=full; } 2>/dev/null | awk '$1 == 400 {print $2}' | json_arr)"
	if [[ $mode == Disabled ]]; then
		printf ',"mislabeled":[],"mislabeled_truncated":false}'
	else
		ml=$(emit_mislabeled)
		trunc=${ml%%$'\n'*}
		json=${ml#*$'\n'}
		printf ',"mislabeled":%s,"mislabeled_truncated":%s}' "$json" "$trunc"
	fi
}

emit_firewall() {
	local z out=""
	if have firewall-cmd && [[ $(firewall-cmd --state 2>/dev/null) == running ]]; then
		for z in $(firewall-cmd --get-active-zones 2>/dev/null | awk '/^[^[:space:]]/ {print $1}'); do
			out+=$(firewall-cmd --zone="$z" --list-all 2>/dev/null)$'\n'
		done
	fi
	json_str "$out"
}

# files under the given roots that mention the service name or its run user as a word
scaffold_grep() {
	local t args=(-e "$SVC")
	[[ -n ${U[User]:-} && ${U[User]} != root ]] && args+=(-e "${U[User]}")
	for t in "$@"; do
		[[ -e $SM_ROOT$t ]] && grep -rlIwF "${args[@]}" "$SM_ROOT$t" 2>/dev/null
	done | strip_root | sort -u
	return 0
}

emit_scaffolding() {
	local units
	units=$(scaffold_grep /etc/systemd/system /usr/lib/systemd/system |
		grep -E '\.(timer|service|path|socket)$' |
		grep -vxF "${U[FragmentPath]:-}" |
		grep -vF "/etc/systemd/system/$SVC.service.d/")
	printf '{"cron":%s' "$(scaffold_grep /etc/crontab /etc/cron.d /etc/cron.hourly /etc/cron.daily /etc/cron.weekly /etc/cron.monthly /var/spool/cron | json_arr)"
	printf ',"systemd_units":%s' "$(json_arr <<<"$units")"
	printf ',"logrotate":%s' "$(scaffold_grep /etc/logrotate.d /etc/logrotate.conf | json_arr)"
	printf ',"sudoers":%s' "$(scaffold_grep /etc/sudoers /etc/sudoers.d | json_arr)"
	printf '}'
}

main() {
	local os="" host
	SVC=${1:?usage: discover.sh <service>}
	SVC=${SVC%.service}
	load_unit
	if [[ ${U[LoadState]:-not-found} == not-found ]]; then
		printf '{"schema":1,"service":%s,"error":"unit not found"}\n' "$(json_str "$SVC")"
		exit 2
	fi
	find_package
	collect_config_files
	collect_secret_files
	collect_data_paths
	collect_secret_files data
	[[ -r $SM_ROOT/etc/os-release ]] &&
		os=$(sed -n 's/^PRETTY_NAME="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$SM_ROOT/etc/os-release")
	host=$(hostname -f 2>/dev/null || cat /proc/sys/kernel/hostname)
	printf '{"schema":1,"service":%s,"host":%s,"os":%s,"collected_at":%s' \
		"$(json_str "$SVC")" "$(json_str "$host")" "$(json_str "$os")" \
		"$(json_str "$(date -u +%Y-%m-%dT%H:%M:%SZ)")"
	printf ',"unit":%s' "$(emit_unit)"
	printf ',"package":%s' "$(emit_package)"
	printf ',"config_files":%s' "$(emit_config_files)"
	printf ',"data_paths":%s' "$(emit_data_paths)"
	printf ',"references":{"certs":%s,"credentials":%s,"services":%s}' \
		"$(emit_certs)" "$(emit_credentials)" "$(emit_service_refs)"
	printf ',"secret_files":%s' "$(emit_secret_files)"
	printf ',"network":%s' "$(emit_network)"
	printf ',"selinux":%s' "$(emit_selinux)"
	printf ',"firewall":%s' "$(emit_firewall)"
	printf ',"scaffolding":%s' "$(emit_scaffolding)"
	printf '}\n'
}

[[ -n ${SM_LIB_ONLY:-} ]] || main "$@"

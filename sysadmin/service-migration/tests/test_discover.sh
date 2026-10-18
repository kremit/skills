#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"

out=$(run_discover fakesvc)
assert_jq "$out" '.service == "fakesvc" and .unit.User == "fakesvc" and .package.name == "fakesvc" and .package.version == "1.2-3.el9"' "unit and owning package"
assert_jq "$out" '.package.modified | any(test("/etc/fakesvc/fakesvc.conf"))' "rpm -V modifications"
assert_jq "$out" '[.config_files[] | {(.path): .package}] | add | .["/etc/fakesvc/fakesvc.conf"] == "fakesvc" and .["/etc/fakesvc/conf.d/extra.conf"] == null and has("/etc/systemd/system/fakesvc.service.d/override.conf")' "config files: owned, unowned, drop-in"
assert_jq "$out" '[.data_paths[].path] == ["/opt/fakesvc-archive", "/var/lib/fakesvc"]' "data paths: config-referenced, nested pruned"

missing=$(run_discover nosuchsvc); rc=$?
assert_eq "2 unit not found" "$rc $(jq -r .error <<<"$missing")" "missing unit: exit 2 and error"

assert_jq "$out" '((.references.certs[] | select(.key == "tls_cert_file") | .kind == "cert" and (.expires | length > 0)) and (.references.certs[] | select(.key == "tls_key_file") | .kind == "key" and .expires == "") and ([.references.certs[] | select(.key == "tls_chain_files") | {(.path): .kind}] | add == {"/etc/fakesvc/keys/chain-key.pem": "key", "/etc/pki/tls/certs/fakesvc.crt": "ca"})) and ([.references.certs[].key] | index("SSLSessionCache") == null)' "certs: expiry read, key files never parsed, per-path kind"
assert_jq "$out" '[.references.credentials[].key] | sort == ["$sql_password","DB_PASSWORD","api_token","b_password","db_password","masterauth","mixed_password","secret","semi_password","url-userinfo","url-userinfo","url-userinfo","url-userinfo"]' "credential key names only, comments skipped"
assert_jq "$out" '.references.services | contains(["unix:/run/spamd/spamd.sock","smtp://mail.example.net:587","redis.internal.example:6379","https://hooks.example.net"])' "service refs: endpoints only, userinfo and query stripped"
assert_jq "$out" '.secret_files | (contains(["/etc/fakesvc/sasl_passwd","/etc/fakesvc/bundle.pem","/etc/fakesvc/keys/chain-key.pem","/etc/fakesvc/keys/dkim.private","/etc/fakesvc/fakesvc.keys"]) and (index("/var/lib/fakesvc/private/notes.txt") == null))' "secret files: key names, credential names, PEM keys anywhere"
leaks=""
for s in hunter2 SUPERSECRET relaypass relayuser SENTINEL LEAK commented-out-secret; do
	[[ $out == *"$s"* ]] && leaks+=" $s"
done
assert_eq "" "$leaks" "no secret values anywhere in output"

assert_jq "$out" '.network.listen == ["tcp 127.0.0.1:8025","tcp [::]:8025"] and .network.outbound == ["tcp 203.0.113.9:587"]' "network: own sockets only, outbound excludes inbound"
assert_jq "$out" '.selinux.mode == "Enforcing" and .selinux.modules == ["fakesvc_local"] and (.selinux.booleans | length == 1) and .selinux.fcontexts == []' "selinux: local customizations, header lines filtered"
assert_jq "$out" '.selinux.mislabeled == [
	{"path": "/var/lib/fakesvc/store/data.db", "current": "unconfined_u:object_r:default_t:s0", "expected": "system_u:object_r:fakesvc_var_lib_t:s0"},
	{"path": "/opt/fakesvc-archive", "current": "unconfined_u:object_r:usr_t:s0", "expected": "system_u:object_r:fakesvc_archive_t:s0"}]
	and .selinux.mislabeled_truncated == false' "selinux: restorecon mislabels, EL9 and EL7/8 formats"

finish

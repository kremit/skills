#!/usr/bin/env bash
source "$(dirname "$0")/lib.sh"

base=(fakesvc --listen "tcp 127.0.0.1:8025" --listen "tcp [::]:8025" --probe none --watch 0 --since 0)

out=$(run_verify "${base[@]}" --allow-egress 203.0.113.9); rc=$?
assert_jq "$out" '.pass and (.checks | length == 6)' "clean run passes all six checks"
assert_eq 0 "$rc" "clean run exits 0"

out=$(run_verify "${base[@]}")
assert_jq "$out" '.checks[] | select(.name == "egress_ok") | .pass == false and (.detail | test("203.0.113.9:587"))' "unexpected public egress fails"

out=$(STUB_SS_LISTEN=ss-listen-drift.txt run_verify "${base[@]}" --allow-egress all)
assert_jq "$out" '.checks[] | select(.name == "listen_match") | .pass == false and (.detail | test("BIND DRIFT"))' "wildcard bind flagged"

out=$(STUB_AVC=1 run_verify "${base[@]}" --allow-egress all)
assert_jq "$out" '.checks[] | select(.name == "selinux_clean") | .pass == false and (.detail | test("default_t"))' "AVC denial fails the run"

export SM_LIB_ONLY=1
source "$SKILL_DIR/verify.sh"
wrong=""
for ip in 10.1.2.3 172.20.0.1 fd00::5 ::ffff:10.0.0.1; do is_private_ip "$ip" || wrong+=" $ip"; done
for ip in 8.8.8.8 172.32.0.1 203.0.113.9 2001:4860::8888; do is_private_ip "$ip" && wrong+=" $ip"; done
assert_eq "" "$wrong" "private/public address classification"
finish

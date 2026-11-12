#!/usr/bin/env bash
# Mock-mode smoke test of moments 1 and 2 against a running stack (fresh DB: docker compose down -v first).
set -e
B=${B:-localhost:8000/api/v1}; D=$(mktemp -d); J='content-type: application/json'
for u in asha ben chloe; do curl -sf -c $D/$u -X POST $B/auth/demo-login/$u >/dev/null; done
pay() { # user
  R=$(curl -sf -b $D/$1 -X POST -H "$J" -d '{"kind":"SHARE","save_for_auto_debit":true}' $B/groups/1/pay)
  TOK=$(echo "$R" | python3 -c 'import sys,json,urllib.parse as u; d=json.load(sys.stdin); print(u.parse_qs(u.urlparse(d["approval_url"]).query)["token"][0])')
  curl -s -o /dev/null -w "  $1 pays -> %{http_code}\n" "$B/paypal/return?token=$TOK&PayerID=X"
}
state() { curl -sf -b $D/asha $B/groups/1 | python3 -c 'import sys,json; d=json.load(sys.stdin); c=d["cycle"]; print("  pot",d["pot"]["collected_minor"],d["pot"]["spent_minor"],d["pot"]["available_minor"],"| sub",d["subscription"]["status"],"| cycle",c["id"],c["status"],c["collected_minor"],"/",c["required_minor"]); [print("   ",m["name"],"paid" if m["is_paid"] else "unpaid",(m["last_attempt"] or {}).get("status"),(m["last_attempt"] or {}).get("error") or "") for m in d["members"]]'; }
run() { curl -sf -b $D/asha -X POST $B/groups/1/demo/run/$1 >/dev/null; sleep 4; echo "after $1:"; state; }
for u in asha ben; do curl -sf -b $D/$u -X PUT -H "$J" -d '{"payment_mode":"AUTO_DEBIT"}' $B/groups/1/me/payment-mode >/dev/null; done
echo "Moment 1: everyone pays cycle 1"; for u in asha ben chloe; do pay $u; done; state
run RENEW
curl -sf -b $D/asha -X PUT -H "$J" -d '{"payment_mode":"AUTO_DEBIT"}' $B/groups/1/me/payment-mode >/dev/null
curl -sf -b $D/asha -X POST -H "$J" -d '{"membership_id":2,"value":true}' $B/groups/1/demo/force-fail >/dev/null
echo "Moment 2: Ben's charge forced to fail"
run COLLECT
run CHECK
echo "Chloe inbox:"; curl -sf -b $D/chloe "$B/inbox?group_id=1" | python3 -c 'import sys,json; [print("   ",n["kind"],"|",n["title"],"|",n["body"][:150],"| ai:",n["ai_written"]) for n in json.load(sys.stdin)["items"][:3]]'
pay ben; pay chloe; state
run RENEW
curl -sf -b $D/asha $B/groups/1/ledger | python3 -c 'import sys,json; [print("   ",e["entry_type"],e["amount_minor"],e["source_label"],e["paypal_capture_id"],e["description"]) for e in json.load(sys.stdin)["entries"]]'

#!/usr/bin/env bash
# Mock-mode smoke test of E1 (leave + vote) and E2 (cover a missed share). Fresh DB: docker compose down -v first.
set -e
B=${B:-localhost:8000/api/v1}; D=$(mktemp -d); J='content-type: application/json'
login() { for u in asha ben chloe; do curl -sf -c $D/$u -X POST $B/auth/demo-login/$u >/dev/null; done; }
approve() { # user json-body
  R=$(curl -sf -b $D/$1 -X POST -H "$J" -d "$2" $B/groups/1/pay)
  TOK=$(echo "$R" | python3 -c 'import sys,json,urllib.parse as u; d=json.load(sys.stdin); print(u.parse_qs(u.urlparse(d["approval_url"]).query)["token"][0])')
  curl -s -o /dev/null -w "  $1 $2 -> %{http_code}\n" "$B/paypal/return?token=$TOK&PayerID=X"
}
pay() { approve $1 '{"kind":"SHARE"}'; }
state() { curl -sf -b $D/${1:-asha} $B/groups/1 | python3 -c 'import sys,json; d=json.load(sys.stdin); c=d["cycle"]; print("  sub",d["subscription"]["status"],"| cycle",c["id"],c["status"],c["collected_minor"],"/",c["required_minor"],"| decision",(d["decision"] or {}).get("status")); [print("   ",m["name"],m["share_minor"],"paid" if m["is_paid"] else "unpaid",m["share_note"] or "") for m in d["members"]]; [print("   ",x["name"],"(left) share",x["share_minor"],"paid",x["paid_minor"]) for x in d["departed"]]'; }
run() { curl -sf -b $D/asha -X POST $B/groups/1/demo/run/$1 >/dev/null; sleep 4; echo "after $1:"; state; }
inbox() { curl -sf -b $D/$1 "$B/inbox?group_id=1" | python3 -c 'import sys,json; [print("    '$1':",n["kind"],"|",n["title"]) for n in json.load(sys.stdin)["items"][:'${2:-3}'] if not n["read_at"]]'; }
login
echo "== E2: cycle 1, everyone pays"; for u in asha ben chloe; do pay $u; done; run RENEW
echo "== E2: cycle 2, Ben does not pay"; pay asha; pay chloe; run CHECK; inbox chloe
echo "Chloe covers Ben:"; approve chloe '{"kind":"COVER","covered_membership_id":2}'; state
run RENEW
echo "== E2: cycle 3, Ben (owes double) misses again"; pay asha; run CHECK; inbox asha 2
echo -n "  Chloe tries to cover again -> "; curl -s -o /dev/null -w "%{http_code}\n" -b $D/chloe -X POST -H "$J" -d '{"kind":"COVER","covered_membership_id":2}' $B/groups/1/pay
run RENEW
echo "== reset"; docker compose exec -T api python -m app.demo_seed --reset >/dev/null; login
echo "== E1: Asha paid, Ben leaves unpaid"; pay asha
curl -sf -b $D/ben -X POST $B/groups/1/leave; echo; inbox chloe 1; state
run COLLECT
for u in asha chloe; do curl -sf -b $D/$u -X POST -H "$J" -d '{"choice":"AGREE"}' $B/groups/1/decision/vote; echo; done; state
pay asha; pay chloe; run RENEW
curl -sf -b $D/asha $B/groups/1/ledger | python3 -c 'import sys,json; [print("   ",e["entry_type"],e["amount_minor"],e["source_label"],e["description"],"| for",e["on_behalf_of"]) for e in json.load(sys.stdin)["entries"]]'

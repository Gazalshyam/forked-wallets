#!/usr/bin/env bash
# Mock-mode smoke test of E3 (per-member pot, refund on leave) and E4 (task participants, sub-pot, consent).
# Fresh DB: docker compose down -v first (or run the demo reset).
set -e
B=${B:-localhost:8000/api/v1}; D=$(mktemp -d); J='content-type: application/json'
for u in asha ben chloe; do curl -sf -c $D/$u -X POST $B/auth/demo-login/$u >/dev/null; done
approve() {
  R=$(curl -sf -b $D/$1 -X POST -H "$J" -d "$2" $B/groups/1/pay)
  TOK=$(echo "$R" | python3 -c 'import sys,json,urllib.parse as u; d=json.load(sys.stdin); print(u.parse_qs(u.urlparse(d["approval_url"]).query)["token"][0])')
  curl -s -o /dev/null -w "  $1 $2 -> %{http_code}\n" "$B/paypal/return?token=$TOK&PayerID=X"
}
parts() { curl -sf -b $D/asha $B/groups/1 | python3 -c 'import sys,json; d=json.load(sys.stdin); print("  pot",d["pot"]); [print("   ",s["name"],"active" if s["active"] else "LEFT","free",s["free_minor"],"reserved",s["reserved_minor"],"committed",s["committed_minor"]) for s in d["pot_shares"]]'; }
task() { curl -sf -b $D/asha $B/tasks/$1 | python3 -c 'import sys,json; t=json.load(sys.stdin); print("  task",t["id"],t["status"],"price",t["final_price_minor"],"merged",t["merged_back_minor"]); [print("   ",p["name"],p["consent"],"forked",p["allocation_minor"]) for p in t["participants"]]; print("    declined",t["declined"])'; }
new() { curl -s -b $D/asha -X POST -H "$J" -d "{\"prompt\":\"Book a hotel in Austin for 2 nights under \$200\",\"budget_minor\":$1,\"participant_ids\":$2}" $B/groups/1/tasks; }
echo "== everyone tops up \$70 and pays the \$5 share"
for u in asha ben chloe; do approve $u '{"kind":"TOP_UP","amount_minor":7000}'; approve $u '{"kind":"SHARE"}'; done
parts
echo "== sub-pot: Asha+Ben only, \$150 budget is over their \$140 (this cycle's \$5 shares are kept for renewal) -> rejected"; new 15000 '[1,2]'; echo
echo "== Asha+Ben, \$140 budget; Ben agrees"
ID=$(new 14000 '[1,2]' | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])'); task $ID
curl -sf -b $D/ben -X POST -H "$J" -d '{"choice":"AGREE"}' $B/tasks/$ID/consent >/dev/null; sleep 7; task $ID; parts
echo "== Chloe tops up \$70 more; all three, \$140 (Asha and Ben have \$5 spare each, Chloe covers the rest); Chloe silent -> owner ends the wait"
approve chloe '{"kind":"TOP_UP","amount_minor":7000}'
ID=$(new 14000 null | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
curl -sf -b $D/ben -X POST -H "$J" -d '{"choice":"AGREE"}' $B/tasks/$ID/consent >/dev/null
echo -n "  Ben tries to leave during the task -> "; curl -s -o /dev/null -w "%{http_code}\n" -b $D/ben -X POST $B/groups/1/leave
curl -sf -b $D/asha -X POST $B/tasks/$ID/skip-wait; echo; sleep 7; task $ID; parts
echo "== Ben tops up \$30, then leaves: refund of his part above this cycle's share"
approve ben '{"kind":"TOP_UP","amount_minor":3000}'
curl -sf -b $D/ben -X POST $B/groups/1/leave; echo; sleep 4; parts
curl -sf -b $D/asha $B/groups/1/ledger | python3 -c 'import sys,json; d=json.load(sys.stdin); [print("   ",e["entry_type"],e["amount_minor"],e["source_label"],e["paypal_capture_id"] or "",e["paypal_refund_id"] or "",e["allocation"] or "") for e in d["entries"] if e["entry_type"] in ("FORK","SPEND","MERGE_BACK","REFUND","WRITE_OFF")]'
curl -sf -b $D/ben "$B/inbox" | python3 -c 'import sys,json; [print("    ben:",n["title"]) for n in json.load(sys.stdin)["items"][:6] if not n["read_at"]]'

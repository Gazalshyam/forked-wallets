#!/usr/bin/env bash
# Mock-mode smoke test of moment 3 against a running stack. Tops up the pot, then runs one task per booking mode.
set -e
B=${B:-localhost:8000/api/v1}; D=$(mktemp -d); J='content-type: application/json'
for u in asha ben chloe; do curl -sf -c $D/$u -X POST $B/auth/demo-login/$u >/dev/null; done
topup() {
  R=$(curl -sf -b $D/$1 -X POST -H "$J" -d "{\"kind\":\"TOP_UP\",\"amount_minor\":$2}" $B/groups/1/pay)
  TOK=$(echo "$R" | python3 -c 'import sys,json,urllib.parse as u; d=json.load(sys.stdin); print(u.parse_qs(u.urlparse(d["approval_url"]).query)["token"][0])')
  curl -s -o /dev/null -w "  $1 tops up $2 -> %{http_code}\n" "$B/paypal/return?token=$TOK&PayerID=X"
}
curl -sf -b $D/asha -X POST -H "$J" -d '{"prompt":"Book a hotel in Austin for 2 nights under $200"}' $B/groups/1/tasks/plan; echo
for MODE in CONFIRMED DECLINE_FIRST UNKNOWN; do
  echo "== $MODE"
  for u in asha ben chloe; do topup $u 7000; done
  ID=$(curl -sf -b $D/asha -X POST -H "$J" -d "{\"prompt\":\"Book a hotel in Austin for 2 nights under \$200\",\"budget_minor\":20000,\"booking_mode\":\"$MODE\"}" $B/groups/1/tasks | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
  for u in ben chloe; do curl -sf -b $D/$u -X POST -H "$J" -d '{"choice":"AGREE"}' $B/tasks/$ID/consent >/dev/null; done  # E4 consent
  sleep 8
  curl -sf -b $D/asha $B/tasks/$ID | python3 -c '
import sys,json; t=json.load(sys.stdin)
print("  status",t["status"],"winner",t["winner_agent"],"booking",t["booking_status"],t["booking_ref"],"price",t["final_price_minor"],"merged",t["merged_back_minor"],"payout",t["payout_label"],t["payout_batch_id"])
[print("   ",a["name"],a["status"],a["elapsed_ms"],[ (c["name"],c["total_minor"],c["status"]) for c in a["candidates"]]) for a in t["agents"]]
[print("    card",c["reference"],c["last4"],c["limit_minor"],c["status"]) for c in t["cards"]]
[print("    ev",e["kind"],e["msg"]) for e in t["events"]]
print("  pot",t["pot"])'
  if [ "$MODE" = UNKNOWN ]; then curl -sf -b $D/asha -X POST -H "$J" -d '{"booked":false}' $B/tasks/$ID/resolve; echo; fi
done
curl -sf -b $D/asha $B/groups/1/ledger | python3 -c 'import sys,json; d=json.load(sys.stdin); [print("   ",e["entry_type"],e["amount_minor"],e["source_label"],e["paypal_payout_batch_id"] or "",e["description"]) for e in d["entries"]]; print("  pot",d["pot"])'

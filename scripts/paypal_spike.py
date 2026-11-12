#!/usr/bin/env python3
"""PayPal Sandbox spike. Standard library only, so it runs outside Docker.

Usage:
    export PAYPAL_CLIENT_ID=...  PAYPAL_CLIENT_SECRET=...
    export DEMO_HOTEL_PAYPAL_EMAIL=hotel-business@...   (a US Business Sandbox account)
    python3 scripts/paypal_spike.py            # or: python3 scripts/paypal_spike.py --env .env

Steps (in order): token, create order (prints approval link, waits for Enter), capture,
saved-token (vault) charge, payout. Prints PASS / FAIL per step with the raw response on failure.
Never prints the client secret or the full vault token.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com").rstrip("/")
RESULTS: dict[str, str] = {}


def load_env_file(path: str) -> None:
    if not os.path.isfile(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def call(method: str, path: str, token: str | None = None, body=None, headers=None, form=None):
    url = BASE + path
    h = {"Accept": "application/json"}
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    if token:
        h["Authorization"] = f"Bearer {token}"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read().decode() or "{}"
            return r.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw}


def report(step: str, ok: bool, status: int | None = None, resp=None, note: str = "") -> None:
    RESULTS[step] = "PASS" if ok else "FAIL"
    tag = "PASS" if ok else "FAIL"
    print(f"[{tag}] {step} {('HTTP ' + str(status)) if status else ''} {note}".rstrip())
    if not ok and resp is not None:
        print(json.dumps(resp, indent=2)[:4000])


def link(resp: dict, *rels: str) -> str | None:
    for l in resp.get("links", []):
        if l.get("rel") in rels:
            return l.get("href")
    return None


def main() -> int:
    if "--env" in sys.argv:
        load_env_file(sys.argv[sys.argv.index("--env") + 1])
    cid, secret = os.getenv("PAYPAL_CLIENT_ID"), os.getenv("PAYPAL_CLIENT_SECRET")
    if not cid or not secret:
        print("Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET (Sandbox app credentials).")
        return 2
    print(f"PayPal base URL: {BASE}")

    # 1. Token
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    st, resp = call("POST", "/v1/oauth2/token", headers={"Authorization": f"Basic {basic}"},
                    form={"grant_type": "client_credentials"})
    token = resp.get("access_token") if isinstance(resp, dict) else None
    report("token", bool(token), st, None if token else resp,
           f"scopes include vault: {'vault' in resp.get('scope', '')}; payouts: {'payouts' in resp.get('scope', '')}" if token else "")
    if not token:
        return 1

    # 2. Create order with vault request
    attempt = f"spike-{uuid.uuid4().hex[:10]}"
    order_body = {
        "intent": "CAPTURE",
        "purchase_units": [{"reference_id": attempt, "custom_id": attempt,
                            "amount": {"currency_code": "USD", "value": "5.00"}}],
        "payment_source": {"paypal": {
            "experience_context": {"return_url": "https://example.com/return", "cancel_url": "https://example.com/cancel",
                                   "user_action": "PAY_NOW"},
            "attributes": {"vault": {"store_in_vault": "ON_SUCCESS", "usage_type": "MERCHANT", "customer_type": "CONSUMER"}},
        }},
    }
    st, order = call("POST", "/v2/checkout/orders", token, order_body, {"PayPal-Request-Id": attempt})
    approve = link(order, "payer-action", "approve")
    report("create_order(vault)", st in (200, 201) and bool(approve), st, order if not approve else None,
           f"id={order.get('id')} status={order.get('status')}")
    vault_id = None
    if approve:
        print("\nOpen this link, log in with a US *Personal* Sandbox buyer account and approve:")
        print("  " + approve)
        print("You will land on example.com (that is expected). Then press Enter here.")
        input()

        # 3. Capture
        st, cap = call("POST", f"/v2/checkout/orders/{order['id']}/capture", token, {},
                       {"PayPal-Request-Id": attempt + "-cap"})
        cap_status = cap.get("status")
        captures = (cap.get("purchase_units") or [{}])[0].get("payments", {}).get("captures", [])
        cap_id = captures[0]["id"] if captures else None
        report("capture", cap_status == "COMPLETED", st, cap if cap_status != "COMPLETED" else None,
               f"order_status={cap_status} capture_id={cap_id}")
        vault = cap.get("payment_source", {}).get("paypal", {}).get("attributes", {}).get("vault", {})
        vault_id = vault.get("id")
        print(f"       vault status={vault.get('status')} id={'<present>' if vault_id else None}")

    # 4. Saved-token charge
    if vault_id:
        k = f"spike-vault-{uuid.uuid4().hex[:10]}"
        st, v = call("POST", "/v2/checkout/orders", token, {
            "intent": "CAPTURE",
            "purchase_units": [{"reference_id": k, "custom_id": k, "amount": {"currency_code": "USD", "value": "5.00"}}],
            "payment_source": {"paypal": {"vault_id": vault_id}},
        }, {"PayPal-Request-Id": k})
        status = v.get("status")
        if status != "COMPLETED" and v.get("id"):
            st, v = call("POST", f"/v2/checkout/orders/{v['id']}/capture", token, {}, {"PayPal-Request-Id": k + "-cap"})
            status = v.get("status")
        report("vault_charge", status == "COMPLETED", st, v if status != "COMPLETED" else None, f"status={status}")
    else:
        report("vault_charge", False, None, None, "skipped: no vault id returned by capture (vaulting not enabled for this app?)")

    # 5. Payout
    hotel = os.getenv("DEMO_HOTEL_PAYPAL_EMAIL")
    if hotel:
        batch = f"spike-payout-{uuid.uuid4().hex[:10]}"
        st, p = call("POST", "/v1/payments/payouts", token, {
            "sender_batch_header": {"sender_batch_id": batch, "email_subject": "Forked Wallets demo payout"},
            "items": [{"recipient_type": "EMAIL", "receiver": hotel, "note": "Demo hotel booking",
                       "sender_item_id": batch + "-1", "amount": {"value": "1.00", "currency": "USD"}}],
        })
        bid = p.get("batch_header", {}).get("payout_batch_id")
        report("payout", st in (200, 201) and bool(bid), st, p if not bid else None,
               f"batch_id={bid} status={p.get('batch_header', {}).get('batch_status')}")
    else:
        report("payout", False, None, None, "skipped: DEMO_HOTEL_PAYPAL_EMAIL not set")

    print("\nSummary:", "  ".join(f"{k}: {v}" for k, v in RESULTS.items()))
    print("Record these in PROGRESS.md under 'PayPal spike results'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

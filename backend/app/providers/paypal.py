"""PayPal provider (Sandbox REST via httpx) and a mock with the same interface.

Every request body is built here from values the backend passes in (database values). AI never calls this.
"""
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field

import httpx

from ..config import get_settings
from ..models import LABEL_MOCK_PAYPAL, LABEL_PAYPAL
from ..services.money import to_paypal_value

log = logging.getLogger("paypal")


class PayPalError(Exception):
    def __init__(self, message: str, status: int | None = None, body: dict | None = None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


@dataclass
class OrderResult:
    order_id: str
    status: str
    approval_url: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class CaptureResult:
    order_id: str
    status: str  # PayPal order status; only "COMPLETED" counts
    capture_id: str | None = None
    capture_status: str | None = None
    vault_id: str | None = None
    raw: dict = field(default_factory=dict)

    @property
    def completed(self) -> bool:
        # Both the order and (when present) the capture itself must be COMPLETED.
        return self.status == "COMPLETED" and (self.capture_status in (None, "COMPLETED"))


@dataclass
class PayoutResult:
    batch_id: str | None
    status: str
    raw: dict = field(default_factory=dict)


@dataclass
class RefundResult:
    refund_id: str | None
    status: str  # COMPLETED | PENDING | FAILED | CANCELLED; only COMPLETED counts as returned
    raw: dict = field(default_factory=dict)


def _extract_capture(resp: dict) -> tuple[str | None, str | None]:
    pus = resp.get("purchase_units") or []
    caps = (pus[0].get("payments", {}).get("captures", []) if pus else [])
    if caps:
        return caps[0].get("id"), caps[0].get("status")
    return None, None


def _extract_vault_id(resp: dict) -> str | None:
    return (resp.get("payment_source", {}).get("paypal", {}).get("attributes", {}).get("vault", {}).get("id"))


def _approval_link(resp: dict) -> str | None:
    for rel in ("payer-action", "approve"):
        for l in resp.get("links", []):
            if l.get("rel") == rel:
                return l.get("href")
    return None


class PayPalProvider:
    label = LABEL_PAYPAL
    _lock = threading.Lock()
    _token: str | None = None
    _token_exp: float = 0.0

    def __init__(self):
        s = get_settings()
        self.base = s.paypal_base_url
        self.client_id = s.paypal_client_id
        self.secret = s.paypal_client_secret
        self.timeout = httpx.Timeout(30.0)

    # ---- low level ----
    def _access_token(self) -> str:
        with PayPalProvider._lock:
            if PayPalProvider._token and time.time() < PayPalProvider._token_exp - 60:
                return PayPalProvider._token
            if not self.client_id or not self.secret:
                raise PayPalError("PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set")
            r = httpx.post(f"{self.base}/v1/oauth2/token", auth=(self.client_id, self.secret),
                           data={"grant_type": "client_credentials"}, headers={"Accept": "application/json"},
                           timeout=self.timeout)
            if r.status_code != 200:
                raise PayPalError(f"PayPal token request failed (HTTP {r.status_code})", r.status_code, _safe_json(r))
            body = r.json()
            PayPalProvider._token = body["access_token"]
            PayPalProvider._token_exp = time.time() + int(body.get("expires_in", 300))
            return PayPalProvider._token

    def _call(self, method: str, path: str, json_body: dict | None = None, request_id: str | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json",
                   "Accept": "application/json", "Prefer": "return=representation"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        try:
            r = httpx.request(method, f"{self.base}{path}", json=json_body, headers=headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise PayPalError(f"PayPal network error: {e.__class__.__name__}") from e
        body = _safe_json(r)
        log.info("PayPal %s %s -> HTTP %s debug_id=%s", method, path, r.status_code, r.headers.get("paypal-debug-id"))
        if r.status_code >= 400:
            detail = body.get("details", [{}])[0].get("issue") if body.get("details") else body.get("name")
            raise PayPalError(f"PayPal {path} failed: HTTP {r.status_code} {detail or ''}".strip(), r.status_code, body)
        return body

    # ---- interface ----
    def create_order(self, *, amount_minor: int, reference_id: str, request_id: str, vault: bool,
                     return_url: str, cancel_url: str) -> OrderResult:
        paypal_src = {"experience_context": {"return_url": return_url, "cancel_url": cancel_url, "user_action": "PAY_NOW",
                                             "shipping_preference": "NO_SHIPPING", "brand_name": "Forked Wallets"}}
        if vault:
            paypal_src["attributes"] = {"vault": {"store_in_vault": "ON_SUCCESS", "usage_type": "MERCHANT",
                                                  "customer_type": "CONSUMER"}}
        body = {"intent": "CAPTURE",
                "purchase_units": [{"reference_id": reference_id, "custom_id": reference_id,
                                    "amount": {"currency_code": "USD", "value": to_paypal_value(amount_minor)}}],
                "payment_source": {"paypal": paypal_src}}
        resp = self._call("POST", "/v2/checkout/orders", body, request_id)
        return OrderResult(order_id=resp["id"], status=resp.get("status", ""), approval_url=_approval_link(resp), raw=resp)

    def capture_order(self, *, order_id: str, request_id: str) -> CaptureResult:
        resp = self._call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id)
        cid, cst = _extract_capture(resp)
        return CaptureResult(order_id=order_id, status=resp.get("status", ""), capture_id=cid, capture_status=cst,
                             vault_id=_extract_vault_id(resp), raw=_redact(resp))

    def charge_saved_token(self, *, vault_id: str, amount_minor: int, reference_id: str, request_id: str) -> CaptureResult:
        body = {"intent": "CAPTURE",
                "purchase_units": [{"reference_id": reference_id, "custom_id": reference_id,
                                    "amount": {"currency_code": "USD", "value": to_paypal_value(amount_minor)}}],
                "payment_source": {"paypal": {"vault_id": vault_id}}}
        resp = self._call("POST", "/v2/checkout/orders", body, request_id)
        order_id = resp["id"]
        if resp.get("status") != "COMPLETED":
            # Some accounts return APPROVED for vaulted orders; try capture exactly once.
            return self.capture_order(order_id=order_id, request_id=request_id + "-cap")
        cid, cst = _extract_capture(resp)
        return CaptureResult(order_id=order_id, status="COMPLETED", capture_id=cid, capture_status=cst, raw=_redact(resp))

    def payout(self, *, receiver_email: str, amount_minor: int, sender_batch_id: str, note: str) -> PayoutResult:
        body = {"sender_batch_header": {"sender_batch_id": sender_batch_id,
                                        "email_subject": "Forked Wallets demo booking payment"},
                "items": [{"recipient_type": "EMAIL", "receiver": receiver_email, "note": note,
                           "sender_item_id": sender_batch_id + "-1",
                           "amount": {"value": to_paypal_value(amount_minor), "currency": "USD"}}]}
        resp = self._call("POST", "/v1/payments/payouts", body)
        bh = resp.get("batch_header", {})
        return PayoutResult(batch_id=bh.get("payout_batch_id"), status=bh.get("batch_status", "UNKNOWN"), raw=resp)

    def refund_capture(self, *, capture_id: str, amount_minor: int, request_id: str, note: str) -> RefundResult:
        """E3: return part of an earlier capture to the payer's PayPal account. Partial refunds are allowed up to
        the capture amount. Shape per PayPal docs (Payments v2); not yet confirmed by the spike."""
        body = {"amount": {"value": to_paypal_value(amount_minor), "currency_code": "USD"},
                "note_to_payer": note[:255]}
        resp = self._call("POST", f"/v2/payments/captures/{capture_id}/refund", body, request_id)
        return RefundResult(refund_id=resp.get("id"), status=resp.get("status", "UNKNOWN"), raw=resp)


class MockPayPalProvider:
    """Same interface, no network. Everything it produces is labelled MOCK PAYPAL."""

    label = LABEL_MOCK_PAYPAL

    def create_order(self, *, amount_minor: int, reference_id: str, request_id: str, vault: bool,
                     return_url: str, cancel_url: str) -> OrderResult:
        oid = f"MOCK-{'V' if vault else 'O'}-{uuid.uuid5(uuid.NAMESPACE_URL, request_id).hex[:16].upper()}"
        url = f"/mock-paypal?token={oid}&amount={to_paypal_value(amount_minor)}"
        return OrderResult(order_id=oid, status="PAYER_ACTION_REQUIRED", approval_url=url, raw={"mock": True})

    def capture_order(self, *, order_id: str, request_id: str) -> CaptureResult:
        vault_id = f"MOCK-VAULT-{uuid.uuid4().hex[:12].upper()}" if order_id.startswith("MOCK-V-") else None
        return CaptureResult(order_id=order_id, status="COMPLETED", capture_id=f"MOCK-CAP-{uuid.uuid4().hex[:12].upper()}",
                             capture_status="COMPLETED", vault_id=vault_id, raw={"mock": True})

    def charge_saved_token(self, *, vault_id: str, amount_minor: int, reference_id: str, request_id: str) -> CaptureResult:
        oid = f"MOCK-O-{uuid.uuid5(uuid.NAMESPACE_URL, request_id).hex[:16].upper()}"
        return CaptureResult(order_id=oid, status="COMPLETED", capture_id=f"MOCK-CAP-{uuid.uuid4().hex[:12].upper()}",
                             capture_status="COMPLETED", raw={"mock": True})

    def payout(self, *, receiver_email: str, amount_minor: int, sender_batch_id: str, note: str) -> PayoutResult:
        return PayoutResult(batch_id=f"MOCK-PAYOUT-{uuid.uuid4().hex[:10].upper()}", status="SUCCESS", raw={"mock": True})

    def refund_capture(self, *, capture_id: str, amount_minor: int, request_id: str, note: str) -> RefundResult:
        rid = f"MOCK-REFUND-{uuid.uuid5(uuid.NAMESPACE_URL, request_id).hex[:12].upper()}"
        return RefundResult(refund_id=rid, status="COMPLETED", raw={"mock": True})


def _safe_json(r: httpx.Response) -> dict:
    try:
        v = r.json()
        return v if isinstance(v, dict) else {"body": v}
    except ValueError:
        return {"raw": r.text[:500]}


def _redact(resp: dict) -> dict:
    """Drop the vault token from anything we might store or log."""
    import copy

    r = copy.deepcopy(resp)
    v = r.get("payment_source", {}).get("paypal", {}).get("attributes", {}).get("vault")
    if isinstance(v, dict) and "id" in v:
        v["id"] = "<redacted>"
    return r


def get_provider():
    return PayPalProvider() if get_settings().paypal_real else MockPayPalProvider()

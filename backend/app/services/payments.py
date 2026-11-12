"""Contribution flows: approval order -> capture, and saved-token auto-debit.

Hard rules enforced here:
- Amounts come from the cycle share snapshot in the database (or a user-entered top-up), never from AI.
- Only a capture whose status is COMPLETED writes a CONTRIBUTION ledger row.
- Every attempt and ledger row has a unique idempotency key.
"""
import logging
import uuid

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import LABEL_SIMULATED, ContributionAttempt, Cycle, Group, Membership, User
from ..providers.paypal import CaptureResult, PayPalError, get_provider
from ..security import decrypt_token, encrypt_token
from .ledger import member_paid_in_cycle, write_entry
from .money import fmt_usd
from .notify import notify

log = logging.getLogger("payments")

MAX_TOP_UP_MINOR = 1000_00


def outstanding_share(db: Session, cycle: Cycle, membership_id: int) -> int:
    share = int(cycle.shares.get(str(membership_id), 0))
    return max(0, share - member_paid_in_cycle(db, cycle.id).get(membership_id, 0))


def was_covered_in_previous_cycle(db: Session, cycle: Cycle, membership_id: int) -> bool:
    from .groups import completed_covers, previous_cycle

    prev = previous_cycle(db, cycle.subscription_id, cycle.renewal_date)
    return prev is not None and any(a.covered_membership_id == membership_id for a in completed_covers(db, prev.id))


def cover_block_reason(db: Session, cycle: Cycle | None, payer: Membership, debtor: Membership | None) -> str | None:
    """E2 policy, checked in backend code: why `payer` may not cover `debtor` in this cycle (None means allowed)."""
    if cycle is None or cycle.status in ("FUNDED", "RENEWED"):
        return "there is no unpaid cycle to cover"
    if debtor is None or debtor.group_id != payer.group_id or debtor.status != "ACTIVE":
        return "that member is not in this group"
    if debtor.id == payer.id:
        return "use \"Pay my share\" for your own share"
    if outstanding_share(db, cycle, debtor.id) <= 0:
        return "that share is already paid"
    if was_covered_in_previous_cycle(db, cycle, debtor.id):
        return "their share was covered last cycle; a second miss in a row pauses the subscription instead"
    return None


def auto_debit_available() -> bool:
    s = get_settings()
    return (not s.paypal_real) or s.paypal_vault_enabled


def start_approval_payment(db: Session, *, membership: Membership, cycle: Cycle | None, kind: str,
                           amount_minor: int | None = None, want_vault: bool = False,
                           covered_membership_id: int | None = None) -> ContributionAttempt:
    """Create a PayPal order and return the attempt (with approval_url)."""
    s = get_settings()
    provider = get_provider()
    covered = None
    if kind == "COVER":
        debtor = db.get(Membership, covered_membership_id) if covered_membership_id else None
        reason = cover_block_reason(db, cycle, membership, debtor)
        if reason:
            raise ValueError(f"Cannot cover this share: {reason}")
        covered = debtor.id
        amount = outstanding_share(db, cycle, debtor.id)  # from the cycle snapshot, never from the client
        want_vault = False
        pending = db.scalar(select(ContributionAttempt).where(
            ContributionAttempt.cycle_id == cycle.id, ContributionAttempt.membership_id == membership.id,
            ContributionAttempt.covered_membership_id == debtor.id, ContributionAttempt.status == "APPROVAL_PENDING",
            ContributionAttempt.amount_minor == amount, ContributionAttempt.source_label == provider.label)
            .order_by(desc(ContributionAttempt.id)).limit(1))
        if pending and pending.approval_url:
            return pending
    elif kind == "SHARE":
        if cycle is None:
            raise ValueError("no open cycle")
        if cycle.status in ("RENEWED",):
            raise ValueError("this cycle is already renewed")
        amount = outstanding_share(db, cycle, membership.id)
        if amount <= 0:
            raise ValueError("your share for this cycle is already paid")
        pending = db.scalar(select(ContributionAttempt).where(
            ContributionAttempt.cycle_id == cycle.id, ContributionAttempt.membership_id == membership.id,
            ContributionAttempt.status == "APPROVAL_PENDING", ContributionAttempt.amount_minor == amount,
            ContributionAttempt.source_label == provider.label).order_by(desc(ContributionAttempt.id)).limit(1))
        if pending and pending.approval_url:
            return pending
    elif kind == "TOP_UP":
        if not isinstance(amount_minor, int) or amount_minor <= 0 or amount_minor > MAX_TOP_UP_MINOR:
            raise ValueError(f"top-up must be between $0.01 and {fmt_usd(MAX_TOP_UP_MINOR)}")
        amount, cycle = amount_minor, None
        want_vault = False
    else:
        raise ValueError("unknown payment kind")

    vault = bool(want_vault and membership.payment_mode == "AUTO_DEBIT" and auto_debit_available())
    key = f"attempt-{uuid.uuid4().hex}"
    a = ContributionAttempt(group_id=membership.group_id, cycle_id=cycle.id if cycle else None, membership_id=membership.id,
                            amount_minor=amount, kind=kind, method="APPROVAL", want_vault=vault, status="CREATED",
                            source_label=provider.label, idempotency_key=key, covered_membership_id=covered)
    db.add(a)
    db.flush()
    try:
        order = provider.create_order(
            amount_minor=amount, reference_id=f"fw-{a.id}", request_id=key, vault=vault,
            return_url=f"{s.app_base_url}/api/v1/paypal/return", cancel_url=f"{s.app_base_url}/api/v1/paypal/cancel")
    except PayPalError as e:
        a.status, a.error = "FAILED", str(e)
        db.flush()
        raise ValueError(f"Could not create the PayPal order: {e}") from e
    a.paypal_order_id = order.order_id
    a.approval_url = order.approval_url
    a.status = "APPROVAL_PENDING"
    db.flush()
    return a


def _record_capture(db: Session, a: ContributionAttempt, cap: CaptureResult) -> bool:
    """Write the CONTRIBUTION row only for a COMPLETED capture. Returns True if collected."""
    a.paypal_order_id = cap.order_id or a.paypal_order_id
    a.paypal_capture_id = cap.capture_id
    if not cap.completed:
        a.status = "FAILED"
        a.error = f"PayPal returned status {cap.status}/{cap.capture_status}; not counted as collected"
        db.flush()
        return False
    a.status = "COMPLETED"
    a.error = None
    m = db.get(Membership, a.membership_id)
    u = db.get(User, m.user_id)
    how = "auto-debit (saved token)" if a.method == "AUTO_DEBIT" else "approved order"
    if a.kind == "COVER":
        from .groups import member_name

        desc_text = (f"{u.name} covered {member_name(db, a.covered_membership_id)}'s share via {how} "
                     "(settled next cycle)")
    else:
        desc_text = f"{u.name}'s {'share' if a.kind == 'SHARE' else 'top-up'} via {how}"
    write_entry(db, group_id=a.group_id, entry_type="CONTRIBUTION", amount_minor=a.amount_minor,
                source_label=a.source_label, idempotency_key=f"contribution-{a.id}",
                description=desc_text, membership_id=m.id, on_behalf_of_membership_id=a.covered_membership_id,
                cycle_id=a.cycle_id, attempt_id=a.id,
                paypal_order_id=a.paypal_order_id, paypal_capture_id=a.paypal_capture_id)
    if cap.vault_id and a.want_vault:
        m.vault_token_enc = encrypt_token(cap.vault_id)
        m.vault_label = a.source_label
    db.flush()
    from .cycles import after_contribution  # local import to avoid a cycle

    after_contribution(db, a)
    return True


def complete_approval(db: Session, order_id: str) -> ContributionAttempt:
    """Called when the payer returns from PayPal. Idempotent."""
    q = select(ContributionAttempt).where(ContributionAttempt.paypal_order_id == order_id)
    if db.bind.dialect.name == "postgresql":
        q = q.with_for_update()
    a = db.scalar(q)
    if a is None:
        raise ValueError("Unknown PayPal order")
    if a.status in ("COMPLETED", "FAILED", "CANCELLED"):
        return a
    provider = get_provider()
    if provider.label != a.source_label:
        a.status, a.error = "FAILED", "Payment provider changed since this order was created"
        db.flush()
        return a
    if a.kind in ("SHARE", "COVER") and a.cycle_id:
        # Do not capture a share that was settled meanwhile (the member paid, or someone covered it).
        # An approved but uncaptured PayPal order moves no money.
        whose = a.covered_membership_id or a.membership_id
        if outstanding_share(db, db.get(Cycle, a.cycle_id), whose) < a.amount_minor:
            a.status = "CANCELLED"
            a.error = "That share was already paid, so this order was not captured. Nothing was charged."
            db.flush()
            return a
    try:
        cap = provider.capture_order(order_id=order_id, request_id=f"{a.idempotency_key}-capture")
    except PayPalError as e:
        a.status, a.error = "FAILED", str(e)
        db.flush()
        return a
    _record_capture(db, a, cap)
    return a


def cancel_approval(db: Session, order_id: str) -> ContributionAttempt | None:
    a = db.scalar(select(ContributionAttempt).where(ContributionAttempt.paypal_order_id == order_id))
    if a and a.status == "APPROVAL_PENDING":
        a.status, a.error = "CANCELLED", "Payer cancelled at PayPal"
        db.flush()
    return a


def _fallback_to_approval(db: Session, m: Membership, cycle: Cycle, reason: str) -> None:
    u = db.get(User, m.user_id)
    g = db.get(Group, m.group_id)
    amount = outstanding_share(db, cycle, m.id)
    notify(db, user_id=u.id, group_id=g.id, kind="PAY_SHARE",
           title=f"Pay your {fmt_usd(amount)} share for {g.name}",
           body=f"{reason} Tap \"Pay my share\" to approve this cycle's payment in PayPal.",
           action={"type": "PAY_SHARE", "cycle_id": cycle.id}, dedupe_key=f"payshare-{cycle.id}-{m.id}-{reason[:20]}")


def auto_debit(db: Session, m: Membership, cycle: Cycle) -> ContributionAttempt | None:
    """Charge the saved token. Falls back to an approval request on any failure."""
    amount = outstanding_share(db, cycle, m.id)
    if amount <= 0:
        return None
    provider = get_provider()
    if not auto_debit_available():
        _fallback_to_approval(db, m, cycle, "Auto-debit is not enabled for this PayPal app, so this cycle needs your approval.")
        return None
    if not m.vault_token_enc or m.vault_label != provider.label:
        _fallback_to_approval(db, m, cycle, "No saved PayPal token yet. Approve once and tick 'save for auto-debit'.")
        return None
    key = f"autodebit-{cycle.id}-{m.id}-{uuid.uuid4().hex[:8]}"
    a = ContributionAttempt(group_id=m.group_id, cycle_id=cycle.id, membership_id=m.id, amount_minor=amount, kind="SHARE",
                            method="AUTO_DEBIT", status="CREATED", source_label=provider.label, idempotency_key=key)
    db.add(a)
    db.flush()
    if m.force_fail_next:
        m.force_fail_next = False
        a.status = "FAILED"
        a.source_label = LABEL_SIMULATED
        a.error = "Forced failure (demo control). No PayPal call was made."
        db.flush()
        _fallback_to_approval(db, m, cycle, "Your automatic payment did not go through.")
        log.info("Auto-debit for membership %s forced to fail (demo)", m.id)
        return a
    try:
        cap = provider.charge_saved_token(vault_id=decrypt_token(m.vault_token_enc), amount_minor=amount,
                                          reference_id=f"fw-{a.id}", request_id=key)
    except PayPalError as e:
        a.status, a.error = "FAILED", str(e)
        db.flush()
        _fallback_to_approval(db, m, cycle, "Your automatic payment did not go through.")
        return a
    if not _record_capture(db, a, cap):
        _fallback_to_approval(db, m, cycle, "Your automatic payment did not go through.")
    return a

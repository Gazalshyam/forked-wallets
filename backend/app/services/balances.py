"""E3: each member's part of the pot, the PayPal refund when a member leaves, and the write-off of a leaver's debt.

Rules (backend code, never AI):
- A member's part of the pot = what they paid in, minus their share of every spend and open reservation
  (see ledger.member_balances). A COVER payment is the payer's money; the covered member's share of the renewal
  is charged to the covered member, so their part goes negative until they repay next cycle.
- On leaving, money already committed to the current cycle (their paid share, which renewal will spend) is not
  refunded. Anything above that is refunded through PayPal against their own earlier captures (newest first).
  Only a refund PayPal reports as COMPLETED writes a REFUND ledger row.
- If they leave owing money, nothing is charged: the debt is written off onto the members whose money covered it,
  in proportion to their parts of the pot, and everyone gets a notification.
- A member cannot leave while a task that uses their money is running.
"""
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    LABEL_SIMULATED,
    ContributionAttempt,
    Group,
    LedgerEntry,
    Membership,
    Subscription,
    Task,
    User,
    utcnow,
)
from ..providers.paypal import PayPalError, get_provider
from .groups import active_members, current_cycle, member_name, spend_allocation
from .ledger import member_balances, member_paid_in_cycle, write_entry
from .money import equal_split, fmt_usd, proportional_split
from .notify import notify

log = logging.getLogger("balances")

ACTIVE_TASK_STATES = ("AWAITING_CONSENT", "SEARCHING", "LOCKED", "BOOKING", "NEEDS_REVIEW")


def tasks_using(db: Session, m: Membership) -> list[Task]:
    """Unfinished tasks whose sub-pot includes this member's money."""
    out = []
    for t in db.scalars(select(Task).where(Task.group_id == m.group_id, Task.status.in_(ACTIVE_TASK_STATES))):
        if t.participants is None or m.id in [int(x) for x in t.participants]:
            out.append(t)
    return out


def committed_to_current_cycle(db: Session, membership_id: int, group_id: int) -> int:
    """Cents this member has paid toward the current, not yet renewed cycle. Renewal will spend them."""
    sub = db.scalar(select(Subscription).where(Subscription.group_id == group_id))
    cycle = current_cycle(db, sub) if sub else None
    if cycle is None or cycle.status == "RENEWED":
        return 0
    cost = int(spend_allocation(cycle).get(str(membership_id), 0))
    return max(0, min(cost, member_paid_in_cycle(db, cycle.id).get(membership_id, 0)))


def projected(db: Session, group_id: int) -> dict[int, int]:
    """Each member's free part of the pot after the current cycle renews (membership id -> cents, may be < 0)."""
    bal = member_balances(db, group_id)
    mids = db.scalars(select(Membership.id).where(Membership.group_id == group_id))
    return {mid: bal.get(mid, {}).get("free_minor", 0) - committed_to_current_cycle(db, mid, group_id) for mid in mids}


def pot_shares(db: Session, group_id: int) -> list[dict]:
    """For the Group page pie chart: everyone with money in the pot, active members first."""
    bal = member_balances(db, group_id)
    active = {m.id for m in active_members(db, group_id)}
    out = []
    for m in db.scalars(select(Membership).where(Membership.group_id == group_id).order_by(Membership.id)):
        b = bal.get(m.id, {"free_minor": 0, "reserved_minor": 0})
        if m.id not in active and b["free_minor"] == 0 and b["reserved_minor"] == 0:
            continue
        out.append({"membership_id": m.id, "name": member_name(db, m.id), "active": m.id in active,
                    "free_minor": b["free_minor"], "reserved_minor": b["reserved_minor"],
                    "committed_minor": committed_to_current_cycle(db, m.id, group_id)})
    out.sort(key=lambda r: (not r["active"], r["membership_id"]))
    return out


# ---------------------------------------------------------------- leaving
def settle_leaver(db: Session, m: Membership, amount_minor: int) -> None:
    """Called by decisions.leave_group after the member is marked LEFT. amount > 0: refund; < 0: write off."""
    g = db.get(Group, m.group_id)
    name = member_name(db, m.id)
    key = f"leave-{m.id}-{m.left_at.strftime('%Y%m%d%H%M%S%f')}"
    if amount_minor > 0:
        from .cycles import enqueue

        enqueue(db, "REFUND_LEAVER", {"membership_id": m.id, "amount_minor": amount_minor, "key": key},
                dedupe_key=f"REFUND-{key}")
        notify(db, user_id=m.user_id, group_id=g.id, kind="INFO", title=f"Refund of {fmt_usd(amount_minor)} requested",
               body=f"Your part of {g.name}'s pot that is not committed to the current cycle goes back to your PayPal "
                    "account. You will get another message when PayPal confirms it.", dedupe_key=f"refund-req-{key}")
        return
    if amount_minor == 0:
        return
    debt = -amount_minor
    others = active_members(db, g.id)
    if not others:
        return
    proj = projected(db, g.id)
    weights = {o.id: max(0, proj.get(o.id, 0)) for o in others}
    split = proportional_split(debt, weights) if sum(weights.values()) else equal_split(debt, [o.id for o in others])
    alloc = {str(m.id): -debt, **{str(k): v for k, v in split.items() if v}}
    who = ", ".join(f"{member_name(db, k)} {fmt_usd(v)}" for k, v in split.items() if v)
    write_entry(db, group_id=g.id, entry_type="WRITE_OFF", amount_minor=debt, source_label=LABEL_SIMULATED,
                idempotency_key=f"writeoff-{key}", membership_id=m.id, allocation=alloc,
                description=f"{name} left owing {fmt_usd(debt)}; written off ({who}). No money moves.")
    notify(db, user_id=m.user_id, group_id=g.id, kind="INFO", title=f"You left {g.name} owing {fmt_usd(debt)}",
           body="Nothing is charged and nothing is refunded. The group has been told the amount is written off.",
           dedupe_key=f"debt-self-{key}")
    for o in others:
        notify(db, user_id=o.user_id, group_id=g.id, kind="INFO", title=f"{name} left owing {fmt_usd(debt)}",
               body=f"{name}'s share had been covered and was not repaid. Forked Wallets does not chase the debt: "
                    f"it is written off against the parts of the pot that covered it ({who}).",
               dedupe_key=f"debt-{key}-{o.id}")


def job_refund_leaver(db: Session, payload: dict) -> None:
    m = db.get(Membership, int(payload["membership_id"]))
    amount, key = int(payload["amount_minor"]), payload["key"]
    g = db.get(Group, m.group_id)
    name = db.get(User, m.user_id).name
    provider = get_provider()
    prior = db.scalars(select(ContributionAttempt).where(ContributionAttempt.kind == "REFUND",
                                                         ContributionAttempt.idempotency_key.like(f"{key}-%")))
    left = amount - sum(a.amount_minor for a in prior if a.status in ("COMPLETED", "PENDING"))
    done = pending = 0
    caps = db.scalars(select(LedgerEntry).where(LedgerEntry.group_id == g.id, LedgerEntry.entry_type == "CONTRIBUTION",
                                                LedgerEntry.membership_id == m.id, LedgerEntry.paypal_capture_id.isnot(None),
                                                LedgerEntry.source_label == provider.label).order_by(LedgerEntry.id.desc()))
    for row in caps:
        if left <= 0:
            break
        used = db.scalar(select(func.coalesce(func.sum(ContributionAttempt.amount_minor), 0)).where(
            ContributionAttempt.kind == "REFUND", ContributionAttempt.paypal_capture_id == row.paypal_capture_id,
            ContributionAttempt.status.in_(("COMPLETED", "PENDING"))))
        take = min(left, row.amount_minor - int(used or 0))
        if take <= 0:
            continue
        ikey = f"{key}-{row.paypal_capture_id}"
        if db.scalar(select(ContributionAttempt).where(ContributionAttempt.idempotency_key == ikey)):
            continue  # an earlier run already tried this capture and it failed; do not retry blindly
        a = ContributionAttempt(group_id=g.id, cycle_id=None, membership_id=m.id, amount_minor=take, kind="REFUND",
                                method="REFUND", status="CREATED", source_label=provider.label,
                                paypal_capture_id=row.paypal_capture_id, idempotency_key=ikey)
        db.add(a)
        db.flush()
        try:
            res = provider.refund_capture(capture_id=row.paypal_capture_id, amount_minor=take, request_id=ikey,
                                          note=f"Refund from {g.name}: you left the group")
        except PayPalError as e:
            a.status, a.error = "FAILED", str(e)[:500]
            db.flush()
            continue
        a.paypal_refund_id = res.refund_id
        if res.status == "COMPLETED":
            a.status = "COMPLETED"
            write_entry(db, group_id=g.id, entry_type="REFUND", amount_minor=take, source_label=provider.label,
                        idempotency_key=f"refund-{a.id}", membership_id=m.id, attempt_id=a.id,
                        paypal_capture_id=row.paypal_capture_id, paypal_refund_id=res.refund_id,
                        description=f"Refund to {name} (left the group) against capture {row.paypal_capture_id}")
            done += take
        elif res.status == "PENDING":
            a.status, a.error = "PENDING", "PayPal reports the refund as PENDING; not counted until COMPLETED"
            pending += take
        else:
            a.status, a.error = "FAILED", f"PayPal refund status {res.status}"
            db.flush()
            continue
        left -= take
        db.flush()
    parts = [f"{fmt_usd(done)} refunded ({provider.label})"] if done else []
    if pending:
        parts.append(f"{fmt_usd(pending)} pending at PayPal")
    if left > 0:
        parts.append(f"{fmt_usd(left)} could not be refunded automatically and stays in the pot under your name")
    summary = "; ".join(parts) or "nothing to refund"
    notify(db, user_id=m.user_id, group_id=g.id, kind="INFO", title=f"Refund from {g.name}: {summary.split(';')[0]}",
           body=summary + ".", dedupe_key=f"refund-done-{key}")
    owner = db.scalar(select(Membership).where(Membership.group_id == g.id, Membership.role == "OWNER"))
    if owner:
        notify(db, user_id=owner.user_id, group_id=g.id, kind="INFO", title=f"{name}'s refund: {summary.split(';')[0]}",
               body=f"{name} left {g.name}. {summary}.", dedupe_key=f"refund-owner-{key}")
    log.info("Refund for membership %s: done=%s pending=%s left=%s at %s", m.id, done, pending, left, utcnow())


JOB_HANDLERS = {"REFUND_LEAVER": job_refund_leaver}

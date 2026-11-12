"""E1: leaving a group, and the remaining members' decision: raise the shares or pause the subscription.

Rules (enforced here, in backend code):
- Nobody's share is raised without consent. The new split applies only when every remaining member agrees.
- One "Pause" vote pauses the subscription. The owner can ask the group again later.
- While a decision is open or paused, COLLECT charges nobody for the affected cycle and RENEW does not renew it
  (see `blocks_cycle`, used by services/cycles.py).
- Money a leaver paid toward the current cycle stays in the pot (renewal spends it). The rest of their part of the
  pot is refunded through PayPal; a debt is written off with a notification (E3, services/balances.py).
- A member cannot leave while a task that uses their money is running. The owner cannot leave in this build.
"""
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ContributionAttempt, Cycle, Group, Membership, Notification, Subscription, User, utcnow
from .groups import Forbidden, active_members, adjustments, apply_cover_repayments, current_cycle, member_name
from .ledger import cycle_collected, member_paid_in_cycle
from .money import equal_split, fmt_usd
from .notify import notify

OPEN_STATES = ("OPEN", "PAUSED")


def blocks_cycle(sub: Subscription, cycle: Cycle) -> bool:
    """True when an unresolved decision means this cycle's shares are not agreed yet."""
    d = sub.decision
    if not d or d.get("status") not in OPEN_STATES:
        return False
    # A leaver who already paid the current cycle leaves it intact; only the next cycle's split is in question.
    return not (d.get("target") == "next" and d.get("cycle_id") == cycle.id)


def _proposal(db: Session, sub: Subscription, cycle: Cycle) -> tuple[str, dict[str, int], dict[str, int]]:
    """Which cycle the new split applies to, the proposed shares (string membership id -> cents) and the cover
    repayments inside them (E3)."""
    remaining = [m.id for m in active_members(db, sub.group_id)]
    paid = member_paid_in_cycle(db, cycle.id)
    departed = [int(k) for k in cycle.shares if int(k) not in remaining]
    unpaid_departed = any(paid.get(k, 0) < int(cycle.shares[str(k)]) for k in departed)
    if cycle.status not in ("FUNDED", "RENEWED") and unpaid_departed:
        # Re-split what is still missing this cycle. Departed members keep exactly what they paid.
        frozen = {str(k): paid.get(k, 0) for k in departed}
        base = equal_split(cycle.required_minor - sum(frozen.values()), remaining)
        split, _ = apply_cover_repayments(db, sub.id, cycle.renewal_date, base)
        return "current", {**frozen, **{str(k): v for k, v in split.items()}}, adjustments(base, split)
    return "next", {str(k): v for k, v in equal_split(sub.price_minor, remaining).items()}, {}


def _open_decision(db: Session, sub: Subscription, left_names: list[str]) -> dict:
    g = db.get(Group, sub.group_id)
    cycle = current_cycle(db, sub)
    target, new_shares, adj = _proposal(db, sub, cycle)
    voters = active_members(db, g.id)
    d = {
        "id": uuid.uuid4().hex[:10], "kind": "MEMBER_LEFT", "status": "OPEN", "target": target, "cycle_id": cycle.id,
        "left": left_names, "voters": [str(m.id) for m in voters], "new_shares": new_shares, "adjustments": adj,
        "old_shares": {str(m.id): int(cycle.shares.get(str(m.id), 0)) for m in voters}, "votes": {},
        "created_at": utcnow().isoformat(),
    }
    sub.decision = d
    db.flush()
    who = " and ".join(left_names)
    when = "for this cycle" if target == "current" else "from the next cycle"
    for m in voters:
        notify(db, user_id=m.user_id, group_id=g.id, kind="DECISION",
               title=f"{who} left {g.name}. Raise shares or pause {sub.merchant_name}?",
               body=(f"{sub.merchant_name} costs {fmt_usd(sub.price_minor)}. Without {who}, your share would be "
                     f"{fmt_usd(new_shares[str(m.id)])} {when} (now {fmt_usd(d['old_shares'][str(m.id)])}). "
                     "The new split applies only if everyone agrees. One \"Pause\" vote pauses the subscription."),
               action={"type": "DECIDE", "decision_id": d["id"]}, dedupe_key=f"decide-{d['id']}-{m.id}")
    return d


def leave_group(db: Session, m: Membership) -> dict:
    if m.status != "ACTIVE":
        raise ValueError("You are not an active member of this group")
    if m.role == "OWNER":
        raise ValueError("The owner cannot leave in this build (ownership transfer is not built).")
    from .balances import projected, settle_leaver, tasks_using

    busy = tasks_using(db, m)
    if busy:
        raise ValueError(f"Task #{busy[0].id} is using your part of the pot. You can leave once it has finished.")
    settlement = projected(db, m.group_id).get(m.id, 0)  # > 0 refund, < 0 debt
    sub = db.scalar(select(Subscription).where(Subscription.group_id == m.group_id))
    g = db.get(Group, m.group_id)
    name = member_name(db, m.id)
    m.status, m.left_at = "LEFT", utcnow()
    m.vault_token_enc, m.vault_label, m.force_fail_next = None, None, False  # stop any future auto-debit
    for a in db.scalars(select(ContributionAttempt).where(ContributionAttempt.membership_id == m.id,
                                                          ContributionAttempt.status == "APPROVAL_PENDING")):
        a.status, a.error = "CANCELLED", "Member left the group before approving"
    if sub and sub.split_mode == "CUSTOM":  # a custom split no longer sums once someone leaves
        sub.split_mode = "EQUAL"
        for mm in db.scalars(select(Membership).where(Membership.group_id == m.group_id)):
            mm.custom_share_minor = None
    db.flush()
    u = db.get(User, m.user_id)
    notify(db, user_id=u.id, group_id=g.id, kind="INFO", title=f"You left {g.name}",
           body="Nothing more will be charged. What you paid toward the current cycle stays in the pot for the renewal.",
           dedupe_key=f"left-{m.id}-{m.left_at.isoformat()}")
    settle_leaver(db, m, settlement)
    if sub is None or current_cycle(db, sub) is None:
        return {"settlement_minor": settlement}
    prev = sub.decision if sub.decision and sub.decision.get("status") in OPEN_STATES else None
    left = (prev["left"] if prev else []) + [name]
    return {**_open_decision(db, sub, left), "settlement_minor": settlement}


def vote(db: Session, sub: Subscription, m: Membership, choice: str) -> dict:
    d = dict(sub.decision or {})
    if d.get("status") != "OPEN":
        raise ValueError("There is no open decision to vote on")
    if str(m.id) not in d["voters"]:
        raise Forbidden("You are not one of the members voting on this decision")
    if choice not in ("AGREE", "PAUSE"):
        raise ValueError("vote must be AGREE or PAUSE")
    d["votes"] = {**d["votes"], str(m.id): choice}
    _clear_prompts(db, [m.user_id])
    g = db.get(Group, sub.group_id)
    cycle = current_cycle(db, sub)
    voter_name = member_name(db, m.id)
    active_ids = {str(x.id) for x in active_members(db, g.id)}
    if choice == "PAUSE":
        d["status"] = "PAUSED"
        sub.status = "PAUSED"
        if cycle and cycle.status != "RENEWED":
            cycle.status = "PAUSED"
        title, body = (f"{sub.merchant_name} paused by vote",
                       f"{voter_name} voted to pause rather than raise shares. Nothing will be collected or renewed. "
                       "The owner can ask the group again.")
    elif all(d["votes"].get(v) == "AGREE" for v in d["voters"] if v in active_ids):
        d["status"] = "AGREED"
        title, body = _apply(db, sub, cycle, d)
    else:
        sub.decision = d
        db.flush()
        return d
    sub.decision = d
    db.flush()
    for x in active_members(db, g.id):
        notify(db, user_id=x.user_id, group_id=g.id, kind="INFO", title=title, body=body,
               dedupe_key=f"decided-{d['id']}-{d['status']}-{x.id}")
    _clear_prompts(db, [x.user_id for x in active_members(db, g.id)])  # the question is settled
    return d


def _clear_prompts(db: Session, user_ids: list[int]) -> None:
    for n in db.scalars(select(Notification).where(Notification.user_id.in_(user_ids), Notification.kind == "DECISION",
                                                   Notification.read_at.is_(None))):
        n.read_at = utcnow()


def _apply(db: Session, sub: Subscription, cycle: Cycle, d: dict) -> tuple[str, str]:
    from .cycles import enqueue

    if cycle and cycle.status != "RENEWED":
        if d["target"] == "current" and cycle.id == d["cycle_id"]:
            cycle.shares = dict(d["new_shares"])
            cycle.share_adjustments = dict(d.get("adjustments") or {})
        if cycle.status == "PAUSED":
            cycle.status = "FUNDED" if cycle_collected(db, cycle.id) >= cycle.required_minor else "COLLECTING"
            sub.status = cycle.status
        db.flush()
        if cycle.status != "FUNDED":
            enqueue(db, "COLLECT", {"cycle_id": cycle.id, "reason": "decision agreed"})
    when = "this cycle" if d["target"] == "current" else "from the next cycle"
    return ("Everyone agreed to the new split",
            f"Shares are raised {when} because every remaining member agreed. Pay requests and auto-debits follow.")


def ask_again(db: Session, sub: Subscription) -> dict:
    """Owner re-opens a paused decision with a fresh proposal and fresh votes."""
    d = sub.decision or {}
    if d.get("status") != "PAUSED":
        raise ValueError("Only a paused decision can be asked again")
    return _open_decision(db, sub, d["left"])

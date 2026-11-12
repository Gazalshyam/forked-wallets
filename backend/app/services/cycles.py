"""Subscription cycle engine: COLLECT (D-5), CHECK (D-1), RENEW. Run by the worker from scheduled_jobs."""
import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    LABEL_SIMULATED,
    ContributionAttempt,
    Cycle,
    Group,
    Membership,
    ScheduledJob,
    Subscription,
    User,
    utcnow,
)
from .decisions import blocks_cycle
from .groups import active_members, current_cycle, member_name, open_cycle, spend_allocation
from .ledger import cycle_collected, pot_numbers, write_entry
from .money import fmt_usd
from .notify import notify

log = logging.getLogger("cycles")


def enqueue(db: Session, job_type: str, payload: dict, run_at: datetime | None = None,
            dedupe_key: str | None = None) -> ScheduledJob | None:
    if dedupe_key and db.scalar(select(ScheduledJob).where(ScheduledJob.dedupe_key == dedupe_key)):
        return None
    j = ScheduledJob(job_type=job_type, payload=payload, run_at=run_at or utcnow(), dedupe_key=dedupe_key)
    db.add(j)
    db.flush()
    return j


def _at(d: date) -> datetime:
    return datetime.combine(d, time(9, 0), tzinfo=timezone.utc)


def schedule_cycle_jobs(db: Session, cycle: Cycle) -> None:
    """Real-calendar jobs. Demo controls run the same handlers immediately."""
    enqueue(db, "COLLECT", {"cycle_id": cycle.id}, _at(cycle.renewal_date - timedelta(days=5)), f"COLLECT-cycle-{cycle.id}")
    enqueue(db, "CHECK", {"cycle_id": cycle.id}, _at(cycle.renewal_date - timedelta(days=1)), f"CHECK-cycle-{cycle.id}")
    enqueue(db, "RENEW", {"cycle_id": cycle.id}, _at(cycle.renewal_date), f"RENEW-cycle-{cycle.id}")


def _cycle_ctx(db: Session, payload: dict):
    cycle = db.get(Cycle, int(payload["cycle_id"]))
    if cycle is None:
        return None, None, None
    sub = db.get(Subscription, cycle.subscription_id)
    g = db.get(Group, cycle.group_id)
    if sub.status == "CANCELLED" or current_cycle(db, sub) is None or current_cycle(db, sub).id != cycle.id:
        log.info("Cycle %s is not the current cycle; skipping", cycle.id)
        return None, None, None
    return cycle, sub, g


def after_contribution(db: Session, a: ContributionAttempt) -> None:
    if not a.cycle_id:
        return
    from ..models import Notification
    from .payments import outstanding_share

    cycle = db.get(Cycle, a.cycle_id)
    whose = a.covered_membership_id or a.membership_id
    if outstanding_share(db, cycle, whose) <= 0:  # clear open pay requests for this share (and cover offers)
        uid = db.get(Membership, whose).user_id
        for n in db.scalars(select(Notification).where(Notification.read_at.is_(None), Notification.group_id == cycle.group_id,
                                                        Notification.kind.in_(("PAY_SHARE", "REMINDER", "COVER_REQUEST")))):
            act = n.action or {}
            if act.get("cycle_id") != cycle.id:
                continue
            if (n.user_id == uid and n.kind != "COVER_REQUEST") or (n.kind == "COVER_REQUEST" and act.get("membership_id") == whose):
                n.read_at = utcnow()
    if a.kind == "COVER":
        g = db.get(Group, cycle.group_id)
        payer, debtor = member_name(db, a.membership_id), member_name(db, whose)
        for m in active_members(db, g.id):
            notify(db, user_id=m.user_id, group_id=g.id, kind="INFO", title=f"{payer} covered {debtor}'s {fmt_usd(a.amount_minor)} share",
                   body=f"Next cycle {payer} skips {fmt_usd(a.amount_minor)} of their share and {debtor} pays it on top of their own. "
                        f"If {debtor} misses again next cycle, nobody can cover and the subscription pauses.",
                   dedupe_key=f"covered-{a.id}-{m.id}")
    if cycle.status in ("OPEN", "COLLECTING", "PAUSED") and cycle_collected(db, cycle.id) >= cycle.required_minor:
        cycle.status = "FUNDED"
        sub = db.get(Subscription, cycle.subscription_id)
        sub.status = "FUNDED"
        g = db.get(Group, cycle.group_id)
        db.flush()
        for m in active_members(db, g.id):
            notify(db, user_id=m.user_id, group_id=g.id, kind="INFO", title=f"{g.name}'s pot is full for {sub.merchant_name}",
                   body=f"{fmt_usd(cycle.required_minor)} collected for the {cycle.renewal_date.isoformat()} renewal.",
                   dedupe_key=f"funded-{cycle.id}-{m.user_id}")


def job_collect(db: Session, payload: dict) -> None:
    from .payments import auto_debit, outstanding_share, _fallback_to_approval

    cycle, sub, g = _cycle_ctx(db, payload)
    if cycle is None or cycle.status in ("FUNDED", "RENEWED"):
        return
    if blocks_cycle(sub, cycle):
        log.info("Cycle %s: members have not agreed the new split yet; COLLECT charges nobody", cycle.id)
        return
    cycle.status = "COLLECTING"
    if sub.status in ("ACTIVE", "PAUSED"):
        sub.status = "COLLECTING"
    db.flush()
    for m in active_members(db, g.id):
        if str(m.id) not in cycle.shares or outstanding_share(db, cycle, m.id) <= 0:
            continue
        if m.payment_mode == "AUTO_DEBIT":
            auto_debit(db, m, cycle)
        else:
            _fallback_to_approval(db, m, cycle, f"{sub.merchant_name} renews on {cycle.renewal_date.isoformat()}.")
    db.flush()


def job_check(db: Session, payload: dict) -> None:
    from ..agents.collections import write_reminder
    from .payments import outstanding_share

    cycle, sub, g = _cycle_ctx(db, payload)
    if cycle is None or cycle.status in ("FUNDED", "RENEWED"):
        return
    shortfall = cycle.required_minor - cycle_collected(db, cycle.id)
    if shortfall <= 0 or blocks_cycle(sub, cycle):
        return
    today = date.today().isoformat()
    for m in active_members(db, g.id):
        u = db.get(User, m.user_id)
        owed = outstanding_share(db, cycle, m.id) if str(m.id) in cycle.shares else 0
        key = f"reminder-{cycle.id}-{m.id}-{today}"  # one reminder per member per day
        from ..models import Notification

        if db.scalar(select(Notification).where(Notification.dedupe_key == key)):
            continue
        text, ai = write_reminder(u.name, g.name, sub.merchant_name, owed, shortfall, cycle.renewal_date.isoformat())
        action = {"type": "PAY_SHARE", "cycle_id": cycle.id, "ai": ai} if owed > 0 else {"type": "NONE", "ai": ai}
        notify(db, user_id=u.id, group_id=g.id, kind="REMINDER",
               title=f"{g.name} is {fmt_usd(shortfall)} short for {sub.merchant_name}", body=text,
               action=action, dedupe_key=key)
    offer_covers(db, cycle, sub, g)


def offer_covers(db: Session, cycle: Cycle, sub: Subscription, g: Group) -> None:
    """E2: tell the others who has not paid, and let one of them cover it. Not offered for a second miss in a row."""
    from .payments import outstanding_share, was_covered_in_previous_cycle

    members = active_members(db, g.id)
    for debtor in members:
        owed = outstanding_share(db, cycle, debtor.id) if str(debtor.id) in cycle.shares else 0
        if owed <= 0:
            continue
        dn = member_name(db, debtor.id)
        if was_covered_in_previous_cycle(db, cycle, debtor.id):
            for m in members:
                notify(db, user_id=m.user_id, group_id=g.id, kind="INFO",
                       title=f"{dn} has missed two cycles in a row",
                       body=f"{dn}'s share was covered last cycle and is unpaid again, so it cannot be covered twice. "
                            f"Unless {dn} pays {fmt_usd(owed)} before {cycle.renewal_date.isoformat()}, "
                            f"{sub.merchant_name} pauses at renewal.",
                       dedupe_key=f"nocover-{cycle.id}-{debtor.id}-{m.id}")
            continue
        for m in members:
            if m.id == debtor.id:
                continue
            notify(db, user_id=m.user_id, group_id=g.id, kind="COVER_REQUEST",
                   title=f"{dn} hasn't paid their {fmt_usd(owed)} share",
                   body=f"{sub.merchant_name} renews on {cycle.renewal_date.isoformat()}. If the share stays unpaid, "
                        f"the subscription pauses. You can pay it for {dn}: next cycle you skip {fmt_usd(owed)} of your "
                        f"share and {dn} pays it back on top of their own.",
                   action={"type": "COVER_SHARE", "cycle_id": cycle.id, "membership_id": debtor.id, "name": dn,
                           "amount_minor": owed},
                   dedupe_key=f"cover-{cycle.id}-{debtor.id}-{m.id}")


def job_renew(db: Session, payload: dict) -> None:
    cycle, sub, g = _cycle_ctx(db, payload)
    if cycle is None or cycle.status == "RENEWED":
        return
    if (sub.decision or {}).get("status") == "PAUSED":
        log.info("Cycle %s: the group voted to pause; not renewing", cycle.id)
        return
    collected = cycle_collected(db, cycle.id)
    pot = pot_numbers(db, g.id)
    if collected >= cycle.required_minor and pot["available_minor"] >= cycle.required_minor:
        write_entry(db, group_id=g.id, entry_type="SPEND", amount_minor=cycle.required_minor, source_label=LABEL_SIMULATED,
                    idempotency_key=f"renewal-{cycle.id}",
                    description=f"{sub.merchant_name} renewal for {cycle.renewal_date.isoformat()} (mock merchant charge, no PayPal debit)",
                    cycle_id=cycle.id, allocation=spend_allocation(cycle))
        cycle.status = "RENEWED"
        sub.next_renewal_date = cycle.renewal_date + timedelta(days=sub.cadence_days)
        sub.status = "ACTIVE"
        db.flush()
        nxt = open_cycle(db, sub)
        schedule_cycle_jobs(db, nxt)
        for mid, note in (nxt.share_notes or {}).items():
            mm = db.get(Membership, int(mid))
            notify(db, user_id=mm.user_id, group_id=g.id, kind="INFO",
                   title=f"Your share for the {nxt.renewal_date.isoformat()} cycle is {fmt_usd(int(nxt.shares[mid]))}",
                   body=note, dedupe_key=f"sharenote-{nxt.id}-{mid}")
        for m in active_members(db, g.id):
            notify(db, user_id=m.user_id, group_id=g.id, kind="INFO", title=f"{sub.merchant_name} renewed",
                   body=f"Renewal of {fmt_usd(cycle.required_minor)} recorded (SIMULATED merchant charge). "
                        f"Next renewal {sub.next_renewal_date.isoformat()}.", dedupe_key=f"renewed-{cycle.id}-{m.user_id}")
        log.info("Cycle %s renewed; next cycle %s", cycle.id, nxt.id)
    else:
        cycle.status = "PAUSED"
        sub.status = "PAUSED"
        short = cycle.required_minor - collected
        db.flush()
        for m in active_members(db, g.id):
            notify(db, user_id=m.user_id, group_id=g.id, kind="INFO", title=f"{sub.merchant_name} paused",
                   body=f"The pot was {fmt_usd(max(short, 0))} short at renewal, so the plan is paused. "
                        "Nobody else's share was raised. Pay the open share and the owner can renew.",
                   dedupe_key=f"paused-{cycle.id}-{m.user_id}-{utcnow().strftime('%H%M%S')}")
        log.info("Cycle %s paused (collected %s of %s)", cycle.id, collected, cycle.required_minor)


JOB_HANDLERS = {"COLLECT": job_collect, "CHECK": job_check, "RENEW": job_renew}

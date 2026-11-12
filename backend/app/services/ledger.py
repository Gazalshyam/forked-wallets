"""Append-only ledger and pot numbers derived from it.

collected = sum CONTRIBUTION
spent     = sum SPEND
reserved  = sum FORK - sum SPEND(with task_id) - sum MERGE_BACK   (open fork reservations)
refunded  = sum REFUND                                              (E3: paid back to members who left)
available = max(0, collected - spent - refunded - reserved)

Per member (E3), from each row's owner or `allocation`:
free[m]     = contributions[m] - spends[m] - refunds[m] - reserved[m] - write_offs[m]
reserved[m] = forks[m] - task spends[m] - merge backs[m]
Summed over every member, free equals collected - spent - refunded - reserved.
"""
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import LABEL_MOCK_PAYPAL, LABEL_PAYPAL, LABEL_SIMULATED, LedgerEntry

ENTRY_TYPES = {"CONTRIBUTION", "SPEND", "FORK", "MERGE_BACK", "REFUND", "WRITE_OFF"}
LABELS = {LABEL_PAYPAL, LABEL_MOCK_PAYPAL, LABEL_SIMULATED}


def write_entry(db: Session, *, group_id: int, entry_type: str, amount_minor: int, source_label: str,
                idempotency_key: str, description: str = "", **refs) -> LedgerEntry:
    """Insert one ledger row. Same idempotency key -> returns the existing row, never a second one."""
    if entry_type not in ENTRY_TYPES:
        raise ValueError(f"bad entry type {entry_type}")
    if source_label not in LABELS:
        raise ValueError(f"bad source label {source_label}")
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool) or amount_minor < 0:
        raise ValueError("ledger amounts are non-negative integer cents")
    alloc = refs.get("allocation")
    if alloc is not None:
        vals = list(alloc.values())
        if any(not isinstance(v, int) or isinstance(v, bool) for v in vals):
            raise ValueError("allocations are integer cents")
        if entry_type == "WRITE_OFF":
            if sum(vals) != 0:
                raise ValueError("a write-off moves money between members and must sum to zero")
        elif any(v < 0 for v in vals) or sum(vals) != amount_minor:
            raise ValueError("allocation must be non-negative and sum to the row amount")
    existing = db.scalar(select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    row = LedgerEntry(group_id=group_id, entry_type=entry_type, amount_minor=amount_minor,
                      source_label=source_label, idempotency_key=idempotency_key, description=description, **refs)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        # A concurrent writer won with the same key.
        existing = db.scalar(select(LedgerEntry).where(LedgerEntry.idempotency_key == idempotency_key))
        if existing is None:
            raise
        return existing
    return row


def pot_numbers(db: Session, group_id: int) -> dict:
    t = LedgerEntry.entry_type
    a = LedgerEntry.amount_minor
    row = db.execute(
        select(
            func.coalesce(func.sum(case((t == "CONTRIBUTION", a), else_=0)), 0),
            func.coalesce(func.sum(case((t == "SPEND", a), else_=0)), 0),
            func.coalesce(func.sum(case((t == "FORK", a), else_=0)), 0),
            func.coalesce(func.sum(case(((t == "SPEND") & LedgerEntry.task_id.isnot(None), a), else_=0)), 0),
            func.coalesce(func.sum(case((t == "MERGE_BACK", a), else_=0)), 0),
            func.coalesce(func.sum(case((t == "REFUND", a), else_=0)), 0),
        ).where(LedgerEntry.group_id == group_id)
    ).one()
    collected, spent, forked, task_spent, merged, refunded = (int(x) for x in row)
    reserved = max(0, forked - task_spent - merged)
    available = max(0, collected - spent - refunded - reserved)
    return {"collected_minor": collected, "spent_minor": spent, "reserved_minor": reserved,
            "available_minor": available, "merged_back_minor": merged, "refunded_minor": refunded}


def member_balances(db: Session, group_id: int) -> dict[int, dict[str, int]]:
    """E3: each member's part of the pot. {membership_id: {"free_minor", "reserved_minor"}}.
    free can be negative (someone covered your share and the spend has happened); it nets out next cycle."""
    from ..models import Cycle

    bal: dict[int, dict[str, int]] = {}

    def b(mid) -> dict[str, int]:
        return bal.setdefault(int(mid), {"free_minor": 0, "reserved_minor": 0})

    for e in db.scalars(select(LedgerEntry).where(LedgerEntry.group_id == group_id).order_by(LedgerEntry.id)):
        alloc = e.allocation
        if alloc is None and e.entry_type == "SPEND" and e.cycle_id:
            from .groups import spend_allocation

            alloc = spend_allocation(db.get(Cycle, e.cycle_id))  # renewal rows written before allocations existed
        if e.entry_type == "CONTRIBUTION" and e.membership_id:
            b(e.membership_id)["free_minor"] += e.amount_minor
        elif e.entry_type == "REFUND" and e.membership_id:
            b(e.membership_id)["free_minor"] -= e.amount_minor
        elif alloc is None:
            continue
        elif e.entry_type == "SPEND":
            for k, v in alloc.items():
                if e.task_id:
                    b(k)["reserved_minor"] -= int(v)
                else:
                    b(k)["free_minor"] -= int(v)
        elif e.entry_type == "FORK":
            for k, v in alloc.items():
                b(k)["free_minor"] -= int(v)
                b(k)["reserved_minor"] += int(v)
        elif e.entry_type == "MERGE_BACK":
            for k, v in alloc.items():
                b(k)["free_minor"] += int(v)
                b(k)["reserved_minor"] -= int(v)
        elif e.entry_type == "WRITE_OFF":
            for k, v in alloc.items():
                b(k)["free_minor"] -= int(v)
    return bal


def cycle_collected(db: Session, cycle_id: int) -> int:
    v = db.scalar(select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
        LedgerEntry.cycle_id == cycle_id, LedgerEntry.entry_type == "CONTRIBUTION"))
    return int(v or 0)


def member_paid_in_cycle(db: Session, cycle_id: int) -> dict[int, int]:
    """Cents credited to each member's share in a cycle. A COVER row counts for the covered member, not the payer."""
    whose = func.coalesce(LedgerEntry.on_behalf_of_membership_id, LedgerEntry.membership_id)
    rows = db.execute(select(whose, func.sum(LedgerEntry.amount_minor)).where(
        LedgerEntry.cycle_id == cycle_id, LedgerEntry.entry_type == "CONTRIBUTION").group_by(whose)).all()
    return {int(m): int(s) for m, s in rows if m is not None}

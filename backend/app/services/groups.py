"""Groups, memberships, invitations, subscription split and cycles."""
from datetime import date, timedelta

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..models import (
    ContributionAttempt,
    Cycle,
    Group,
    Invitation,
    Membership,
    Subscription,
    User,
)
from .ledger import cycle_collected
from .money import equal_split, fmt_usd, validate_custom_split
from .notify import notify


class Forbidden(Exception):
    pass


def active_members(db: Session, group_id: int) -> list[Membership]:
    return list(db.scalars(select(Membership).where(Membership.group_id == group_id, Membership.status == "ACTIVE")
                          .order_by(Membership.id)))


def get_membership(db: Session, group_id: int, user_id: int) -> Membership | None:
    return db.scalar(select(Membership).where(Membership.group_id == group_id, Membership.user_id == user_id,
                                              Membership.status == "ACTIVE"))


def require_member(db: Session, group_id: int, user_id: int) -> Membership:
    m = get_membership(db, group_id, user_id)
    if m is None:
        raise Forbidden("You are not a member of this group")
    return m


def require_owner(db: Session, group_id: int, user_id: int) -> Membership:
    m = require_member(db, group_id, user_id)
    if m.role != "OWNER":
        raise Forbidden("Only the group owner can do this")
    return m


def compute_shares(db: Session, sub: Subscription) -> dict[int, int]:
    members = active_members(db, sub.group_id)
    ids = [m.id for m in members]
    if sub.split_mode == "CUSTOM" and all(m.custom_share_minor is not None for m in members):
        return validate_custom_split(sub.price_minor, {m.id: int(m.custom_share_minor) for m in members}, ids)
    return equal_split(sub.price_minor, ids)


def current_cycle(db: Session, sub: Subscription) -> Cycle | None:
    return db.scalar(select(Cycle).where(Cycle.subscription_id == sub.id, Cycle.renewal_date == sub.next_renewal_date))


def previous_cycle(db: Session, subscription_id: int, renewal_date: date) -> Cycle | None:
    return db.scalar(select(Cycle).where(Cycle.subscription_id == subscription_id, Cycle.renewal_date < renewal_date)
                     .order_by(desc(Cycle.renewal_date)).limit(1))


def completed_covers(db: Session, cycle_id: int) -> list[ContributionAttempt]:
    return list(db.scalars(select(ContributionAttempt).where(
        ContributionAttempt.cycle_id == cycle_id, ContributionAttempt.kind == "COVER",
        ContributionAttempt.status == "COMPLETED").order_by(ContributionAttempt.id)))


def member_name(db: Session, membership_id: int) -> str:
    m = db.get(Membership, membership_id)
    return db.get(User, m.user_id).name if m else "?"


def apply_cover_repayments(db: Session, subscription_id: int, renewal_date: date,
                           shares: dict[int, int]) -> tuple[dict[int, int], dict[str, str]]:
    """E2: a cover paid in the previous cycle is settled in this one, for one cycle only.
    The coverer's share drops by the covered amount (never below 0) and the covered member's rises by the same
    amount, so the total still equals the price. Returns (shares, notes per membership id)."""
    shares = dict(shares)
    notes: dict[str, str] = {}
    prev = previous_cycle(db, subscription_id, renewal_date)
    if prev is None:
        return shares, notes
    for a in completed_covers(db, prev.id):
        payer, debtor = a.membership_id, a.covered_membership_id
        if payer not in shares or debtor not in shares:
            continue  # someone left; nothing to settle inside the subscription
        applied = min(a.amount_minor, shares[payer])
        if applied <= 0:
            continue
        shares[payer] -= applied
        shares[debtor] += applied
        pn, dn = member_name(db, payer), member_name(db, debtor)
        notes[str(payer)] = (notes.get(str(payer), "") + f" {fmt_usd(applied)} less: covered {dn}'s share last cycle.").strip()
        notes[str(debtor)] = (notes.get(str(debtor), "") + f" {fmt_usd(applied)} more: repays {pn}, who covered your share last cycle.").strip()
    return shares, notes


def adjustments(base: dict[int, int], adjusted: dict[int, int]) -> dict[str, int]:
    return {str(k): adjusted[k] - base.get(k, 0) for k in adjusted if adjusted[k] != base.get(k, 0)}


def spend_allocation(cycle: Cycle) -> dict[str, int]:
    """E3: what the renewal costs each member: the share minus any cover repayment inside it."""
    adj = cycle.share_adjustments or {}
    return {k: int(v) - int(adj.get(k, 0)) for k, v in cycle.shares.items()}


def snapshot_shares(db: Session, sub: Subscription, renewal_date: date) -> tuple[dict[str, int], dict[str, str], dict[str, int]]:
    base = compute_shares(db, sub)
    shares, notes = apply_cover_repayments(db, sub.id, renewal_date, base)
    return {str(k): v for k, v in shares.items()}, notes, adjustments(base, shares)


def open_cycle(db: Session, sub: Subscription) -> Cycle:
    """Create (or return) the cycle for the subscription's next renewal date with an immutable share snapshot."""
    c = current_cycle(db, sub)
    if c is not None:
        return c
    shares, notes, adj = snapshot_shares(db, sub, sub.next_renewal_date)
    c = Cycle(subscription_id=sub.id, group_id=sub.group_id, renewal_date=sub.next_renewal_date,
              required_minor=sub.price_minor, shares=shares, share_notes=notes, share_adjustments=adj, status="OPEN")
    db.add(c)
    db.flush()
    return c


def refresh_cycle_if_untouched(db: Session, sub: Subscription) -> bool:
    """Shares are a snapshot. We only re-snapshot the current cycle if nothing has been attempted in it yet;
    otherwise the change applies from the next cycle."""
    c = current_cycle(db, sub)
    if c is None:
        open_cycle(db, sub)
        return True
    touched = db.scalar(select(ContributionAttempt.id).where(ContributionAttempt.cycle_id == c.id).limit(1))
    if touched or cycle_collected(db, c.id) > 0 or c.status != "OPEN":
        return False
    c.shares, c.share_notes, c.share_adjustments = snapshot_shares(db, sub, c.renewal_date)
    c.required_minor = sub.price_minor
    db.flush()
    return True


def create_group(db: Session, owner: User, name: str, merchant_name: str, price_minor: int,
                 renewal_date: date | None = None, cadence_days: int = 30) -> Group:
    if price_minor <= 0:
        raise ValueError("price must be positive")
    g = Group(name=name, owner_id=owner.id, currency="USD")
    db.add(g)
    db.flush()
    db.add(Membership(group_id=g.id, user_id=owner.id, role="OWNER"))
    sub = Subscription(group_id=g.id, merchant_name=merchant_name, price_minor=price_minor, cadence_days=cadence_days,
                       next_renewal_date=renewal_date or (date.today() + timedelta(days=7)))
    db.add(sub)
    db.flush()
    open_cycle(db, sub)
    return g


def invite(db: Session, group: Group, inviter: User, email: str) -> Invitation:
    email = email.strip().lower()
    if "@" not in email:
        raise ValueError("enter an email address")
    existing_user = db.scalar(select(User).where(User.email == email))
    if existing_user and get_membership(db, group.id, existing_user.id):
        raise ValueError("already a member")
    inv = db.scalar(select(Invitation).where(Invitation.group_id == group.id, Invitation.email == email,
                                             Invitation.status == "PENDING"))
    if inv:
        return inv
    inv = Invitation(group_id=group.id, email=email, invited_by=inviter.id)
    db.add(inv)
    db.flush()
    if existing_user:
        notify(db, user_id=existing_user.id, group_id=group.id, kind="INVITE",
               title=f"{inviter.name} invited you to {group.name}",
               body="Accept to share the subscription and the group pot.",
               action={"type": "ACCEPT_INVITE", "invitation_id": inv.id}, dedupe_key=f"invite:{inv.id}")
    return inv


def accept_invitation(db: Session, inv: Invitation, user: User) -> Membership:
    if inv.email != user.email:
        raise Forbidden("This invitation is for a different email")
    if inv.status != "PENDING":
        raise ValueError("Invitation is no longer pending")
    m = db.scalar(select(Membership).where(Membership.group_id == inv.group_id, Membership.user_id == user.id))
    if m is None:
        m = Membership(group_id=inv.group_id, user_id=user.id, role="MEMBER")
        db.add(m)
    else:
        m.status = "ACTIVE"
    inv.status = "ACCEPTED"
    db.flush()
    sub = db.scalar(select(Subscription).where(Subscription.group_id == inv.group_id))
    if sub:
        if sub.split_mode == "CUSTOM":
            sub.split_mode = "EQUAL"  # new member invalidates the custom split; owner can set a new one
        refresh_cycle_if_untouched(db, sub)
    return m


def set_custom_split(db: Session, sub: Subscription, shares: dict[int, int] | None) -> None:
    members = active_members(db, sub.group_id)
    if shares is None:
        sub.split_mode = "EQUAL"
        for m in members:
            m.custom_share_minor = None
    else:
        validate_custom_split(sub.price_minor, shares, [m.id for m in members])
        for m in members:
            m.custom_share_minor = shares[m.id]
        sub.split_mode = "CUSTOM"
    db.flush()
    refresh_cycle_if_untouched(db, sub)


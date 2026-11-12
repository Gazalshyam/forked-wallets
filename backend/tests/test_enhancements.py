"""E1 (leave + vote) and E2 (cover a missed share). See ENHANCEMENTS.md."""
import pytest
from sqlalchemy import func, select

from app.models import ContributionAttempt, LedgerEntry, Notification, ScheduledJob, User
from app.services import cycles, decisions
from app.services import groups as gs
from app.services import payments as ps
from app.services.groups import Forbidden

from .helpers import cycle, member, pay_share


def cover(db, payer, debtor):
    _, c = cycle(db)
    a = ps.start_approval_payment(db, membership=member(db, payer), cycle=c, kind="COVER",
                                  covered_membership_id=member(db, debtor).id)
    ps.complete_approval(db, a.paypal_order_id)
    db.commit()
    return a


def inbox(db, name, kind):
    u = db.scalar(select(User).where(User.name == name))
    return list(db.scalars(select(Notification).where(Notification.user_id == u.id, Notification.kind == kind)))


def shares_by_name(db, c):
    return {gs.member_name(db, int(k)): v for k, v in c.shares.items()}


# ---------- E2: cover a missed share ----------

def test_cover_settles_debtor_share_and_is_repaid_next_cycle(db):
    sub, c = cycle(db)
    pay_share(db, "Asha")
    pay_share(db, "Chloe")
    cycles.job_check(db, {"cycle_id": c.id})
    db.commit()
    offers = inbox(db, "Chloe", "COVER_REQUEST")
    assert len(offers) == 1 and offers[0].action["membership_id"] == member(db, "Ben").id
    assert inbox(db, "Ben", "COVER_REQUEST") == []  # nobody is asked to cover themselves

    a = cover(db, "Chloe", "Ben")
    assert a.status == "COMPLETED" and a.amount_minor == 500  # amount from the snapshot, not the client
    row = db.scalar(select(LedgerEntry).where(LedgerEntry.attempt_id == a.id))
    assert row.membership_id == member(db, "Chloe").id and row.on_behalf_of_membership_id == member(db, "Ben").id
    db.refresh(c)
    assert c.status == "FUNDED"
    assert ps.outstanding_share(db, c, member(db, "Ben").id) == 0
    assert all(n.read_at for n in inbox(db, "Chloe", "COVER_REQUEST"))  # the offer is cleared

    cycles.job_renew(db, {"cycle_id": c.id})
    db.commit()
    _, c2 = cycle(db)
    assert c2.id != c.id
    assert shares_by_name(db, c2) == {"Asha": 500, "Ben": 1000, "Chloe": 0}
    assert sum(c2.shares.values()) == c2.required_minor
    assert "covered Ben" in c2.share_notes[str(member(db, "Chloe").id)]

    # One cycle only: the cycle after that is back to equal shares.
    for n in ("Asha", "Ben"):
        pay_share(db, n)
    cycles.job_renew(db, {"cycle_id": c2.id})
    db.commit()
    _, c3 = cycle(db)
    assert shares_by_name(db, c3) == {"Asha": 500, "Ben": 500, "Chloe": 500}


def test_second_miss_in_a_row_cannot_be_covered_and_pauses(db):
    sub, c = cycle(db)
    pay_share(db, "Asha")
    pay_share(db, "Chloe")
    cover(db, "Chloe", "Ben")
    cycles.job_renew(db, {"cycle_id": c.id})
    db.commit()
    _, c2 = cycle(db)
    pay_share(db, "Asha")  # Chloe owes 0; Ben owes 1000 and does not pay
    cycles.job_check(db, {"cycle_id": c2.id})
    db.commit()
    assert [n for n in inbox(db, "Asha", "COVER_REQUEST") if n.action["cycle_id"] == c2.id] == []
    assert any("two cycles in a row" in n.title for n in inbox(db, "Asha", "INFO"))
    with pytest.raises(ValueError, match="covered last cycle"):
        ps.start_approval_payment(db, membership=member(db, "Asha"), cycle=c2, kind="COVER",
                                  covered_membership_id=member(db, "Ben").id)
    cycles.job_renew(db, {"cycle_id": c2.id})
    db.commit()
    db.refresh(c2)
    db.refresh(sub)
    assert c2.status == "PAUSED" and sub.status == "PAUSED"


def test_cover_is_not_captured_if_the_share_was_paid_meanwhile(db):
    _, c = cycle(db)
    a = ps.start_approval_payment(db, membership=member(db, "Chloe"), cycle=c, kind="COVER",
                                  covered_membership_id=member(db, "Ben").id)
    pay_share(db, "Ben")
    ps.complete_approval(db, a.paypal_order_id)
    db.commit()
    assert a.status == "CANCELLED" and a.paypal_capture_id is None
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.attempt_id == a.id)) == 0


def test_cannot_cover_yourself(db):
    _, c = cycle(db)
    with pytest.raises(ValueError):
        ps.start_approval_payment(db, membership=member(db, "Ben"), cycle=c, kind="COVER",
                                  covered_membership_id=member(db, "Ben").id)


# ---------- E1: leave + vote ----------

def test_leave_unpaid_resplits_current_cycle_only_after_everyone_agrees(db):
    sub, c = cycle(db)
    pay_share(db, "Asha")
    d = decisions.leave_group(db, member(db, "Ben"))
    db.commit()
    assert d["target"] == "current"
    assert shares_by_name(db, c)["Ben"] == 500  # not changed yet: no consent
    assert len(inbox(db, "Chloe", "DECISION")) == 1

    cycles.job_collect(db, {"cycle_id": c.id})  # blocked while the decision is open
    db.commit()
    assert db.scalar(select(func.count()).select_from(ContributionAttempt).where(
        ContributionAttempt.membership_id == member(db, "Chloe").id)) == 0

    decisions.vote(db, sub, member(db, "Asha"), "AGREE")
    db.commit()
    assert sub.decision["status"] == "OPEN"
    decisions.vote(db, sub, member(db, "Chloe"), "AGREE")
    db.commit()
    db.refresh(c)
    assert sub.decision["status"] == "AGREED"
    assert shares_by_name(db, c) == {"Asha": 750, "Ben": 0, "Chloe": 750}
    assert ps.outstanding_share(db, c, member(db, "Asha").id) == 250
    assert db.scalar(select(ScheduledJob).where(ScheduledJob.job_type == "COLLECT", ScheduledJob.status == "PENDING"))


def test_one_pause_vote_pauses_and_owner_can_ask_again(db):
    sub, c = cycle(db)
    decisions.leave_group(db, member(db, "Ben"))
    decisions.vote(db, sub, member(db, "Asha"), "AGREE")
    decisions.vote(db, sub, member(db, "Chloe"), "PAUSE")
    db.commit()
    db.refresh(c)
    assert sub.decision["status"] == "PAUSED" and sub.status == "PAUSED" and c.status == "PAUSED"
    pay_share(db, "Asha")
    pay_share(db, "Chloe")
    cycles.job_renew(db, {"cycle_id": c.id})
    db.commit()
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.entry_type == "SPEND")) == 0
    decisions.ask_again(db, sub)
    db.commit()
    assert sub.decision["status"] == "OPEN" and sub.decision["votes"] == {}


def test_leaver_who_already_paid_changes_the_next_cycle_only(db):
    sub, c = cycle(db)
    for n in ("Asha", "Ben", "Chloe"):
        pay_share(db, n)
    d = decisions.leave_group(db, member(db, "Ben"))
    db.commit()
    assert d["target"] == "next"
    cycles.job_renew(db, {"cycle_id": c.id})  # the current cycle is fully paid, so it renews
    db.commit()
    _, c2 = cycle(db)
    assert shares_by_name(db, c2) == {"Asha": 750, "Chloe": 750}
    cycles.job_collect(db, {"cycle_id": c2.id})  # raised shares are not collected before consent
    db.commit()
    assert db.scalar(select(func.count()).select_from(ContributionAttempt).where(ContributionAttempt.cycle_id == c2.id)) == 0
    for n in ("Asha", "Chloe"):
        decisions.vote(db, sub, member(db, n), "AGREE")
    db.commit()
    assert sub.decision["status"] == "AGREED"


def test_owner_cannot_leave_and_leavers_cannot_vote(db):
    sub, _ = cycle(db)
    with pytest.raises(ValueError):
        decisions.leave_group(db, member(db, "Asha"))
    decisions.leave_group(db, member(db, "Ben"))
    with pytest.raises(Forbidden):
        decisions.vote(db, sub, member(db, "Ben"), "AGREE")

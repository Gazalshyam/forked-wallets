"""E3 (per-member pot, refund on leave, debt write-off) and E4 (task participants, sub-pot, consent)."""
import pytest
from sqlalchemy import func, select

from app.models import LedgerEntry, Notification, ScheduledJob, Task, User
from app.services import balances, cycles, decisions, race
from app.services.ledger import member_balances, pot_numbers
from app.services.money import capped_split, proportional_split

from .helpers import cycle, group, member, pay_share, top_up

PROMPT = "Book a hotel in Austin for 2 nights under $200"


def free(db, name):
    return member_balances(db, group(db).id).get(member(db, name).id, {"free_minor": 0})["free_minor"]


def rows(db, entry_type):
    return list(db.scalars(select(LedgerEntry).where(LedgerEntry.entry_type == entry_type).order_by(LedgerEntry.id)))


def asha(db):
    return db.scalar(select(User).where(User.name == "Asha"))


def run_refund_jobs(db):
    for j in db.scalars(select(ScheduledJob).where(ScheduledJob.job_type == "REFUND_LEAVER", ScheduledJob.status == "PENDING")):
        balances.job_refund_leaver(db, j.payload)
        j.status = "DONE"
    db.commit()


def assert_balances_add_up(db):
    g = group(db).id
    bal = member_balances(db, g)
    p = pot_numbers(db, g)
    assert sum(b["free_minor"] for b in bal.values()) == p["available_minor"]
    assert sum(b["reserved_minor"] for b in bal.values()) == p["reserved_minor"]


# ---------- money helpers ----------

def test_capped_split_moves_what_a_member_cannot_cover_to_the_others():
    assert capped_split(100000, {1: 30000, 2: 100000}) == {1: 30000, 2: 70000}
    assert capped_split(1000, {3: 9999, 1: 9999, 2: 9999}) == {1: 334, 2: 333, 3: 333}
    assert capped_split(1001, {1: 500, 2: 1000}) == {1: 500, 2: 501}  # the remainder cent cannot break a cap
    assert capped_split(500, {1: 0, 2: 800}) == {1: 0, 2: 500}
    with pytest.raises(ValueError):
        capped_split(1000, {1: 400, 2: 500})


def test_proportional_split_is_exact():
    out = proportional_split(1000, {1: 1, 2: 1, 3: 1})
    assert sum(out.values()) == 1000 and max(out.values()) - min(out.values()) <= 1
    assert proportional_split(500, {1: 0, 2: 300}) == {1: 0, 2: 500}


# ---------- E3: per-member pot ----------

def test_member_parts_track_contributions_and_spends(db):
    top_up(db, "Asha", 3000)
    for n in ("Asha", "Ben", "Chloe"):
        pay_share(db, n)
    _, c = cycle(db)
    assert (free(db, "Asha"), free(db, "Ben"), free(db, "Chloe")) == (3500, 500, 500)
    cycles.job_renew(db, {"cycle_id": c.id})
    db.commit()
    assert (free(db, "Asha"), free(db, "Ben"), free(db, "Chloe")) == (3000, 0, 0)
    assert_balances_add_up(db)


def test_a_cover_makes_the_debtor_negative_until_repaid(db):
    _, c = cycle(db)
    pay_share(db, "Asha")
    pay_share(db, "Chloe")
    from .test_enhancements import cover

    cover(db, "Chloe", "Ben")
    cycles.job_renew(db, {"cycle_id": c.id})
    db.commit()
    assert (free(db, "Asha"), free(db, "Ben"), free(db, "Chloe")) == (0, -500, 500)
    assert_balances_add_up(db)
    _, c2 = cycle(db)  # Ben 1000, Chloe 0 this cycle
    pay_share(db, "Asha")
    pay_share(db, "Ben")
    cycles.job_renew(db, {"cycle_id": c2.id})
    db.commit()
    assert (free(db, "Asha"), free(db, "Ben"), free(db, "Chloe")) == (0, 0, 0)


def test_leaver_gets_their_surplus_back_but_not_the_current_cycle_share(db):
    for n in ("Asha", "Ben", "Chloe"):
        top_up(db, n, 7000)
        pay_share(db, n)
    collected = pot_numbers(db, group(db).id)["available_minor"]
    d = decisions.leave_group(db, member(db, "Ben"))
    db.commit()
    assert d["settlement_minor"] == 7000  # 7500 in the pot, 500 committed to this cycle's renewal
    run_refund_jobs(db)
    refunds = rows(db, "REFUND")
    assert sum(r.amount_minor for r in refunds) == 7000
    assert all(r.source_label == "MOCK PAYPAL" and r.paypal_refund_id.startswith("MOCK-REFUND-") for r in refunds)
    assert all(r.membership_id == member(db, "Ben").id for r in refunds)
    p = pot_numbers(db, group(db).id)
    assert p["refunded_minor"] == 7000 and p["available_minor"] == collected - 7000
    assert free(db, "Ben") == 500
    _, c = cycle(db)
    cycles.job_renew(db, {"cycle_id": c.id})  # Ben had paid, so the current cycle renews unchanged
    db.commit()
    assert free(db, "Ben") == 0
    assert_balances_add_up(db)
    ben = db.scalar(select(User).where(User.name == "Ben"))
    assert db.scalar(select(Notification).where(Notification.user_id == ben.id, Notification.title.like("Refund from%")))


def test_refund_never_exceeds_a_capture(db):
    for n in ("Asha", "Ben", "Chloe"):
        pay_share(db, n)
    top_up(db, "Ben", 1200)
    top_up(db, "Ben", 800)
    decisions.leave_group(db, member(db, "Ben"))
    db.commit()
    run_refund_jobs(db)
    refunds = rows(db, "REFUND")
    assert [r.amount_minor for r in refunds] == [800, 1200]  # newest capture first, each within its own amount
    assert len({r.paypal_capture_id for r in refunds}) == 2


def test_leaver_with_a_debt_is_notified_and_the_debt_is_written_off(db):
    _, c = cycle(db)
    pay_share(db, "Asha")
    pay_share(db, "Chloe")
    from .test_enhancements import cover

    cover(db, "Chloe", "Ben")
    d = decisions.leave_group(db, member(db, "Ben"))
    db.commit()
    assert d["settlement_minor"] == -500
    assert db.scalar(select(func.count()).select_from(ScheduledJob).where(ScheduledJob.job_type == "REFUND_LEAVER")) == 0
    wo = rows(db, "WRITE_OFF")
    assert len(wo) == 1 and wo[0].allocation == {str(member(db, "Ben").id): -500, str(member(db, "Chloe").id): 500}
    for n in ("Asha", "Chloe"):
        u = db.scalar(select(User).where(User.name == n))
        assert db.scalar(select(Notification).where(Notification.user_id == u.id, Notification.title == "Ben left owing $5.00"))
    cycles.job_renew(db, {"cycle_id": c.id})
    db.commit()
    assert (free(db, "Asha"), free(db, "Ben"), free(db, "Chloe")) == (0, 0, 0)
    assert_balances_add_up(db)


# ---------- E4: participants, sub-pot, consent ----------

def test_sub_pot_only_counts_the_participants(db):
    for n in ("Asha", "Ben", "Chloe"):
        top_up(db, n, 10000)
    g, a = group(db), asha(db)
    with pytest.raises(ValueError, match="participants have"):
        race.create_task(db, g, a, PROMPT, 25000, "CONFIRMED", [member(db, "Asha").id, member(db, "Ben").id])
    t = race.create_task(db, g, a, PROMPT, 20000, "CONFIRMED", [member(db, "Asha").id, member(db, "Ben").id])
    db.commit()
    assert t.status == "AWAITING_CONSENT" and t.consent == {str(member(db, "Asha").id): "AGREE"}
    assert rows(db, "FORK") == []  # nothing is reserved before consent
    race.consent(db, t, member(db, "Ben"), "AGREE")
    db.commit()
    assert t.status == "SEARCHING"
    assert t.allocation == {str(member(db, "Asha").id): 10000, str(member(db, "Ben").id): 10000}
    race.job_run_task(db, {"task_id": t.id})
    db.expire_all()
    t = db.get(Task, t.id)
    assert t.status == "COMPLETED"
    assert free(db, "Chloe") == 10000  # untouched
    half = t.final_price_minor // 2
    assert {free(db, "Asha"), free(db, "Ben")} == {10000 - half, 10000 - (t.final_price_minor - half)}
    assert_balances_add_up(db)


def test_a_member_with_less_pays_only_what_they_have(db):
    top_up(db, "Asha", 5000)
    top_up(db, "Ben", 15000)
    t = race.create_task(db, group(db), asha(db), PROMPT, 20000, "CONFIRMED", [member(db, "Asha").id, member(db, "Ben").id])
    race.consent(db, t, member(db, "Ben"), "AGREE")
    db.commit()
    assert t.allocation == {str(member(db, "Asha").id): 5000, str(member(db, "Ben").id): 15000}
    race.job_run_task(db, {"task_id": t.id})
    db.expire_all()
    t = db.get(Task, t.id)
    spend = db.scalar(select(LedgerEntry).where(LedgerEntry.task_id == t.id, LedgerEntry.entry_type == "SPEND"))
    a_part = spend.allocation[str(member(db, "Asha").id)]
    assert a_part == min(5000, (t.final_price_minor + 1) // 2) and sum(spend.allocation.values()) == t.final_price_minor
    assert free(db, "Asha") == 5000 - a_part and free(db, "Asha") >= 0
    assert_balances_add_up(db)


def test_decline_drops_the_member_or_cancels_if_short(db):
    for n in ("Asha", "Ben", "Chloe"):
        top_up(db, n, 10000)
    g, a = group(db), asha(db)
    t = race.create_task(db, g, a, PROMPT, 15000, "CONFIRMED")
    race.consent(db, t, member(db, "Chloe"), "DECLINE")
    db.commit()
    assert t.status == "AWAITING_CONSENT" and member(db, "Chloe").id not in t.participants
    race.consent(db, t, member(db, "Ben"), "AGREE")
    db.commit()
    assert t.status == "SEARCHING" and str(member(db, "Chloe").id) not in t.allocation
    assert free(db, "Asha") == 2500  # 7500 of her 10000 is reserved for the first task
    t2 = race.create_task(db, g, a, PROMPT, 12000, "CONFIRMED", [member(db, "Asha").id, member(db, "Chloe").id])
    race.consent(db, t2, member(db, "Chloe"), "DECLINE")  # Asha alone has 2500, less than 12000
    db.commit()
    assert t2.status == "CANCELLED"
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.task_id == t2.id)) == 0


def test_silence_past_the_deadline_uses_the_owners_authorization(db):
    for n in ("Asha", "Ben", "Chloe"):
        top_up(db, n, 7000)
    t = race.create_task(db, group(db), asha(db), PROMPT, 20000, "CONFIRMED")
    race.consent(db, t, member(db, "Ben"), "AGREE")
    db.commit()
    job = db.scalar(select(ScheduledJob).where(ScheduledJob.job_type == "CONSENT_TIMEOUT"))
    assert job is not None and job.payload == {"task_id": t.id}
    race.job_consent_timeout(db, job.payload)
    db.commit()
    assert t.status == "SEARCHING" and t.consent[str(member(db, "Chloe").id)] == "OWNER_AUTHORIZED"
    chloe = db.scalar(select(User).where(User.name == "Chloe"))
    assert db.scalar(select(Notification).where(Notification.user_id == chloe.id,
                                                Notification.title.like("%went ahead on Asha's authorization")))
    race.job_consent_timeout(db, job.payload)  # a late second run changes nothing
    assert len(rows(db, "FORK")) == 1


def test_cannot_leave_while_a_task_uses_your_money(db):
    for n in ("Asha", "Ben", "Chloe"):
        top_up(db, n, 7000)
    race.create_task(db, group(db), asha(db), PROMPT, 20000, "CONFIRMED")
    db.commit()
    with pytest.raises(ValueError, match="Task #"):
        decisions.leave_group(db, member(db, "Ben"))

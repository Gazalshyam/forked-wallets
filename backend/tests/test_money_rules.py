import threading

import pytest
from sqlalchemy import func, select

from app import db as dbmod
from app.agents import tools
from app.models import AgentRun, Candidate, LedgerEntry, Subscription, Task, User
from app.providers.paypal import CaptureResult, MockPayPalProvider
from app.services import cycles, race
from app.services import payments as ps
from app.services.ledger import pot_numbers, write_entry
from app.services.money import equal_split, validate_custom_split

from .helpers import cycle, group, member, pay_share, top_up


def test_equal_split_remainder_goes_to_first_members_by_id():
    assert equal_split(1000, [3, 1, 2]) == {1: 334, 2: 333, 3: 333}
    assert equal_split(1001, [5, 9, 7]) == {5: 334, 7: 334, 9: 333}
    assert equal_split(1500, [1, 2, 3]) == {1: 500, 2: 500, 3: 500}
    assert sum(equal_split(997, [1, 2, 3, 4]).values()) == 997


def test_custom_split_must_sum_exactly():
    assert validate_custom_split(1500, {1: 700, 2: 500, 3: 300}, [1, 2, 3]) == {1: 700, 2: 500, 3: 300}
    with pytest.raises(ValueError):
        validate_custom_split(1500, {1: 600, 2: 500, 3: 300}, [1, 2, 3])
    with pytest.raises(ValueError):
        validate_custom_split(1500, {1: 1000, 2: 500}, [1, 2, 3])  # a member is missing


def test_only_completed_capture_counts(db, monkeypatch):
    _, c = cycle(db)
    m = member(db, "Ben")
    a = ps.start_approval_payment(db, membership=m, cycle=c, kind="SHARE")
    monkeypatch.setattr(MockPayPalProvider, "capture_order",
                        lambda self, order_id, request_id: CaptureResult(order_id=order_id, status="APPROVED"))
    ps.complete_approval(db, a.paypal_order_id)
    db.commit()
    assert a.status == "FAILED"
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 0
    assert pot_numbers(db, group(db).id)["collected_minor"] == 0
    monkeypatch.undo()
    pay_share(db, "Ben")
    assert pot_numbers(db, group(db).id)["collected_minor"] == 500


def test_duplicate_idempotency_key_writes_one_row(db):
    g = group(db)
    for _ in range(3):
        write_entry(db, group_id=g.id, entry_type="CONTRIBUTION", amount_minor=500, source_label="MOCK PAYPAL",
                    idempotency_key="same-key")
    db.commit()
    assert db.scalar(select(func.count()).select_from(LedgerEntry)) == 1
    # Capturing the same order twice is also idempotent.
    a = pay_share(db, "Asha")
    ps.complete_approval(db, a.paypal_order_id)
    db.commit()
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.attempt_id == a.id)) == 1


def test_pot_availability_formula(db):
    g = group(db)
    w = lambda t, amt, key, **kw: write_entry(db, group_id=g.id, entry_type=t, amount_minor=amt,  # noqa: E731
                                              source_label="SIMULATED", idempotency_key=key, **kw)
    w("CONTRIBUTION", 1000, "c1")
    w("SPEND", 200, "s1")
    p = pot_numbers(db, g.id)
    assert (p["collected_minor"], p["spent_minor"], p["reserved_minor"], p["available_minor"]) == (1000, 200, 0, 800)
    t = Task(group_id=g.id, created_by=1, prompt="x", budget_minor=300, spec={})
    db.add(t)
    db.flush()
    w("FORK", 300, "f1", task_id=t.id)
    assert pot_numbers(db, g.id)["reserved_minor"] == 300
    assert pot_numbers(db, g.id)["available_minor"] == 500
    w("SPEND", 100, "s2", task_id=t.id)
    w("MERGE_BACK", 200, "m1", task_id=t.id)
    p = pot_numbers(db, g.id)
    assert (p["spent_minor"], p["reserved_minor"], p["available_minor"]) == (300, 0, 700)
    w("SPEND", 5000, "s3")
    assert pot_numbers(db, g.id)["available_minor"] == 0  # never below zero


def test_renewal_pauses_when_short_and_never_raises_shares(db):
    sub, c = cycle(db)
    pay_share(db, "Asha")
    pay_share(db, "Ben")
    shares_before = dict(c.shares)
    cycles.job_renew(db, {"cycle_id": c.id})
    db.commit()
    db.refresh(c)
    db.refresh(sub)
    assert c.status == "PAUSED" and sub.status == "PAUSED"
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.entry_type == "SPEND")) == 0
    assert c.shares == shares_before
    # Paying the missing share and renewing again works.
    pay_share(db, "Chloe")
    cycles.job_renew(db, {"cycle_id": c.id})
    db.commit()
    db.refresh(c)
    assert c.status == "RENEWED"


def _new_task(db, budget=20000, mode="CONFIRMED"):
    for n in ("Asha", "Ben", "Chloe"):
        top_up(db, n, 7000)
    g = group(db)
    asha = db.scalar(select(User).where(User.name == "Asha"))
    t = race.create_task(db, g, asha, "Book a hotel in Austin for 2 nights under $200", budget, mode)
    for n in ("Ben", "Chloe"):  # E4: every participant consents before the fork
        race.consent(db, t, member(db, n), "AGREE")
    db.commit()
    assert t.status == "SEARCHING"
    return t


def test_two_concurrent_claims_produce_one_winner(db):
    t = _new_task(db)
    runs = list(db.scalars(select(AgentRun).where(AgentRun.task_id == t.id)))
    cands = []
    for r, ref, price in ((runs[0], "A", 8900), (runs[1], "B", 9500)):
        c = Candidate(task_id=t.id, agent_run_id=r.id, hotel_ref=ref, name=ref, city="Austin", nightly_minor=price,
                      total_minor=price * 2, rating="4.5")
        db.add(c)
        db.flush()
        cands.append((r.agent_name, c.id))
    db.commit()
    barrier = threading.Barrier(2)
    results = []

    def go(agent, cid):
        s = dbmod.SessionLocal()
        try:
            barrier.wait()
            results.append(tools.claim_task(s, t.id, agent, cid))
        finally:
            s.close()

    th = [threading.Thread(target=go, args=a) for a in cands]
    [x.start() for x in th]
    [x.join() for x in th]
    assert len([r for r in results if r]) == 1
    db.expire_all()
    assert db.get(Task, t.id).status == "LOCKED"


def test_unknown_booking_keeps_the_lock(db):
    t = _new_task(db, mode="UNKNOWN")
    race.job_run_task(db, {"task_id": t.id})
    db.expire_all()
    t = db.get(Task, t.id)
    assert t.status == "NEEDS_REVIEW"
    assert t.lock_token is not None and t.winner_agent is not None
    assert pot_numbers(db, t.group_id)["reserved_minor"] == t.budget_minor
    assert db.scalar(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.task_id == t.id,
                                                                         LedgerEntry.entry_type == "SPEND")) == 0
    # The policy layer refuses a second card while the first is unresolved.
    with pytest.raises(tools.PolicyError):
        tools.request_card(db, t.id, "wrong-token")


def test_merge_back_returns_unused_amount(db):
    t = _new_task(db)
    available_before = pot_numbers(db, t.group_id)["available_minor"] + t.budget_minor  # before the fork
    race.job_run_task(db, {"task_id": t.id})
    db.expire_all()
    t = db.get(Task, t.id)
    assert t.status == "COMPLETED"
    assert 0 < t.final_price_minor <= t.budget_minor
    mb = db.scalar(select(LedgerEntry).where(LedgerEntry.task_id == t.id, LedgerEntry.entry_type == "MERGE_BACK"))
    assert mb.amount_minor == t.budget_minor - t.final_price_minor
    p = pot_numbers(db, t.group_id)
    assert p["reserved_minor"] == 0
    assert p["available_minor"] == available_before - t.final_price_minor


def test_fork_cannot_exceed_available(db):
    g = group(db)
    asha = db.scalar(select(User).where(User.name == "Asha"))
    with pytest.raises(ValueError):
        race.create_task(db, g, asha, "Book a hotel in Austin for 2 nights", 20000, "CONFIRMED")

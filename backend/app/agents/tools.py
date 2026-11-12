"""The only things an agent can do. An allowlist of Python functions; each re-checks policy in backend code.
No SQL, shell, raw HTTP or PayPal access is exposed to any model. Amounts, payees and caps come from the database.
(Mirrors the PayPal Agent Toolkit shape: create_order / get_order / pay_order sit behind the same kind of policy gate.)
"""
import json
import logging
import os
import random
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import LABEL_SIMULATED, Candidate, MockCard, Task, utcnow
from ..providers.paypal import PayPalError, get_provider
from ..services.ledger import pot_numbers, write_entry
from . import planner

log = logging.getLogger("tools")
CATALOG_DIR = os.path.join(os.path.dirname(__file__), "catalogs")
MERCHANT = "DEMO_HOTEL"
CARD_TTL = timedelta(minutes=10)


class PolicyError(Exception):
    pass


# ---------- parse_task ----------
def parse_task(text: str, budget_minor: int):
    return planner.plan(text, budget_minor)


# ---------- search_hotels ----------
def load_catalog(name: str) -> dict:
    if name not in ("alpha", "beta", "gamma"):
        raise PolicyError("unknown catalog")
    with open(os.path.join(CATALOG_DIR, f"{name}.json"), encoding="utf-8") as f:
        return json.load(f)


def search_hotels(catalog: str, spec: dict, strategy: str) -> list[dict]:
    """Pure function over a mock catalog. Returns candidates that satisfy every filter."""
    cat = load_catalog(catalog)
    nights = int(spec["nights"])
    out = []
    for h in cat["hotels"]:
        total = int(h["nightly_minor"]) * nights
        if h["city"].lower() != spec["city"].lower():
            continue
        if total > int(spec["max_total_minor"]):
            continue
        if Decimal(h["rating"]) < Decimal(spec.get("min_rating") or "0"):
            continue
        out.append({**h, "total_minor": total})
    if strategy == "cheapest":
        out.sort(key=lambda h: h["total_minor"])
    elif strategy == "best_rated":
        out.sort(key=lambda h: Decimal(h["rating"]), reverse=True)
    else:
        random.shuffle(out)
    return out


def candidate_is_valid(task: Task, c: Candidate) -> bool:
    spec = task.spec
    return (c.city.lower() == spec["city"].lower() and c.total_minor <= task.budget_minor
            and c.total_minor == c.nightly_minor * int(spec["nights"])
            and Decimal(c.rating) >= Decimal(spec.get("min_rating") or "0"))


# ---------- claim_task ----------
def claim_task(db: Session, task_id: int, agent_name: str, candidate_id: int) -> str | None:
    """Atomic compare-and-set. Returns the lock token if this agent won, else None."""
    task = db.get(Task, task_id)
    c = db.get(Candidate, candidate_id)
    if c is None or c.task_id != task_id or not candidate_is_valid(task, c):
        raise PolicyError("candidate does not satisfy the task")
    token = secrets.token_hex(8)
    res = db.execute(
        update(Task)
        .where(Task.id == task_id, Task.status == "SEARCHING")
        .values(winner_agent=agent_name, winner_candidate_id=candidate_id, lock_token=token, status="LOCKED",
                updated_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return token if res.rowcount == 1 else None


# ---------- request_card ----------
def request_card(db: Session, task_id: int, lock_token: str) -> MockCard:
    task = db.get(Task, task_id)
    db.refresh(task)
    if task.status != "LOCKED" or task.lock_token != lock_token:
        raise PolicyError("only the lock holder can request a card")
    c = db.get(Candidate, task.winner_candidate_id)
    if not candidate_is_valid(task, c):
        raise PolicyError("price is over the task budget")
    # The fork reservation must still cover the price.
    if pot_numbers(db, task.group_id)["reserved_minor"] < c.total_minor:
        raise PolicyError("reservation does not cover this price")
    if db.scalar(select(MockCard).where(MockCard.task_id == task_id, MockCard.status == "ACTIVE")):
        raise PolicyError("an active card already exists for this task")
    card = MockCard(task_id=task_id, reference=f"MCARD-{uuid.uuid4().hex[:10].upper()}",
                    last4=f"{random.randint(0, 9999):04d}", limit_minor=c.total_minor, merchant_lock=MERCHANT,
                    single_use=True, expires_at=utcnow() + CARD_TTL, status="ACTIVE")
    db.add(card)
    db.commit()
    return card


# ---------- book_hotel ----------
def book_hotel(db: Session, task_id: int, card_ref: str, attempt_no: int) -> tuple[str, str | None]:
    """Mock booking provider. Returns (CONFIRMED|DECLINED|UNKNOWN, booking_ref)."""
    task = db.get(Task, task_id)
    card = db.scalar(select(MockCard).where(MockCard.reference == card_ref))
    c = db.get(Candidate, task.winner_candidate_id)
    if card is None or card.task_id != task_id or card.status != "ACTIVE":
        raise PolicyError("card is not active for this task")
    if card.expires_at.replace(tzinfo=card.expires_at.tzinfo or utcnow().tzinfo) < utcnow():
        card.status = "CLOSED"
        db.commit()
        raise PolicyError("card expired")
    if card.merchant_lock != MERCHANT or c.total_minor > card.limit_minor:
        raise PolicyError("charge outside the card's merchant lock or limit")
    mode = task.booking_mode
    if mode == "UNKNOWN":
        outcome = "UNKNOWN"
    elif mode == "DECLINE_FIRST" and attempt_no == 1:
        outcome = "DECLINED"
    else:
        outcome = "CONFIRMED"
    if outcome == "CONFIRMED":
        card.status = "USED"
        ref = f"BK-{uuid.uuid4().hex[:8].upper()}"
    elif outcome == "DECLINED":
        card.status = "CLOSED"
        ref = None
    else:
        ref = None  # card stays ACTIVE: we don't know whether it was charged
    db.commit()
    return outcome, ref


# ---------- pay_merchant ----------
def pay_merchant(db: Session, task_id: int) -> dict:
    """Pay the demo hotel the confirmed price and write the SPEND row. Payee and amount come from the DB."""
    s = get_settings()
    task = db.get(Task, task_id)
    c = db.get(Candidate, task.winner_candidate_id)
    if task.booking_status != "CONFIRMED" or task.status != "BOOKING":
        raise PolicyError("no confirmed booking to pay")
    from ..services.race import spend_allocation

    amount = c.total_minor
    provider = get_provider()
    label, batch_id, note = LABEL_SIMULATED, None, ""
    if not s.paypal_real:
        res = provider.payout(receiver_email="demo-hotel@mock", amount_minor=amount,
                              sender_batch_id=f"fw-task-{task.id}", note=f"Booking {task.booking_ref}")
        label, batch_id, note = provider.label, res.batch_id, f"Mock payout {res.status}"
    elif s.paypal_payouts_enabled and s.demo_hotel_paypal_email:
        try:
            res = provider.payout(receiver_email=s.demo_hotel_paypal_email, amount_minor=amount,
                                  sender_batch_id=f"fw-task-{task.id}", note=f"Booking {task.booking_ref}")
            label, batch_id, note = provider.label, res.batch_id, f"PayPal payout batch {res.status}"
        except PayPalError as e:
            note = f"PayPal payout failed ({e}); recorded as SIMULATED"
    else:
        note = "PayPal payouts are disabled (PAYPAL_PAYOUTS_ENABLED=false); hotel payment recorded as SIMULATED"
    write_entry(db, group_id=task.group_id, entry_type="SPEND", amount_minor=amount, source_label=label,
                idempotency_key=f"task-spend-{task.id}", task_id=task.id, paypal_payout_batch_id=batch_id,
                allocation=spend_allocation(task, amount) or None,
                description=f"{c.name}, {task.spec['nights']} nights (booking {task.booking_ref}, mock card)")
    task.payout_label, task.payout_batch_id, task.payout_note = label, batch_id, note
    db.commit()
    return {"label": label, "batch_id": batch_id, "note": note}


TOOLS = {
    "parse_task": parse_task,
    "search_hotels": search_hotels,
    "claim_task": claim_task,
    "request_card": request_card,
    "book_hotel": book_hotel,
    "pay_merchant": pay_merchant,
}


def call_tool(name: str, *args, **kwargs):
    fn = TOOLS.get(name)
    if fn is None:
        raise PolicyError(f"tool {name!r} is not on the allowlist")
    log.info("tool %s", name)
    return fn(*args, **kwargs)

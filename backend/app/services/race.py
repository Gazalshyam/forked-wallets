"""AI task: consent -> fork -> three agents race -> one lock -> mock card -> booking -> payout -> merge back.

E4: a task names its participants. Only their parts of the pot (the sub-pot) can fund it, split equally and capped at
each person's balance. Every participant is asked first; the agents start when all agree, or when the consent window
(CONSENT_TIMEOUT_SECONDS, default 5 minutes) ends, at which point the owner's authorization stands in for anyone who
did not answer. A participant who declines is dropped and their money is not used.
"""
import asyncio
import logging
import random
import time
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import db as dbmod
from ..agents.tools import PolicyError, call_tool
from ..config import get_settings
from ..models import LABEL_SIMULATED, AgentRun, Candidate, Group, LedgerEntry, Membership, MockCard, Notification, Task, User, utcnow
from ..providers import llm
from .groups import active_members, member_name
from .ledger import member_balances, pot_numbers, write_entry
from .money import capped_split, fmt_usd
from .notify import notify

log = logging.getLogger("race")

AGENTS = [
    ("Agent Alpha", "alpha", "cheapest"),
    ("Agent Beta", "beta", "best_rated"),
    ("Agent Gamma", "gamma", "random"),
]
BOOKING_MODES = ("CONFIRMED", "DECLINE_FIRST", "UNKNOWN")
DELAY_RANGE = (0.8, 3.2)  # simulated provider latency in seconds; tests shrink it


def add_event(task: Task, kind: str, msg: str) -> None:
    task.events = list(task.events or []) + [{"at": utcnow().isoformat(), "kind": kind, "msg": msg}]


# ---------------------------------------------------------------- create, consent, start (API)
def sub_pot(db: Session, group_id: int, participant_ids: list[int]) -> tuple[int, dict[int, int]]:
    """What each participant can put in (never below 0) and the total. Money already paid toward the current
    cycle is left out: the renewal needs it."""
    from .balances import committed_to_current_cycle

    bal = member_balances(db, group_id)
    caps = {mid: max(0, bal.get(mid, {}).get("free_minor", 0) - committed_to_current_cycle(db, mid, group_id))
            for mid in participant_ids}
    return sum(caps.values()), caps


def _check_funds(db: Session, group_id: int, budget_minor: int, participant_ids: list[int]) -> dict[int, int]:
    total, caps = sub_pot(db, group_id, participant_ids)
    if budget_minor > total:
        who = ", ".join(f"{member_name(db, k)} {fmt_usd(v)}" for k, v in caps.items())
        raise ValueError(f"Budget {fmt_usd(budget_minor)} is more than the participants have in the pot "
                         f"({fmt_usd(total)}: {who}).")
    available = pot_numbers(db, group_id)["available_minor"]
    if budget_minor > available:
        raise ValueError(f"Budget {fmt_usd(budget_minor)} is more than the pot has available ({fmt_usd(available)}).")
    return caps


def create_task(db: Session, group: Group, user: User, prompt: str, budget_minor: int, booking_mode: str,
                participant_ids: list[int] | None = None) -> Task:
    from .cycles import enqueue

    if booking_mode not in BOOKING_MODES:
        raise ValueError("bad booking mode")
    if not isinstance(budget_minor, int) or budget_minor <= 0:
        raise ValueError("budget must be a positive number of cents")
    members = {m.id: m for m in active_members(db, group.id)}
    parts = sorted(set(participant_ids)) if participant_ids else sorted(members)
    if not parts or any(p not in members for p in parts):
        raise ValueError("participants must be active members of this group")
    spec, source = call_tool("parse_task", prompt, budget_minor)
    _check_funds(db, group.id, budget_minor, parts)
    owner = next(m for m in members.values() if m.user_id == user.id)
    deadline = utcnow() + timedelta(seconds=get_settings().consent_timeout_seconds)
    task = Task(group_id=group.id, created_by=user.id, prompt=prompt, budget_minor=budget_minor,
                spec=spec.model_dump(), status="AWAITING_CONSENT", booking_mode=booking_mode, events=[],
                participants=parts, consent={str(owner.id): "AGREE"} if owner.id in parts else {},
                consent_deadline=deadline)
    db.add(task)
    db.flush()
    add_event(task, "plan", f"{source} -> {spec.city}, {spec.nights} night(s), {spec.guests} guest(s), "
                            f"rating >= {spec.min_rating}, max {fmt_usd(budget_minor)}")
    names = ", ".join(member_name(db, p) for p in parts)
    total, _ = sub_pot(db, group.id, parts)
    add_event(task, "consent", f"Sub-pot: {names} ({fmt_usd(total)} between them). Asking for consent; "
                               f"the owner's authorization is used for anyone silent after {_mins()}.")
    for name, catalog, _ in AGENTS:
        db.add(AgentRun(task_id=task.id, agent_name=name, catalog=catalog, status="QUEUED"))
    db.flush()
    _, caps = sub_pot(db, group.id, parts)
    preview = capped_split(budget_minor, caps)
    for p in parts:
        if task.consent.get(str(p)):
            continue
        notify(db, user_id=members[p].user_id, group_id=group.id, kind="CONSENT",
               title=f"{user.name} wants to use up to {fmt_usd(preview[p])} of your part of the pot",
               body=f"Task: \"{prompt}\" with a budget of {fmt_usd(budget_minor)} shared by {names}. "
                    f"Unused money returns to you. If you do not answer within {_mins()}, {user.name}'s "
                    "authorization is used. Decline and your money is left out.",
               action={"type": "TASK_CONSENT", "task_id": task.id, "amount_minor": preview[p]},
               dedupe_key=f"consent-{task.id}-{p}")
    if _all_agreed(task):
        start_task(db, task)
    else:
        enqueue(db, "CONSENT_TIMEOUT", {"task_id": task.id}, run_at=deadline, dedupe_key=f"CONSENT-{task.id}")
    db.flush()
    return task


def _mins() -> str:
    s = get_settings().consent_timeout_seconds
    return f"{s // 60} minute(s)" if s >= 60 else f"{s} seconds"


def _all_agreed(task: Task) -> bool:
    return all((task.consent or {}).get(str(p)) in ("AGREE", "OWNER_AUTHORIZED") for p in task.participants or [])


def _clear_consent_prompts(db: Session, task: Task, user_ids: list[int] | None = None) -> None:
    for n in db.scalars(select(Notification).where(Notification.kind == "CONSENT", Notification.read_at.is_(None),
                                                   Notification.group_id == task.group_id)):
        if (n.action or {}).get("task_id") == task.id and (user_ids is None or n.user_id in user_ids):
            n.read_at = utcnow()


def _cancel(db: Session, task: Task, why: str) -> None:
    task.status, task.error = "CANCELLED", why
    add_event(task, "consent", why + " Nothing was reserved or spent.")
    for r in db.scalars(select(AgentRun).where(AgentRun.task_id == task.id)):
        r.status = "CANCELLED"
    _clear_consent_prompts(db, task)
    ids = set(task.participants or []) | {m.id for m in active_members(db, task.group_id) if m.role == "OWNER"}
    for mid in ids:
        notify(db, user_id=db.get(Membership, mid).user_id, group_id=task.group_id, kind="INFO",
               title=f"Task #{task.id} cancelled", body=why, dedupe_key=f"task-cancel-{task.id}-{mid}")
    db.flush()


def consent(db: Session, task: Task, m: Membership, choice: str) -> Task:
    if task.status != "AWAITING_CONSENT":
        raise ValueError("This task is not waiting for consent")
    if m.id not in (task.participants or []):
        raise ValueError("You are not a participant in this task")
    if choice not in ("AGREE", "DECLINE"):
        raise ValueError("choice must be AGREE or DECLINE")
    if (task.consent or {}).get(str(m.id)):
        raise ValueError("You have already answered")
    name = member_name(db, m.id)
    task.consent = {**(task.consent or {}), str(m.id): choice}
    _clear_consent_prompts(db, task, [m.user_id])
    if choice == "DECLINE":
        task.participants = [p for p in task.participants if p != m.id]
        add_event(task, "consent", f"{name} declined; their money is left out of this task")
        if not task.participants:
            _cancel(db, task, f"{name} declined and nobody else is funding the task.")
            return task
        total, _ = sub_pot(db, task.group_id, task.participants)
        if total < task.budget_minor:
            _cancel(db, task, f"{name} declined. The remaining participants have {fmt_usd(total)}, "
                              f"less than the {fmt_usd(task.budget_minor)} budget.")
            return task
    else:
        add_event(task, "consent", f"{name} agreed")
    if _all_agreed(task):
        start_task(db, task)
    db.flush()
    return task


def consent_timeout(db: Session, task: Task, by_owner: bool = False) -> None:
    """The window has ended (or the owner skipped the wait in the demo): owner's authorization covers the silent."""
    if task.status != "AWAITING_CONSENT":
        return
    silent = [p for p in task.participants if not (task.consent or {}).get(str(p))]
    task.consent = {**(task.consent or {}), **{str(p): "OWNER_AUTHORIZED" for p in silent}}
    if silent:
        names = ", ".join(member_name(db, p) for p in silent)
        how = "the owner skipped the wait (demo control)" if by_owner else f"no answer within {_mins()}"
        add_event(task, "consent", f"{names}: {how}; the owner's authorization is used")
        owner = db.get(User, task.created_by)
        for p in silent:
            notify(db, user_id=db.get(Membership, p).user_id, group_id=task.group_id, kind="INFO",
                   title=f"Task #{task.id} went ahead on {owner.name}'s authorization",
                   body=f"You did not answer the consent request, so {owner.name}'s authorization was used for "
                        "your part of the budget. Unused money returns to you.",
                   dedupe_key=f"owner-auth-{task.id}-{p}")
    _clear_consent_prompts(db, task)
    start_task(db, task)
    db.flush()


def start_task(db: Session, task: Task) -> None:
    """Fork the participants' money and let the agents run."""
    from .cycles import enqueue

    # Serialize forks per group so two tasks cannot both reserve the same dollars.
    if db.bind.dialect.name == "postgresql":
        db.execute(select(Group.id).where(Group.id == task.group_id).with_for_update())
    try:
        caps = _check_funds(db, task.group_id, task.budget_minor, task.participants)
    except ValueError as e:
        _cancel(db, task, f"Funds changed while waiting for consent. {e}")
        return
    alloc = capped_split(task.budget_minor, caps)
    available = pot_numbers(db, task.group_id)["available_minor"]
    task.allocation = {str(k): v for k, v in alloc.items()}
    task.status = "SEARCHING"
    who = ", ".join(f"{member_name(db, k)} {fmt_usd(v)}" for k, v in alloc.items())
    write_entry(db, group_id=task.group_id, entry_type="FORK", amount_minor=task.budget_minor, source_label=LABEL_SIMULATED,
                idempotency_key=f"fork-{task.id}", task_id=task.id, allocation=dict(task.allocation),
                description=f"Fork: {fmt_usd(task.budget_minor)} slice reserved for task #{task.id} from {who} "
                            "(database reservation, no money moves)")
    add_event(task, "fork", f"Forked {fmt_usd(task.budget_minor)} from the pot ({who}); "
                            f"{fmt_usd(available)} -> {fmt_usd(available - task.budget_minor)} available")
    enqueue(db, "RUN_TASK", {"task_id": task.id}, dedupe_key=f"RUN_TASK-{task.id}")
    db.flush()


def job_consent_timeout(db: Session, payload: dict) -> None:
    task = db.get(Task, int(payload["task_id"]))
    if task is not None:
        consent_timeout(db, task)


# ---------------------------------------------------------------- helpers (each opens its own session; run in threads)
def _set_run(run_id: int, **values) -> None:
    with dbmod.session_scope() as db:
        r = db.get(AgentRun, run_id)
        for k, v in values.items():
            setattr(r, k, v)


def _save_candidates(task_id: int, run_id: int, hotels: list[dict]) -> list[int]:
    with dbmod.session_scope() as db:
        ids = []
        for h in hotels:
            c = Candidate(task_id=task_id, agent_run_id=run_id, hotel_ref=h["ref"], name=h["name"], city=h["city"],
                          nightly_minor=int(h["nightly_minor"]), total_minor=int(h["total_minor"]), rating=h["rating"])
            db.add(c)
            db.flush()
            ids.append(c.id)
        return ids


def _claim(task_id: int, agent: str, candidate_id: int) -> str | None:
    db = dbmod.SessionLocal()
    try:
        return call_tool("claim_task", db, task_id, agent, candidate_id)
    finally:
        db.close()


async def _reason(agent: str, hotel: dict, spec: dict) -> str:
    left = int(spec["max_total_minor"]) - int(hotel["total_minor"])
    fallback = (f"{hotel['name']}: rated {hotel['rating']}, {fmt_usd(int(hotel['total_minor']))} for "
                f"{spec['nights']} night(s), {fmt_usd(left)} under budget.")
    out = await llm.acomplete(
        f"You are {agent}, a hotel search agent. In one sentence of at most 140 characters, say why this hotel fits "
        "the request. Use only the facts given. No prices other than those given.",
        f"Request: {spec['city']}, {spec['nights']} nights, {spec['guests']} guests, min rating {spec['min_rating']}.\n"
        f"Hotel: {hotel['name']}, rating {hotel['rating']}, total {fmt_usd(int(hotel['total_minor']))}, "
        f"{fmt_usd(left)} under budget.", max_tokens=80)
    if not out or len(out) > 220:
        return fallback
    return out.strip().strip('"') + f"  [{llm.label()}]"


async def _agent(task_id: int, run_id: int, name: str, catalog: str, strategy: str, spec: dict,
                 won: asyncio.Event) -> tuple | None:
    t0 = time.monotonic()
    ms = lambda: int((time.monotonic() - t0) * 1000)  # noqa: E731
    await asyncio.to_thread(_set_run, run_id, status="SEARCHING")
    await asyncio.sleep(random.uniform(*DELAY_RANGE))  # simulated provider latency
    if won.is_set():
        await asyncio.to_thread(_set_run, run_id, status="CANCELLED", elapsed_ms=ms(), finished_at=utcnow(),
                                reason="Stopped: another agent already holds the lock.")
        return None
    hotels = call_tool("search_hotels", catalog, spec, strategy)[:2]
    if not hotels:
        await asyncio.to_thread(_set_run, run_id, status="NO_MATCH", elapsed_ms=ms(), finished_at=utcnow(),
                                reason="No hotel in this catalog meets every filter.")
        return None
    reason = await _reason(name, hotels[0], spec)
    cand_ids = await asyncio.to_thread(_save_candidates, task_id, run_id, hotels)
    await asyncio.to_thread(_set_run, run_id, status="FOUND", reason=reason, elapsed_ms=ms())
    token = await asyncio.to_thread(_claim, task_id, name, cand_ids[0])
    if token:
        won.set()
        await asyncio.to_thread(_set_run, run_id, status="WON", elapsed_ms=ms(), finished_at=utcnow())
        return name, cand_ids[0], token
    await asyncio.to_thread(_set_run, run_id, status="LOST", elapsed_ms=ms(), finished_at=utcnow(),
                            reason=reason + "  (claim rejected: task already LOCKED by another agent)")
    return None


async def _race(task_id: int, spec: dict, runs: list[tuple[int, str, str, str]]):
    won = asyncio.Event()
    results = await asyncio.gather(*[_agent(task_id, rid, n, c, s, spec, won) for rid, n, c, s in runs],
                                   return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.error("agent error: %r", r)
    return next((r for r in results if isinstance(r, tuple)), None)


# ---------------------------------------------------------------- after the race
def spend_allocation(task: Task, price_minor: int) -> dict[str, int]:
    """Who pays the actual price: equal among participants, capped at what each put into the fork."""
    fork = {int(k): int(v) for k, v in (task.allocation or {}).items()}
    if not fork:
        return {}
    return {str(k): v for k, v in capped_split(price_minor, fork).items()}


def merge_back(db: Session, task: Task) -> int:
    spent = task.final_price_minor or 0
    unused = task.budget_minor - spent
    if unused > 0:
        spend = db.scalar(select(LedgerEntry).where(LedgerEntry.task_id == task.id, LedgerEntry.entry_type == "SPEND"))
        used = (spend.allocation or {}) if spend else {}
        alloc = {k: int(v) - int(used.get(k, 0)) for k, v in (task.allocation or {}).items()} or None
        write_entry(db, group_id=task.group_id, entry_type="MERGE_BACK", amount_minor=unused, source_label=LABEL_SIMULATED,
                    idempotency_key=f"merge-{task.id}", task_id=task.id, allocation=alloc,
                    description=f"Merge back: unused {fmt_usd(unused)} of task #{task.id} returns to the pot"
                                + (" (" + ", ".join(f"{member_name(db, int(k))} {fmt_usd(v)}" for k, v in alloc.items()) + ")"
                                   if alloc else ""))
    task.merged_back_minor = max(unused, 0)
    add_event(task, "merge", f"Merged {fmt_usd(max(unused, 0))} back into the pot; reservation closed")
    return unused


def _close_cards(db: Session, task_id: int) -> None:
    for c in db.scalars(select(MockCard).where(MockCard.task_id == task_id, MockCard.status == "ACTIVE")):
        c.status = "CLOSED"


def _next_candidate(db: Session, task: Task) -> Candidate | None:
    from ..agents.tools import candidate_is_valid

    for c in db.scalars(select(Candidate).where(Candidate.task_id == task.id, Candidate.status == "PROPOSED")
                        .order_by(Candidate.id)):
        if candidate_is_valid(task, c):
            return c
    return None


def finish_confirmed(db: Session, task: Task) -> None:
    c = db.get(Candidate, task.winner_candidate_id)
    task.status = "BOOKING"
    task.final_price_minor = c.total_minor
    db.commit()
    res = call_tool("pay_merchant", db, task.id)
    db.refresh(task)
    add_event(task, "payout", f"Hotel paid {fmt_usd(c.total_minor)}: {res['label']}"
                              + (f" batch {res['batch_id']}" if res["batch_id"] else "") + f". {res['note']}")
    merge_back(db, task)
    task.status = "COMPLETED"
    db.commit()


def book_with_lock(db: Session, task: Task, token: str) -> None:
    attempt = 0
    while True:
        attempt += 1
        c = db.get(Candidate, task.winner_candidate_id)
        c.status = "WON"
        card = call_tool("request_card", db, task.id, token)
        add_event(task, "card", f"Mock card {card.reference} issued: ****{card.last4}, limit {fmt_usd(card.limit_minor)}, "
                                f"merchant {card.merchant_lock}, single use, expires in 10 min")
        db.commit()
        outcome, ref = call_tool("book_hotel", db, task.id, card.reference, attempt)
        db.refresh(task)
        task.booking_status = outcome
        if outcome == "CONFIRMED":
            task.booking_ref = ref
            add_event(task, "booking", f"{c.name} CONFIRMED, booking {ref} (mock booking provider)")
            db.commit()
            finish_confirmed(db, task)
            return
        if outcome == "UNKNOWN":
            task.status = "NEEDS_REVIEW"
            add_event(task, "booking", f"{c.name}: outcome UNKNOWN. Lock and reservation kept; no retry until reviewed.")
            db.commit()
            return
        # DECLINED: release the lock and let the next valid candidate compete.
        c.status = "DECLINED"
        add_event(task, "booking", f"{c.name} DECLINED by the hotel; card closed, lock released")
        db.execute(update(Task).where(Task.id == task.id, Task.lock_token == token)
                   .values(status="SEARCHING", winner_agent=None, winner_candidate_id=None, lock_token=None)
                   .execution_options(synchronize_session=False))
        db.commit()
        db.refresh(task)
        nxt = _next_candidate(db, task)
        if nxt is None:
            task.status = "FAILED"
            task.error = "No remaining candidate after decline"
            merge_back(db, task)
            db.commit()
            return
        agent = db.get(AgentRun, nxt.agent_run_id).agent_name
        token = call_tool("claim_task", db, task.id, agent, nxt.id)
        db.refresh(task)
        if not token:
            raise PolicyError("could not re-claim after decline")
        add_event(task, "lock", f"{agent} claimed the lock with {nxt.name} ({fmt_usd(nxt.total_minor)})")
        db.commit()


def job_run_task(db: Session, payload: dict) -> None:
    task = db.get(Task, int(payload["task_id"]))
    if task is None or task.status != "SEARCHING" or task.winner_agent:
        return
    runs = []
    for r in db.scalars(select(AgentRun).where(AgentRun.task_id == task.id).order_by(AgentRun.id)):
        strategy = next(s for n, _, s in AGENTS if n == r.agent_name)
        runs.append((r.id, r.agent_name, r.catalog, strategy))
    add_event(task, "race", "Three agents searching in parallel")
    db.commit()
    winner = asyncio.run(_race(task.id, dict(task.spec), runs))
    db.expire_all()
    task = db.get(Task, task.id)
    if winner is None:
        task.status = "FAILED"
        task.error = "No agent found a hotel that meets every filter"
        add_event(task, "race", "No valid candidate. Nothing booked.")
        merge_back(db, task)
        db.commit()
        return
    name, cand_id, token = winner
    c = db.get(Candidate, cand_id)
    add_event(task, "lock", f"{name} won the lock with {c.name} ({fmt_usd(c.total_minor)}); other agents stopped")
    db.commit()
    try:
        book_with_lock(db, task, token)
    except PolicyError as e:
        db.rollback()
        task = db.get(Task, task.id)
        task.status = "NEEDS_REVIEW"
        task.error = f"Policy stopped the task: {e}"
        add_event(task, "policy", task.error)
        db.commit()
    # Anything still PROPOSED after the task is settled did not win.
    db.refresh(task)
    if task.status in ("COMPLETED", "FAILED"):
        for o in db.scalars(select(Candidate).where(Candidate.task_id == task.id, Candidate.status == "PROPOSED")):
            o.status = "LOST"
        _close_cards(db, task.id)
        db.commit()


def resolve_review(db: Session, task: Task, booked: bool) -> None:
    """Owner reconciles an UNKNOWN booking outcome."""
    if task.status != "NEEDS_REVIEW":
        raise ValueError("task is not waiting for review")
    if booked:
        card = db.scalar(select(MockCard).where(MockCard.task_id == task.id, MockCard.status == "ACTIVE"))
        if card:
            card.status = "USED"
        task.booking_status = "CONFIRMED"
        task.booking_ref = task.booking_ref or f"BK-REVIEWED-{task.id}"
        add_event(task, "review", "Owner confirmed the hotel did book")
        db.commit()
        finish_confirmed(db, task)
    else:
        _close_cards(db, task.id)
        task.booking_status = "NOT_BOOKED"
        task.status = "CANCELLED"
        task.lock_token = None
        add_event(task, "review", "Owner confirmed no booking happened; lock released")
        merge_back(db, task)
        db.commit()


JOB_HANDLERS = {"RUN_TASK": job_run_task, "CONSENT_TIMEOUT": job_consent_timeout}

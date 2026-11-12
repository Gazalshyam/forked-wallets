from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents import planner
from ..db import get_db
from ..models import AgentRun, Candidate, Group, MockCard, Task, User
from ..security import current_user
from ..services import groups as gs
from ..services import race
from ..services.ledger import pot_numbers

router = APIRouter(prefix="/api/v1", tags=["tasks"])


class PlanIn(BaseModel):
    prompt: str = Field(min_length=5, max_length=500)
    budget_minor: int | None = Field(default=None, gt=0)


@router.post("/groups/{group_id}/tasks/plan")
def plan(group_id: int, body: PlanIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    gs.require_owner(db, group_id, user.id)
    suggested = planner.suggest_budget_minor(body.prompt)
    budget = body.budget_minor or suggested or 1
    spec, source = planner.plan(body.prompt, budget)
    _, caps = race.sub_pot(db, group_id, [m.id for m in gs.active_members(db, group_id)])
    return {"spec": spec.model_dump(), "source": source, "suggested_budget_minor": suggested,
            "available_minor": pot_numbers(db, group_id)["available_minor"],
            "members": [{"membership_id": m.id, "name": gs.member_name(db, m.id), "role": m.role,
                         "free_minor": caps[m.id]}
                        for m in gs.active_members(db, group_id)]}


class TaskIn(BaseModel):
    prompt: str = Field(min_length=5, max_length=500)
    budget_minor: int = Field(gt=0)
    booking_mode: str = Field(default="CONFIRMED", pattern="^(CONFIRMED|DECLINE_FIRST|UNKNOWN)$")
    participant_ids: list[int] | None = Field(default=None, max_length=50)  # membership ids; default everyone


@router.post("/groups/{group_id}/tasks")
def create(group_id: int, body: TaskIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, "Group not found")
    gs.require_owner(db, group_id, user.id)
    t = race.create_task(db, g, user, body.prompt.strip(), body.budget_minor, body.booking_mode, body.participant_ids)
    db.commit()
    return {"id": t.id}


def _task_or_404(db: Session, task_id: int, user: User) -> Task:
    t = db.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "Task not found")
    gs.require_member(db, t.group_id, user.id)
    return t


@router.get("/tasks/{task_id}")
def get_task(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _task_or_404(db, task_id, user)
    g = db.get(Group, t.group_id)
    runs = list(db.scalars(select(AgentRun).where(AgentRun.task_id == t.id).order_by(AgentRun.id)))
    cands = list(db.scalars(select(Candidate).where(Candidate.task_id == t.id).order_by(Candidate.id)))
    cards = list(db.scalars(select(MockCard).where(MockCard.task_id == t.id).order_by(MockCard.id)))
    me = gs.get_membership(db, t.group_id, user.id)
    return {
        "id": t.id, "group": {"id": g.id, "name": g.name}, "prompt": t.prompt, "budget_minor": t.budget_minor,
        "spec": t.spec, "status": t.status, "booking_mode": t.booking_mode, "winner_agent": t.winner_agent,
        "winner_candidate_id": t.winner_candidate_id, "lock_token": (t.lock_token[:6] + "…") if t.lock_token else None,
        "booking_ref": t.booking_ref, "booking_status": t.booking_status, "final_price_minor": t.final_price_minor,
        "merged_back_minor": t.merged_back_minor, "payout_label": t.payout_label, "payout_batch_id": t.payout_batch_id,
        "payout_note": t.payout_note, "events": t.events or [], "error": t.error,
        "consent_deadline": t.consent_deadline.isoformat() if t.consent_deadline else None,
        "participants": [{"membership_id": mid, "name": gs.member_name(db, mid), "consent": (t.consent or {}).get(str(mid)),
                          "allocation_minor": (t.allocation or {}).get(str(mid))} for mid in (t.participants or [])],
        "declined": [gs.member_name(db, int(k)) for k, v in (t.consent or {}).items() if v == "DECLINE"],
        "my_consent_open": bool(me and t.status == "AWAITING_CONSENT" and me.id in (t.participants or [])
                                and not (t.consent or {}).get(str(me.id))),
        "is_owner": bool(me and me.role == "OWNER"), "pot": pot_numbers(db, t.group_id),
        "agents": [{
            "id": r.id, "name": r.agent_name, "catalog": r.catalog, "status": r.status, "reason": r.reason,
            "elapsed_ms": r.elapsed_ms,
            "candidates": [{"id": c.id, "name": c.name, "total_minor": c.total_minor, "nightly_minor": c.nightly_minor,
                            "rating": c.rating, "status": c.status, "hotel_ref": c.hotel_ref}
                           for c in cands if c.agent_run_id == r.id],
        } for r in runs],
        "cards": [{"reference": c.reference, "last4": c.last4, "limit_minor": c.limit_minor, "merchant_lock": c.merchant_lock,
                   "single_use": c.single_use, "expires_at": c.expires_at.isoformat(), "status": c.status} for c in cards],
    }


class ResolveIn(BaseModel):
    booked: bool


@router.post("/tasks/{task_id}/resolve")
def resolve(task_id: int, body: ResolveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _task_or_404(db, task_id, user)
    gs.require_owner(db, t.group_id, user.id)
    race.resolve_review(db, t, body.booked)
    db.commit()
    return {"status": t.status}


class ConsentIn(BaseModel):
    choice: str = Field(pattern="^(AGREE|DECLINE)$")


@router.post("/tasks/{task_id}/consent")
def give_consent(task_id: int, body: ConsentIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = _task_or_404(db, task_id, user)
    me = gs.require_member(db, t.group_id, user.id)
    race.consent(db, t, me, body.choice)
    db.commit()
    return {"status": t.status}


@router.post("/tasks/{task_id}/skip-wait")
def skip_wait(task_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Demo control: end the consent window now; the owner's authorization covers anyone who has not answered."""
    t = _task_or_404(db, task_id, user)
    gs.require_owner(db, t.group_id, user.id)
    race.consent_timeout(db, t, by_owner=True)
    db.commit()
    return {"status": t.status}

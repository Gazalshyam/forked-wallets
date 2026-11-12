"""Demo controls: run cycle jobs now (through the worker queue) and force a member's next charge to fail."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Membership, Subscription, User
from ..security import current_user
from ..services import groups as gs
from ..services.cycles import enqueue

router = APIRouter(prefix="/api/v1", tags=["demo"])


def _demo_owner(db: Session, group_id: int, user: User):
    if not get_settings().demo_mode:
        raise HTTPException(404, "Demo mode is off")
    gs.require_owner(db, group_id, user.id)


@router.post("/groups/{group_id}/demo/run/{job}")
def run_job(group_id: int, job: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _demo_owner(db, group_id, user)
    if job not in ("COLLECT", "CHECK", "RENEW"):
        raise HTTPException(422, "job must be COLLECT, CHECK or RENEW")
    sub = db.scalar(select(Subscription).where(Subscription.group_id == group_id))
    cycle = gs.current_cycle(db, sub)
    if cycle is None:
        raise HTTPException(409, "No current cycle")
    j = enqueue(db, job, {"cycle_id": cycle.id, "demo": True})
    db.commit()
    return {"job_id": j.id, "job_type": job, "cycle_id": cycle.id}


class ForceFailIn(BaseModel):
    membership_id: int
    value: bool = True


@router.post("/groups/{group_id}/demo/force-fail")
def force_fail(group_id: int, body: ForceFailIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _demo_owner(db, group_id, user)
    m = db.get(Membership, body.membership_id)
    if m is None or m.group_id != group_id:
        raise HTTPException(404, "Member not found")
    m.force_fail_next = body.value
    db.commit()
    return {"membership_id": m.id, "force_fail_next": m.force_fail_next}

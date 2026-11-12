from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Notification, User, utcnow
from ..security import current_user

router = APIRouter(prefix="/api/v1", tags=["inbox"])


@router.get("/inbox")
def inbox(group_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == user.id)
    if group_id is not None:
        q = q.where(Notification.group_id == group_id)
    rows = db.scalars(q.order_by(desc(Notification.id)).limit(30))
    return {"items": [{
        "id": n.id, "group_id": n.group_id, "kind": n.kind, "title": n.title, "body": n.body, "action": n.action,
        "ai_written": (n.action or {}).get("ai"), "email_status": n.email_status,
        "read_at": n.read_at.isoformat() if n.read_at else None, "created_at": n.created_at.isoformat(),
    } for n in rows]}


@router.post("/inbox/{nid}/read")
def mark_read(nid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = db.get(Notification, nid)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "Not found")
    n.read_at = utcnow()
    db.commit()
    return {"ok": True}

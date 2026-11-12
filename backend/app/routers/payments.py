from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Membership, Subscription, User
from ..security import current_user
from ..services import groups as gs
from ..services import payments as ps
from ..services.money import fmt_usd

router = APIRouter(prefix="/api/v1", tags=["payments"])


class PayIn(BaseModel):
    kind: str = Field(pattern="^(SHARE|TOP_UP|COVER)$")
    amount_minor: int | None = Field(default=None, gt=0)  # only for TOP_UP; SHARE/COVER amounts come from the DB
    covered_membership_id: int | None = None  # COVER only: whose unpaid share to pay
    save_for_auto_debit: bool = False


@router.post("/groups/{group_id}/pay")
def pay(group_id: int, body: PayIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    m = gs.require_member(db, group_id, user.id)
    sub = db.scalar(select(Subscription).where(Subscription.group_id == group_id))
    cycle = gs.current_cycle(db, sub) if sub else None
    a = ps.start_approval_payment(db, membership=m, cycle=cycle, kind=body.kind,
                                  amount_minor=body.amount_minor if body.kind == "TOP_UP" else None,
                                  want_vault=body.save_for_auto_debit,
                                  covered_membership_id=body.covered_membership_id if body.kind == "COVER" else None)
    db.commit()
    return {"attempt_id": a.id, "status": a.status, "approval_url": a.approval_url, "source_label": a.source_label,
            "amount_minor": a.amount_minor, "will_save_token": a.want_vault}


def _back(group_id: int | None, msg: str) -> RedirectResponse:
    target = f"/groups/{group_id}?msg={quote(msg)}" if group_id else f"/?msg={quote(msg)}"
    return RedirectResponse(target, status_code=303)


@router.get("/paypal/return")
def paypal_return(token: str, db: Session = Depends(get_db)):
    """PayPal redirects here with ?token=<order id>&PayerID=... . Capture is the source of truth."""
    try:
        a = ps.complete_approval(db, token)
        db.commit()
    except ValueError as e:
        db.rollback()
        raise HTTPException(404, str(e))
    if a.status == "COMPLETED":
        msg = f"Payment of {fmt_usd(a.amount_minor)} captured ({a.source_label}, capture {a.paypal_capture_id})."
        if a.want_vault:
            msg += " Saved for auto-debit." if db.get(Membership, a.membership_id).vault_token_enc else " PayPal did not return a saved token, so the next cycle will ask for approval."
    else:
        msg = f"Payment not completed: {a.error or a.status}. Nothing was counted."
    return _back(a.group_id, msg)


@router.get("/paypal/cancel")
def paypal_cancel(token: str, db: Session = Depends(get_db)):
    a = ps.cancel_approval(db, token)
    db.commit()
    return _back(a.group_id if a else None, "Payment cancelled at PayPal. Nothing was charged.")

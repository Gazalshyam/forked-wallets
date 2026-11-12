from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import (
    ContributionAttempt,
    Cycle,
    Group,
    Invitation,
    LedgerEntry,
    Membership,
    Subscription,
    Task,
    User,
)
from ..security import current_user
from ..services import groups as gs
from ..services.balances import pot_shares
from ..services.ledger import cycle_collected, member_paid_in_cycle, pot_numbers

router = APIRouter(prefix="/api/v1", tags=["groups"])


def _group_or_404(db: Session, group_id: int) -> Group:
    g = db.get(Group, group_id)
    if g is None:
        raise HTTPException(404, "Group not found")
    return g


def _sub(db: Session, group_id: int) -> Subscription | None:
    return db.scalar(select(Subscription).where(Subscription.group_id == group_id))


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    merchant_name: str = Field(default="StreamFlix", min_length=1, max_length=120)
    price_minor: int = Field(gt=0, le=10_000_00)
    renewal_date: date | None = None


@router.get("/groups")
def list_groups(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Group, Membership).join(Membership, Membership.group_id == Group.id).where(
        Membership.user_id == user.id, Membership.status == "ACTIVE").order_by(Group.id)).all()
    out = []
    for g, m in rows:
        sub = _sub(db, g.id)
        out.append({
            "id": g.id, "name": g.name, "role": m.role, "pot": pot_numbers(db, g.id),
            "member_count": len(gs.active_members(db, g.id)),
            "subscription": {"merchant_name": sub.merchant_name, "price_minor": sub.price_minor, "status": sub.status,
                             "next_renewal_date": sub.next_renewal_date.isoformat()} if sub else None,
        })
    invites = db.execute(select(Invitation, Group).join(Group, Group.id == Invitation.group_id).where(
        Invitation.email == user.email, Invitation.status == "PENDING")).all()
    return {"groups": out, "invitations": [{"id": i.id, "group_id": g.id, "group_name": g.name} for i, g in invites]}


@router.post("/groups")
def create_group(body: GroupIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = gs.create_group(db, user, body.name.strip(), body.merchant_name.strip(), body.price_minor, body.renewal_date)
    from ..services.cycles import schedule_cycle_jobs

    schedule_cycle_jobs(db, gs.current_cycle(db, _sub(db, g.id)))
    db.commit()
    return {"id": g.id}


def decision_view(db: Session, sub: Subscription | None, viewer_mid: int) -> dict | None:
    d = sub.decision if sub else None
    if not d or d.get("status") not in ("OPEN", "PAUSED"):
        return None
    return {
        "id": d["id"], "status": d["status"], "target": d["target"], "left": d["left"],
        "voters": [{"membership_id": int(v), "name": gs.member_name(db, int(v)), "vote": d["votes"].get(v),
                    "old_share_minor": d["old_shares"].get(v), "new_share_minor": d["new_shares"].get(v)} for v in d["voters"]],
        "my_vote": d["votes"].get(str(viewer_mid)), "can_vote": str(viewer_mid) in d["voters"] and d["status"] == "OPEN",
    }


def member_view(db: Session, m: Membership, cycle: Cycle | None, paid: dict[int, int], viewer_id: int) -> dict:
    u = db.get(User, m.user_id)
    share = int(cycle.shares.get(str(m.id), 0)) if cycle else None
    last = None
    if cycle:
        a = db.scalar(select(ContributionAttempt).where(ContributionAttempt.cycle_id == cycle.id,
                                                        ContributionAttempt.membership_id == m.id)
                      .order_by(desc(ContributionAttempt.id)).limit(1))
        if a:
            last = {"id": a.id, "status": a.status, "method": a.method, "source_label": a.source_label, "error": a.error,
                    "approval_url": a.approval_url if (m.user_id == viewer_id and a.status == "APPROVAL_PENDING") else None}
    return {
        "membership_id": m.id, "user_id": u.id, "name": u.name, "email": u.email, "role": m.role,
        "payment_mode": m.payment_mode, "has_saved_token": bool(m.vault_token_enc), "token_label": m.vault_label,
        "custom_share_minor": m.custom_share_minor, "force_fail_next": m.force_fail_next,
        "share_minor": share, "share_note": (cycle.share_notes or {}).get(str(m.id)) if cycle else None,
        "paid_minor": paid.get(m.id, 0) if cycle else 0,
        "is_paid": bool(cycle) and share is not None and paid.get(m.id, 0) >= share, "last_attempt": last,
    }


@router.get("/groups/{group_id}")
def group_detail(group_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _group_or_404(db, group_id)
    me = gs.require_member(db, g.id, user.id)
    sub = _sub(db, g.id)
    cycle = gs.current_cycle(db, sub) if sub else None
    paid = member_paid_in_cycle(db, cycle.id) if cycle else {}
    active = gs.active_members(db, g.id)
    members = [member_view(db, m, cycle, paid, user.id) for m in active]
    active_ids = {m.id for m in active}
    departed = [{"membership_id": int(k), "name": gs.member_name(db, int(k)), "share_minor": int(v),
                 "paid_minor": paid.get(int(k), 0)} for k, v in (cycle.shares.items() if cycle else []) if int(k) not in active_ids]
    invites = list(db.scalars(select(Invitation).where(Invitation.group_id == g.id).order_by(Invitation.id)))
    past_cycles = list(db.scalars(select(Cycle).where(Cycle.group_id == g.id).order_by(desc(Cycle.renewal_date)).limit(6)))
    tasks = list(db.scalars(select(Task).where(Task.group_id == g.id).order_by(desc(Task.id)).limit(5)))
    s = get_settings()
    return {
        "id": g.id, "name": g.name, "currency": g.currency,
        "me": {"membership_id": me.id, "role": me.role, "is_owner": me.role == "OWNER", "payment_mode": me.payment_mode},
        "pot": pot_numbers(db, g.id),
        "pot_shares": pot_shares(db, g.id),
        "subscription": {
            "id": sub.id, "merchant_name": sub.merchant_name, "price_minor": sub.price_minor, "status": sub.status,
            "split_mode": sub.split_mode, "cadence_days": sub.cadence_days,
            "next_renewal_date": sub.next_renewal_date.isoformat(),
        } if sub else None,
        "cycle": {
            "id": cycle.id, "renewal_date": cycle.renewal_date.isoformat(), "required_minor": cycle.required_minor,
            "collected_minor": cycle_collected(db, cycle.id), "status": cycle.status,
        } if cycle else None,
        "cycles": [{"id": c.id, "renewal_date": c.renewal_date.isoformat(), "required_minor": c.required_minor,
                    "collected_minor": cycle_collected(db, c.id), "status": c.status} for c in past_cycles],
        "members": members, "departed": departed, "decision": decision_view(db, sub, me.id),
        "invitations": [{"id": i.id, "email": i.email, "status": i.status} for i in invites],
        "tasks": [{"id": t.id, "prompt": t.prompt, "status": t.status, "budget_minor": t.budget_minor,
                   "final_price_minor": t.final_price_minor} for t in tasks],
        "capabilities": {
            "payment_label": "PAYPAL SANDBOX" if s.paypal_real else "MOCK PAYPAL",
            "auto_debit_available": (not s.paypal_real) or s.paypal_vault_enabled,
            "payouts_enabled": s.paypal_payouts_enabled,
        },
    }


class InviteIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)


@router.post("/groups/{group_id}/invitations")
def invite(group_id: int, body: InviteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _group_or_404(db, group_id)
    gs.require_owner(db, g.id, user.id)
    inv = gs.invite(db, g, user, body.email)
    db.commit()
    return {"id": inv.id, "email": inv.email, "status": inv.status}


@router.post("/invitations/{inv_id}/accept")
def accept(inv_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    inv = db.get(Invitation, inv_id)
    if inv is None:
        raise HTTPException(404, "Invitation not found")
    gs.accept_invitation(db, inv, user)
    db.commit()
    return {"group_id": inv.group_id}


class SplitIn(BaseModel):
    mode: str = Field(pattern="^(EQUAL|CUSTOM)$")
    shares: dict[int, int] | None = None  # membership_id -> cents


@router.put("/groups/{group_id}/split")
def set_split(group_id: int, body: SplitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _group_or_404(db, group_id)
    gs.require_owner(db, g.id, user.id)
    sub = _sub(db, g.id)
    gs.set_custom_split(db, sub, body.shares if body.mode == "CUSTOM" else None)
    applied_now = gs.current_cycle(db, sub).status == "OPEN"
    db.commit()
    return {"ok": True, "split_mode": sub.split_mode, "applied_to_current_cycle": applied_now}


class PaymentModeIn(BaseModel):
    payment_mode: str = Field(pattern="^(AUTO_DEBIT|APPROVAL_EACH_CYCLE)$")


@router.put("/groups/{group_id}/me/payment-mode")
def set_payment_mode(group_id: int, body: PaymentModeIn, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    m = gs.require_member(db, group_id, user.id)
    m.payment_mode = body.payment_mode
    db.commit()
    return {"payment_mode": m.payment_mode}


@router.get("/groups/{group_id}/ledger")
def ledger(group_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _group_or_404(db, group_id)
    gs.require_member(db, g.id, user.id)
    names = {m.id: db.get(User, m.user_id).name for m in db.scalars(select(Membership).where(Membership.group_id == g.id))}
    rows = db.scalars(select(LedgerEntry).where(LedgerEntry.group_id == g.id).order_by(LedgerEntry.id))
    return {
        "group": {"id": g.id, "name": g.name},
        "pot": pot_numbers(db, g.id),
        "entries": [{
            "id": e.id, "created_at": e.created_at.isoformat(), "entry_type": e.entry_type, "amount_minor": e.amount_minor,
            "source_label": e.source_label, "description": e.description,
            "member": names.get(e.membership_id) if e.membership_id else None,
            "on_behalf_of": names.get(e.on_behalf_of_membership_id) if e.on_behalf_of_membership_id else None,
            "cycle_id": e.cycle_id, "task_id": e.task_id,
            "paypal_order_id": e.paypal_order_id, "paypal_capture_id": e.paypal_capture_id,
            "paypal_payout_batch_id": e.paypal_payout_batch_id, "paypal_refund_id": e.paypal_refund_id,
            "allocation": {names.get(int(k), k): v for k, v in (e.allocation or {}).items()} or None,
            "idempotency_key": e.idempotency_key,
        } for e in rows],
    }


@router.post("/groups/{group_id}/leave")
def leave(group_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services.decisions import leave_group

    m = gs.require_member(db, group_id, user.id)
    d = leave_group(db, m)
    db.commit()
    return {"left": True, "decision_opened": "id" in d, "target": d.get("target"),
            "settlement_minor": d.get("settlement_minor", 0)}


class VoteIn(BaseModel):
    choice: str = Field(pattern="^(AGREE|PAUSE)$")


@router.post("/groups/{group_id}/decision/vote")
def vote(group_id: int, body: VoteIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services.decisions import vote as do_vote

    m = gs.require_member(db, group_id, user.id)
    d = do_vote(db, _sub(db, group_id), m, body.choice)
    db.commit()
    return {"status": d["status"], "votes": d["votes"]}


@router.post("/groups/{group_id}/decision/ask-again")
def ask_again(group_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services.decisions import ask_again as do_ask

    gs.require_owner(db, group_id, user.id)
    d = do_ask(db, _sub(db, group_id))
    db.commit()
    return {"status": d["status"], "target": d["target"]}

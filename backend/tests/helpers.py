from sqlalchemy import select

from app.models import Group, Membership, Subscription, User
from app.services import groups as gs
from app.services import payments as ps


def group(db):
    return db.scalar(select(Group).where(Group.name == "Flatmates"))


def member(db, name):
    u = db.scalar(select(User).where(User.name == name))
    return db.scalar(select(Membership).where(Membership.user_id == u.id))


def cycle(db):
    sub = db.scalar(select(Subscription).where(Subscription.group_id == group(db).id))
    return sub, gs.current_cycle(db, sub)


def pay_share(db, name):
    _, c = cycle(db)
    a = ps.start_approval_payment(db, membership=member(db, name), cycle=c, kind="SHARE")
    ps.complete_approval(db, a.paypal_order_id)
    db.commit()
    return a


def top_up(db, name, amount_minor):
    a = ps.start_approval_payment(db, membership=member(db, name), cycle=None, kind="TOP_UP", amount_minor=amount_minor)
    ps.complete_approval(db, a.paypal_order_id)
    db.commit()
    return a

"""Demo group seed: Asha (owner), Ben, Chloe share a $15 StreamFlix plan."""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Group, Invitation, Membership
from ..seed import seed_users
from .groups import create_group, open_cycle

DEMO_GROUP_NAME = "Flatmates"


def seed_demo_group(db: Session) -> Group:
    existing = db.scalar(select(Group).where(Group.name == DEMO_GROUP_NAME))
    if existing:
        return existing
    users = seed_users(db)
    asha, ben, chloe = users["Asha"], users["Ben"], users["Chloe"]
    g = create_group(db, asha, DEMO_GROUP_NAME, "StreamFlix", 1500, renewal_date=date.today() + timedelta(days=7))
    for u in (ben, chloe):
        db.add(Invitation(group_id=g.id, email=u.email, invited_by=asha.id, status="ACCEPTED"))
        db.add(Membership(group_id=g.id, user_id=u.id, role="MEMBER"))
    db.flush()
    # re-snapshot the (untouched) first cycle now that all three are in
    from ..models import Subscription
    from .groups import refresh_cycle_if_untouched

    sub = db.scalar(select(Subscription).where(Subscription.group_id == g.id))
    refresh_cycle_if_untouched(db, sub)
    from .cycles import schedule_cycle_jobs

    schedule_cycle_jobs(db, open_cycle(db, sub))
    return g

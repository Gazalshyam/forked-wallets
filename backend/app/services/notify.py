"""In-app inbox plus a worker/api log line. Email is not sent in this build (column records that)."""
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Notification, User

log = logging.getLogger("notify")


def notify(db: Session, *, user_id: int, kind: str, title: str, body: str = "", group_id: int | None = None,
           action: dict | None = None, dedupe_key: str | None = None) -> Notification | None:
    if dedupe_key and db.scalar(select(Notification).where(Notification.dedupe_key == dedupe_key)):
        return None
    user = db.get(User, user_id)
    n = Notification(user_id=user_id, group_id=group_id, kind=kind, title=title, body=body, action=action,
                     dedupe_key=dedupe_key,
                     email_status="NOT_SENT (demo: email disabled)" if user and user.email_notifications else "OPTED_OUT")
    try:
        with db.begin_nested():
            db.add(n)
            db.flush()
    except IntegrityError:
        return None
    log.info("NOTIFY user=%s (%s) kind=%s title=%r [push=in-app inbox, email=%s]",
             user_id, user.email if user else "?", kind, title, n.email_status)
    return n

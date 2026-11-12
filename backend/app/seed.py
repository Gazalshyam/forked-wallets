"""Idempotent demo seed: three users. Group seeding lives in services/demo.py."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User
from .security import hash_password

DEMO_PASSWORD = "demo1234"
DEMO_USERS = [
    ("asha@demo.test", "Asha"),
    ("ben@demo.test", "Ben"),
    ("chloe@demo.test", "Chloe"),
]


def seed_users(db: Session) -> dict[str, User]:
    out = {}
    for email, name in DEMO_USERS:
        u = db.scalar(select(User).where(User.email == email))
        if u is None:
            u = User(email=email, name=name, password_hash=hash_password(DEMO_PASSWORD))
            db.add(u)
            db.flush()
        out[name] = u
    return out


def run_seed(db: Session) -> None:
    from .services.demo import seed_demo_group

    seed_users(db)
    seed_demo_group(db)
    db.commit()

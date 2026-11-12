"""Demo seed / reset.

    docker compose exec api python -m app.demo_seed --reset            # wipe all data, reseed Asha/Ben/Chloe + Flatmates
    docker compose exec api python -m app.demo_seed --reset --topup 7000  # mock mode only: each member tops up $70 (MOCK PAYPAL)
"""
import argparse

from sqlalchemy import select, text

from . import db as dbmod
from .config import get_settings
from .db import Base
from .models import Group
from .seed import run_seed


def reset() -> None:
    dbmod.create_all()
    eng = dbmod.engine()
    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with eng.begin() as c:
        if eng.dialect.name == "postgresql":
            c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
        else:
            for t in reversed(Base.metadata.sorted_tables):
                c.execute(t.delete())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--topup", type=int, default=0, help="cents each member tops up (mock mode only)")
    a = ap.parse_args()
    if a.reset:
        reset()
        print("All data wiped.")
    db = dbmod.SessionLocal()
    try:
        run_seed(db)
        g = db.scalar(select(Group).where(Group.name == "Flatmates"))
        print(f"Seeded group #{g.id} 'Flatmates' (Asha owner, Ben, Chloe; StreamFlix $15.00).")
        if a.topup:
            if get_settings().paypal_real:
                print("Not topping up: PAYMENT_PROVIDER=paypal. Use 'Top up the pot' in the UI so it goes through PayPal Sandbox.")
            else:
                from .services.groups import active_members
                from .services import payments as ps

                for m in active_members(db, g.id):
                    att = ps.start_approval_payment(db, membership=m, cycle=None, kind="TOP_UP", amount_minor=a.topup)
                    ps.complete_approval(db, att.paypal_order_id)
                db.commit()
                print(f"Each member topped up {a.topup} cents (labelled MOCK PAYPAL).")
    finally:
        db.close()


if __name__ == "__main__":
    main()

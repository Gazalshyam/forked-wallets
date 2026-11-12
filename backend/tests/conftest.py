"""Tests run against SQLite by default (no services needed), or Postgres via TEST_DATABASE_URL."""
import os
import tempfile

import pytest

os.environ.setdefault("PAYMENT_PROVIDER", "mock")
os.environ.setdefault("LLM_PROVIDER", "mock")

from app import db as dbmod  # noqa: E402
from app.db import Base  # noqa: E402
from app.services import race  # noqa: E402


@pytest.fixture()
def db():
    url = os.getenv("TEST_DATABASE_URL")
    tmp = None
    if not url:
        fd, tmp = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        url = f"sqlite:///{tmp}"
    engine = dbmod.configure(url)
    from app import models  # noqa: F401

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    race.DELAY_RANGE = (0.0, 0.02)
    from app.services.demo import seed_demo_group

    s = dbmod.SessionLocal()
    seed_demo_group(s)
    s.commit()
    yield s
    s.close()
    Base.metadata.drop_all(engine)
    engine.dispose()
    if tmp:
        os.unlink(tmp)

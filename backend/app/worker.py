"""Job worker: polls scheduled_jobs with FOR UPDATE SKIP LOCKED and runs handlers.

Run with:  python -m app.worker
"""
import logging
import time
import traceback
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from . import db as dbmod
from .config import get_settings
from .models import ScheduledJob, utcnow

log = logging.getLogger("worker")


def _handlers():
    import importlib

    handlers = {}
    for name in ("cycles", "race", "balances"):
        try:
            mod = importlib.import_module(f".services.{name}", __package__)
        except ModuleNotFoundError as e:
            if e.name != f"{__package__}.services.{name}":
                raise
            continue
        handlers.update(mod.JOB_HANDLERS)
    return handlers


def claim_next_job():
    """Atomically claim one due job. Returns job id or None."""
    db = dbmod.SessionLocal()
    try:
        q = (
            select(ScheduledJob)
            .where(ScheduledJob.status == "PENDING", ScheduledJob.run_at <= datetime.now(timezone.utc))
            .order_by(ScheduledJob.run_at, ScheduledJob.id)
            .limit(1)
        )
        if db.bind.dialect.name == "postgresql":
            q = q.with_for_update(skip_locked=True)
        job = db.scalar(q)
        if job is None:
            db.rollback()
            return None
        job.status = "RUNNING"
        job.attempts += 1
        db.commit()
        return job.id
    finally:
        db.close()


def run_job(job_id: int) -> None:
    db = dbmod.SessionLocal()
    try:
        handlers = _handlers()
        job = db.get(ScheduledJob, job_id)
        handler = handlers.get(job.job_type)
        if handler is None:
            raise RuntimeError(f"No handler for job type {job.job_type}")
        log.info("Running job %s %s %s", job.id, job.job_type, job.payload)
        handler(db, job.payload or {})
        job = db.get(ScheduledJob, job_id)
        job.status = "DONE"
        job.finished_at = utcnow()
        db.commit()
        log.info("Job %s done", job_id)
    except Exception as e:  # keep worker alive; record error on the job
        db.rollback()
        log.error("Job %s failed: %s\n%s", job_id, e, traceback.format_exc())
        job = db.get(ScheduledJob, job_id)
        if job is not None:
            job.status = "FAILED"
            job.last_error = str(e)[:2000]
            job.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


def run_due_jobs(max_jobs: int = 50) -> int:
    """Run due jobs inline until none are left. Used by tests and the worker loop."""
    n = 0
    while n < max_jobs:
        jid = claim_next_job()
        if jid is None:
            break
        run_job(jid)
        n += 1
    return n


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s worker: %(message)s")
    s = get_settings()
    for i in range(60):
        try:
            with dbmod.engine().connect() as c:
                c.execute(text("SELECT 1"))
            break
        except OperationalError:
            log.info("Waiting for database (%s)...", i)
            time.sleep(1)
    dbmod.create_all()
    # Single worker: anything left RUNNING by a crash goes back to the queue (handlers are idempotent).
    with dbmod.session_scope() as db:
        for j in db.scalars(select(ScheduledJob).where(ScheduledJob.status == "RUNNING")):
            j.status = "PENDING"
    log.info("Worker started; polling every %ss", s.worker_poll_seconds)
    while True:
        try:
            if run_due_jobs() == 0:
                time.sleep(s.worker_poll_seconds)
        except Exception as e:  # pragma: no cover
            log.error("Worker loop error: %s", e)
            time.sleep(3)


if __name__ == "__main__":
    main()

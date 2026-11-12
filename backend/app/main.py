import logging
import os
import time

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from . import db as dbmod
from .config import get_settings
from .routers import auth

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("forked_wallets")

app = FastAPI(title="Forked Wallets", version="0.1.0")
app.include_router(auth.router)

import importlib

for _name in ("groups", "payments", "cycles", "tasks", "inbox"):
    try:
        app.include_router(importlib.import_module(f".routers.{_name}", __package__).router)
    except ModuleNotFoundError as e:  # routers arrive in later checkpoints
        if e.name != f"{__package__}.routers.{_name}":
            raise
        log.info("Router %s not present yet", _name)


@app.on_event("startup")
def startup() -> None:
    for i in range(30):
        try:
            with dbmod.engine().connect() as c:
                c.execute(text("SELECT 1"))
            break
        except OperationalError:
            log.info("Waiting for database (%s)...", i)
            time.sleep(1)
    dbmod.create_all()
    from .seed import run_seed

    db = dbmod.SessionLocal()
    try:
        run_seed(db)
    finally:
        db.close()
    s = get_settings()
    log.info("Startup done. payment_provider=%s llm_provider=%s demo_mode=%s", s.payment_provider, s.llm_provider, s.demo_mode)


@app.get("/api/v1/health")
def health():
    with dbmod.engine().connect() as c:
        c.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.exception_handler(ValueError)
def value_error_handler(_request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


from .services.groups import Forbidden  # noqa: E402


@app.exception_handler(Forbidden)
def forbidden_handler(_request, exc: Forbidden):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


# ---- Static frontend (built by Vite into ./static) ----
_static = get_settings().static_dir
if os.path.isdir(os.path.join(_static, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(_static, "assets")), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    candidate = os.path.join(_static, full_path)
    if full_path and os.path.isfile(candidate) and os.path.realpath(candidate).startswith(os.path.realpath(_static)):
        return FileResponse(candidate)
    index = os.path.join(_static, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)
    return JSONResponse({"detail": "Frontend not built. Run `docker compose up --build`."}, status_code=503)

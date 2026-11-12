from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import COOKIE_NAME, SESSION_TTL, current_user, hash_password, make_session_value, verify_password
from ..seed import DEMO_USERS

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class RegisterIn(LoginIn):
    name: str = Field(min_length=1, max_length=100)


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name, "paypal_email": u.paypal_email}


def _set_cookie(resp: Response, user_id: int) -> None:
    resp.set_cookie(
        COOKIE_NAME,
        make_session_value(user_id),
        max_age=SESSION_TTL,
        httponly=True,
        samesite="lax",
        secure=get_settings().app_base_url.startswith("https"),
        path="/",
    )


@router.post("/login")
def login(body: LoginIn, response: Response, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if u is None or not verify_password(body.password, u.password_hash):
        raise HTTPException(401, "Wrong email or password")
    _set_cookie(response, u.id)
    return user_out(u)


@router.post("/register")
def register(body: RegisterIn, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "Email already registered")
    if len(body.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")
    u = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(u)
    db.commit()
    _set_cookie(response, u.id)
    return user_out(u)


@router.post("/demo-login/{name}")
def demo_login(name: str, response: Response, db: Session = Depends(get_db)):
    if not get_settings().demo_mode:
        raise HTTPException(404, "Demo mode is off")
    email = next((e for e, n in DEMO_USERS if n.lower() == name.lower()), None)
    u = db.scalar(select(User).where(User.email == email)) if email else None
    if u is None:
        raise HTTPException(404, "Unknown demo user")
    _set_cookie(response, u.id)
    return user_out(u)


@router.get("/demo-login/{name}")
def demo_login_redirect(name: str, next: str = "/", db: Session = Depends(get_db)):
    """Demo convenience: /api/v1/auth/demo-login/ben?next=/groups/1 switches user in one click."""
    from fastapi.responses import RedirectResponse

    target = next if next.startswith("/") and not next.startswith("//") else "/"
    resp = RedirectResponse(target, status_code=303)
    demo_login(name, resp, db)
    return resp


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_out(user)


@router.get("/config")
def public_config():
    s = get_settings()
    return {
        "demo_mode": s.demo_mode,
        "payment_provider": s.payment_provider,
        "payment_label": "PAYPAL SANDBOX" if s.paypal_real else "MOCK PAYPAL",
        "vault_enabled": s.paypal_vault_enabled,
        "payouts_enabled": s.paypal_payouts_enabled,
        "llm_provider": s.llm_provider,
    }

"""Password hashing (stdlib scrypt), signed session cookie, vault token encryption."""
import base64
import hashlib
import hmac
import os
import time

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User

COOKIE_NAME = "fw_session"
SESSION_TTL = 7 * 24 * 3600

_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, n, r, p, salt_hex, dk_hex = stored.split("$")
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(dk.hex(), dk_hex)
    except Exception:
        return False


def _sign(payload: str) -> str:
    key = get_settings().session_secret.encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def make_session_value(user_id: int) -> str:
    payload = f"{user_id}.{int(time.time()) + SESSION_TTL}"
    return f"{payload}.{_sign(payload)}"


def read_session_value(value: str | None) -> int | None:
    if not value:
        return None
    try:
        uid, exp, sig = value.split(".")
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(f"{uid}.{exp}"), sig):
        return None
    if int(exp) < time.time():
        return None
    return int(uid)


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = read_session_value(request.cookies.get(COOKIE_NAME))
    if uid is None:
        raise HTTPException(401, "Not signed in")
    user = db.get(User, uid)
    if user is None:
        raise HTTPException(401, "Not signed in")
    return user


def _fernet() -> Fernet:
    s = get_settings()
    material = s.token_encryption_key or ("derived-from-session-secret:" + s.session_secret)
    key = base64.urlsafe_b64encode(hashlib.sha256(material.encode()).digest())
    return Fernet(key)


def encrypt_token(token: str) -> str:
    return _fernet().encrypt(token.encode()).decode()


def decrypt_token(blob: str) -> str:
    return _fernet().decrypt(blob.encode()).decode()

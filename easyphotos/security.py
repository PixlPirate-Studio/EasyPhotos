"""Passwords, sessions and permission checks."""
import base64
import hashlib
import hmac
import secrets
import threading
import time

from fastapi import HTTPException, Request

import logging

from . import db

audit_log = logging.getLogger("easyphotos.audit")


def audit(request: Request | None, who, message: str, *args, level: int = logging.INFO) -> None:
    """One line in the container log for sign-ins and admin changes."""
    name = who["username"] if who is not None and not isinstance(who, str) else (who or "-")
    ip = _client_ip(request) if request is not None else "-"
    audit_log.log(level, "%-5s %s (%s): " + message, "USER", name, ip, *args)

SESSION_COOKIE = "ep_session"
SESSION_DAYS_REMEMBER = 30
SESSION_HOURS_SHORT = 12

ROLES = ("viewer", "user", "admin")


# ---------------------------------------------------------------- passwords

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_b64, dk_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=len(expected))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def check_password_rules(password: str) -> None:
    if len(password or "") < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if len(password) > 200:
        raise HTTPException(400, "Password is too long.")


# ---------------------------------------------------------------- rate limit

_fail_lock = threading.Lock()
_failures: dict[str, list[float]] = {}
MAX_FAILS = 10
FAIL_WINDOW = 15 * 60


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def check_rate(request: Request, username: str) -> None:
    key = f"{_client_ip(request)}|{(username or '').lower()}"
    now = time.time()
    with _fail_lock:
        lst = [t for t in _failures.get(key, []) if now - t < FAIL_WINDOW]
        _failures[key] = lst
        if len(lst) >= MAX_FAILS:
            raise HTTPException(429, "Too many attempts. Wait 15 minutes and try again.")


def note_failure(request: Request, username: str) -> None:
    key = f"{_client_ip(request)}|{(username or '').lower()}"
    with _fail_lock:
        _failures.setdefault(key, []).append(time.time())


def clear_failures(request: Request, username: str) -> None:
    key = f"{_client_ip(request)}|{(username or '').lower()}"
    with _fail_lock:
        _failures.pop(key, None)


# ---------------------------------------------------------------- sessions

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int, remember: bool, user_agent: str = "") -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    now = time.time()
    max_age = SESSION_DAYS_REMEMBER * 86400 if remember else SESSION_HOURS_SHORT * 3600
    db.ex("INSERT INTO sessions(token_hash, user_id, created_at, expires_at, user_agent) VALUES (?,?,?,?,?)",
          (_token_hash(token), user_id, now, now + max_age, (user_agent or "")[:200]))
    db.ex("UPDATE users SET last_login=? WHERE id=?", (now, user_id))
    db.ex("DELETE FROM sessions WHERE expires_at < ?", (now,))
    return token, max_age


def set_session_cookie(response, request: Request, token: str, max_age: int, remember: bool) -> None:
    secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=max_age if remember else None,
        httponly=True, samesite="lax", secure=secure, path="/",
    )


def destroy_session(token: str) -> None:
    if token:
        db.ex("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))


def user_from_request(request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = db.q1(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash=? AND s.expires_at > ?", (_token_hash(token), time.time()))
    if row is None or not row["password_hash"]:
        return None
    return row


# ---------------------------------------------------------------- dependencies

class User(dict):
    @property
    def id(self) -> int:
        return self["id"]

    @property
    def role(self) -> str:
        return self["role"]

    @property
    def is_admin(self) -> bool:
        return self["role"] == "admin"

    @property
    def can_download(self) -> bool:
        return self["role"] in ("user", "admin")


def current_user(request: Request) -> User:
    row = user_from_request(request)
    if row is None:
        raise HTTPException(401, "Please sign in.")
    # Mutating requests must come from our own page (basic CSRF protection).
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get("x-requested-with") != "EasyPhotos":
            raise HTTPException(403, "Bad request origin.")
    return User(dict(row))


def admin_user(request: Request) -> User:
    user = current_user(request)
    if not user.is_admin:
        raise HTTPException(403, "Only an admin can do that.")
    return user


def download_user(request: Request) -> User:
    user = current_user(request)
    if not user.can_download:
        raise HTTPException(403, "Your account can view photos but not download them.")
    return user


def allowed_library_ids(user: User):
    """None means every library."""
    if user.is_admin or user.get("all_libraries", 1):
        return None
    return [r["library_id"] for r in db.q("SELECT library_id FROM user_libraries WHERE user_id=?", (user.id,))]


def user_public(user) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "can_download": user["role"] in ("user", "admin"),
        "is_admin": user["role"] == "admin",
    }

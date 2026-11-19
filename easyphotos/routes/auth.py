"""Sign in, first-run setup and passwords.

There is no sign-up. The admin creates users with just a username and role;
the person picks their own password the first time they sign in.
"""
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import db
from ..security import (SESSION_COOKIE, User, check_password_rules, check_rate, clear_failures, create_session,
                        current_user, destroy_session, hash_password, note_failure, set_session_cookie,
                        user_from_request, user_public, verify_password)

router = APIRouter(prefix="/api/auth")


def _require_page_header(request: Request):
    if request.headers.get("x-requested-with") != "EasyPhotos":
        raise HTTPException(403, "Bad request origin.")


@router.get("/state")
def state(request: Request):
    setup_needed = db.val("SELECT COUNT(*) FROM users", default=0) == 0
    user = user_from_request(request)
    return {"setup_needed": setup_needed, "user": user_public(user) if user else None}


@router.post("/setup")
def setup(body: dict, request: Request, response: Response):
    """Creates the first admin account. Only works while there are no users."""
    _require_page_header(request)
    if db.val("SELECT COUNT(*) FROM users", default=0) > 0:
        raise HTTPException(400, "Setup is already done.")
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    _check_username(username)
    check_password_rules(password)
    cur = db.ex("INSERT INTO users(username, role, password_hash, created_at) VALUES (?, 'admin', ?, ?)",
                (username, hash_password(password), time.time()))
    token, max_age = create_session(cur.lastrowid, True, request.headers.get("user-agent", ""))
    set_session_cookie(response, request, token, max_age, True)
    return {"user": user_public(db.q1("SELECT * FROM users WHERE id=?", (cur.lastrowid,)))}


def _check_username(username: str):
    if not (2 <= len(username) <= 40) or not all(c.isalnum() or c in "._-" for c in username):
        raise HTTPException(400, "Usernames can use letters, numbers, dots, dashes and underscores (2-40 characters).")


@router.post("/login")
def login(body: dict, request: Request, response: Response):
    _require_page_header(request)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    remember = bool(body.get("remember", True))
    check_rate(request, username)
    user = db.q1("SELECT * FROM users WHERE username=?", (username,))
    if user is not None and not user["password_hash"]:
        # New account (or password was reset): the person picks a password now.
        return {"status": "set_password", "username": user["username"]}
    if not password:
        # Ask for the password without revealing whether the username exists.
        return {"status": "need_password"}
    if user is None or not verify_password(password, user["password_hash"]):
        note_failure(request, username)
        raise HTTPException(401, "Wrong username or password.")
    clear_failures(request, username)
    token, max_age = create_session(user["id"], remember, request.headers.get("user-agent", ""))
    set_session_cookie(response, request, token, max_age, remember)
    return {"status": "ok", "user": user_public(user)}


@router.post("/set-password")
def set_first_password(body: dict, request: Request, response: Response):
    """First sign-in for a new account: the person chooses their password."""
    _require_page_header(request)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    check_rate(request, username)
    user = db.q1("SELECT * FROM users WHERE username=?", (username,))
    if user is None or user["password_hash"]:
        note_failure(request, username)
        raise HTTPException(400, "This account already has a password.")
    check_password_rules(password)
    db.ex("UPDATE users SET password_hash=? WHERE id=? AND password_hash IS NULL", (hash_password(password), user["id"]))
    remember = bool(body.get("remember", True))
    token, max_age = create_session(user["id"], remember, request.headers.get("user-agent", ""))
    set_session_cookie(response, request, token, max_age, remember)
    return {"status": "ok", "user": user_public(user)}


@router.post("/logout")
def logout(request: Request, response: Response):
    destroy_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.post("/change-password")
def change_password(body: dict, user: User = Depends(current_user)):
    row = db.q1("SELECT password_hash FROM users WHERE id=?", (user.id,))
    if not verify_password(body.get("current") or "", row["password_hash"] or ""):
        raise HTTPException(400, "Your current password is not right.")
    check_password_rules(body.get("new") or "")
    db.ex("UPDATE users SET password_hash=? WHERE id=?", (hash_password(body["new"]), user.id))
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return user_public(user)

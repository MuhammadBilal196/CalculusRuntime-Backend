"""Auth routes — signup, login, me (email verification commented out until deployment)."""

import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from urllib.parse import quote

from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse

from core import email_sender, storage
from core.auth_utils import (
    SECRET_KEY,
    hash_password,
    verify_password,
    create_token,
    require_user,
    err,
)

log = logging.getLogger(__name__)

CODE_TTL_MINUTES = 15
RESEND_COOLDOWN_SECONDS = 60
MAX_CODE_ATTEMPTS = 5

MAX_FULL_NAME = 80
MAX_EMAIL = 254


def clean_full_name(value) -> str:
    """Collapse whitespace; certificates print this name."""
    return " ".join(str(value or "").split())[:MAX_FULL_NAME]


def clean_email(value) -> str:
    return str(value or "").strip().lower()[:MAX_EMAIL]


def valid_email(email: str) -> bool:
    if not email:
        return True
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and " " not in email


def public_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "fullName": (row.get("full_name") or "").strip() or None,
        "email": row.get("email"),
        # Email verification fields commented out until deployment:
        # "emailVerified": bool(row.get("email_verified")),
        # "verificationRequired": bool(row.get("verification_required")),
    }


# ── Email verification helpers ────────────────────────────────────────────────


def _digest(kind: str, value: str) -> str:
    """Keyed hash, so a leaked table can't be used to verify accounts."""
    return hmac.new(SECRET_KEY.encode(), f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


_DEFAULT_SITE = "https://calculus.quantumlogicslimited.com"
_SITE_ORIGIN = re.compile(
    r"^https?://("
    r"localhost(:\d+)?|127\.0\.0\.1(:\d+)?"
    r"|([a-z0-9-]+\.)*quantumlogicslimited\.com|([a-z0-9-]+\.)*quantumlogiclimited\.com"
    r"|calculus-runtime-frontend[a-z0-9-]*\.vercel\.app"
    r")$",
    re.IGNORECASE,
)


def _site_url(request: Request) -> str:
    """Frontend address for the link in the email (never an arbitrary origin)."""
    configured = os.getenv("FRONTEND_URL", "").strip().rstrip("/")
    if configured:
        return configured
    origin = (request.headers.get("origin") or "").strip().rstrip("/")
    extra = {o.strip().rstrip("/") for o in os.getenv("ALLOWED_ORIGINS", "").split(",") if o.strip()}
    if origin and (_SITE_ORIGIN.match(origin) or origin in extra):
        return origin
    return _DEFAULT_SITE


async def issue_verification(request: Request, user: dict, force: bool = False) -> dict:
    """Create a new code + link for the user's current email and send it."""
    email = user.get("email")
    if not email:
        return {"sent": False, "error": "Add an email address first."}

    now = int(time.time())
    pending = await storage.get_email_verification(user["id"])
    if (
        not force
        and pending
        and pending.get("email") == email
        and now - int(pending.get("sent_at") or 0) < RESEND_COOLDOWN_SECONDS
    ):
        wait = RESEND_COOLDOWN_SECONDS - (now - int(pending["sent_at"]))
        return {"sent": False, "error": f"Please wait {wait}s before asking for another code.", "retryAfter": wait}

    configured = email_sender.is_configured()

    code = f"{secrets.randbelow(1_000_000):06d}"
    token = secrets.token_urlsafe(32)
    await storage.save_email_verification(
        user["id"],
        email,
        _digest("code", f"{user['id']}:{code}"),
        _digest("link", token),
        now + CODE_TTL_MINUTES * 60,
        now,
    )
    link = f"{_site_url(request)}/verify-email?token={quote(token)}"
    result = {"sent": True, "email": email, "expiresInMinutes": CODE_TTL_MINUTES, "retryAfter": RESEND_COOLDOWN_SECONDS}

    if configured and not email_sender.dev_mode():
        subject, text, body = email_sender.verification_email(
            code, link, CODE_TTL_MINUTES, (user.get("full_name") or "").split(" ")[0]
        )
        try:
            await email_sender.send_email(email, subject, text, body)
            return result
        except Exception as exc:
            log.warning("Verification email to %s failed (falling back to instant code): %s", email, exc)

    print(f"[CalcVoyager] Verification for {email}: code {code} · link {link}", flush=True)
    return {**result, "devCode": code, "devLink": link}


def storage_err(exc: Exception):
    message = str(exc)
    if "public.users" in message and "schema cache" in message:
        return err(
            500,
            "Supabase table public.users is missing. Run backend/supabase_schema.sql in the Supabase SQL Editor, then restart the backend.",
        )
    if "row-level security" in message or "42501" in message:
        return err(
            500,
            "Supabase RLS is blocking this request. Re-run backend/supabase_schema.sql in the Supabase SQL Editor, or use a backend-only Supabase service role/secret key.",
        )
    return err(500, f"Database error: {message}")


async def signup(request: Request):
    try:
        body = await request.json()
    except Exception:
        return err(400, "Invalid JSON.")

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    email = clean_email(body.get("email")) or None
    full_name = clean_full_name(body.get("full_name") or body.get("fullName")) or None

    if len(username) < 3:
        return err(400, "Username must be at least 3 characters.")
    if len(password) < 6:
        return err(400, "Password must be at least 6 characters.")
    # Email requirement commented out until deployment:
    # if not email:
    #     return err(400, "Enter your email address — we'll send a code to verify it.")
    if email and not valid_email(email):
        return err(400, "Enter a valid email address.")

    try:
        existing = await storage.get_user_by_username(username)
    except Exception as exc:
        return storage_err(exc)
    if existing:
        return err(409, "Username already taken.")
    if email:
        try:
            if await storage.get_user_by_email(email):
                return err(409, "An account with this email already exists.")
        except Exception as exc:
            return storage_err(exc)

    hashed = hash_password(password)
    try:
        # verification_required=False while email verification is commented out:
        user_id = await storage.create_user(
            username, email, hashed, full_name, verification_required=False
        )
    except Exception as exc:
        return storage_err(exc)
    token = create_token(user_id)

    # Email verification on signup is commented out until deployment:
    # try:
    #     verification = await issue_verification(
    #         request, {"id": user_id, "email": email, "full_name": full_name}, force=True
    #     )
    # except Exception as exc:
    #     log.warning("Could not start email verification for %s: %s", username, exc)
    #     verification = {"sent": False, "error": "We couldn't send the code yet. Use “Resend code”."}

    return JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": {
                "id": user_id,
                "username": username,
                "fullName": full_name,
                "email": email,
                # "emailVerified": False,
                # "verificationRequired": True,
            },
            # "verification": verification,
        },
        status_code=201,
    )


async def login(request: Request):
    try:
        body = await request.json()
    except Exception:
        return err(400, "Invalid JSON.")

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    try:
        row = await storage.get_user_by_username(username)
    except Exception as exc:
        return storage_err(exc)
    if not row or not verify_password(password, row["hashed_pw"]):
        return err(401, "Incorrect username or password.")

    token = create_token(row["id"])
    return JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": public_user(row),
        }
    )


async def me(request: Request):
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")
    try:
        row = await storage.get_user_profile(user_id)
    except Exception as exc:
        return storage_err(exc)
    if not row:
        return err(401, "Session expired. Please sign in again.")
    return JSONResponse(
        {
            **row,
            "fullName": (row.get("full_name") or "").strip() or None,
            # "emailVerified": bool(row.get("email_verified")),
            # "verificationRequired": bool(row.get("verification_required")),
        }
    )


async def update_me(request: Request):
    """PATCH /api/auth/me — edit full name and email (printed on certificates)."""
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")
    try:
        body = await request.json()
    except Exception:
        return err(400, "Invalid JSON.")

    full_name = None
    if "full_name" in body or "fullName" in body:
        full_name = clean_full_name(body.get("full_name", body.get("fullName")))
        if len(full_name) < 2:
            return err(400, "Full name must be at least 2 characters.")
    email = None
    if "email" in body:
        email = clean_email(body.get("email"))
        if not valid_email(email):
            return err(400, "Enter a valid email address.")
        if email:
            try:
                other = await storage.get_user_by_email(email)
            except Exception as exc:
                return storage_err(exc)
            if other and int(other["id"]) != int(user_id):
                return err(409, "An account with this email already exists.")

    try:
        row = await storage.update_user_profile(user_id, full_name, email)
    except Exception as exc:
        return storage_err(exc)
    if not row:
        return err(401, "Session expired. Please sign in again.")
    return JSONResponse({"user": public_user(row)})


async def send_verification(request: Request):
    """POST /api/auth/verify-email/send — body {email?}. Sends a code + link."""
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        row = await storage.get_user_profile(user_id)
        if not row:
            return err(401, "Session expired. Please sign in again.")

        new_email = clean_email((body or {}).get("email"))
        if new_email and new_email != (row.get("email") or ""):
            if not valid_email(new_email):
                return err(400, "Enter a valid email address.")
            other = await storage.get_user_by_email(new_email)
            if other and int(other["id"]) != int(user_id):
                return err(409, "An account with this email already exists.")
            row = await storage.update_user_profile(user_id, None, new_email)
            await storage.set_email_verified(user_id, False)
            row = await storage.get_user_profile(user_id)

        if not row.get("email"):
            return err(400, "Add an email address first.")
        if row.get("email_verified"):
            return JSONResponse({"sent": False, "alreadyVerified": True, "user": public_user(row)})

        result = await issue_verification(request, row)
    except Exception as exc:
        return storage_err(exc)

    if not result.get("sent"):
        status = 429 if result.get("retryAfter") else 502
        return JSONResponse({"detail": result.get("error"), **result, "user": public_user(row)}, status_code=status)
    return JSONResponse({**result, "user": public_user(row)})


async def _mark_verified(user_id: int) -> dict:
    await storage.set_email_verified(user_id, True)
    await storage.delete_email_verification(user_id)
    return await storage.get_user_profile(user_id)


async def confirm_verification(request: Request):
    """POST /api/auth/verify-email/confirm — body {code}."""
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")
    try:
        body = await request.json()
    except Exception:
        return err(400, "Invalid JSON.")
    code = re.sub(r"\D", "", str(body.get("code") or ""))
    if len(code) != 6:
        return err(400, "Enter the 6-digit code from the email.")

    try:
        row = await storage.get_user_profile(user_id)
        if not row:
            return err(401, "Session expired. Please sign in again.")
        if row.get("email_verified"):
            return JSONResponse({"verified": True, "user": public_user(row)})

        pending = await storage.get_email_verification(user_id)
        if not pending or pending.get("email") != row.get("email"):
            return err(400, "No active code. Ask for a new one.")
        if int(pending.get("expires_at") or 0) < int(time.time()):
            return err(400, "This code has expired. Ask for a new one.")
        attempts = int(pending.get("attempts") or 0)
        if attempts >= MAX_CODE_ATTEMPTS:
            return err(429, "Too many wrong tries. Ask for a new code.")
        if not hmac.compare_digest(pending["code_hash"], _digest("code", f"{user_id}:{code}")):
            await storage.count_verification_attempt(user_id, attempts + 1)
            left = MAX_CODE_ATTEMPTS - attempts - 1
            return err(400, f"That code isn't right. {left} tr{'y' if left == 1 else 'ies'} left.")

        row = await _mark_verified(user_id)
    except Exception as exc:
        return storage_err(exc)
    return JSONResponse({"verified": True, "user": public_user(row)})


async def verify_link(request: Request):
    """POST /api/auth/verify-email/link — body {token}. Works without signing in."""
    try:
        body = await request.json()
    except Exception:
        return err(400, "Invalid JSON.")
    token = str(body.get("token") or "").strip()
    if not token or len(token) > 200:
        return err(400, "This verification link is not valid.")

    try:
        pending = await storage.get_email_verification_by_token(_digest("link", token))
        if not pending:
            return err(400, "This link was already used or has been replaced by a newer one.")
        if int(pending.get("expires_at") or 0) < int(time.time()):
            return err(400, "This link has expired. Sign in and ask for a new code.")
        user_id = int(pending["user_id"])
        row = await storage.get_user_profile(user_id)
        if not row or row.get("email") != pending.get("email"):
            return err(400, "This link is for an older email address.")
        row = await _mark_verified(user_id)
    except Exception as exc:
        return storage_err(exc)

    caller = require_user(request)
    payload = {"verified": True}
    if caller and int(caller) == user_id:
        payload["user"] = public_user(row)
    return JSONResponse(payload)


routes = [
    Route("/signup", signup, methods=["POST"]),
    Route("/login", login, methods=["POST"]),
    Route("/me", me, methods=["GET"]),
    Route("/me", update_me, methods=["PATCH"]),
    # Verification routes commented out until backend deployment:
    # Route("/verify-email/send", send_verification, methods=["POST"]),
    # Route("/verify-email/confirm", confirm_verification, methods=["POST"]),
    # Route("/verify-email/link", verify_link, methods=["POST"]),
]

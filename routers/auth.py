"""Auth routes — signup, login, me."""

from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse

from core import storage
from core.auth_utils import hash_password, verify_password, create_token, require_user, err

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
    }


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
        user_id = await storage.create_user(username, email, hashed, full_name)
    except Exception as exc:
        return storage_err(exc)
    token = create_token(user_id)
    return JSONResponse(
        {
            "access_token": token,
            "token_type": "bearer",
            "user": {"id": user_id, "username": username, "fullName": full_name, "email": email},
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
    return JSONResponse({**row, "fullName": (row.get("full_name") or "").strip() or None})


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


routes = [
    Route("/signup", signup, methods=["POST"]),
    Route("/login", login, methods=["POST"]),
    Route("/me", me, methods=["GET"]),
    Route("/me", update_me, methods=["PATCH"]),
]

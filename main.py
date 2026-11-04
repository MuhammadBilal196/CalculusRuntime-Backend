"""
CalcVoyager Backend — Starlette + SQLite/Supabase
Works on Python 3.10+ including 3.14.
No pydantic, no aiosqlite, no SQLAlchemy.
"""

import logging
import os
import sys
import warnings
from contextlib import asynccontextmanager
from pathlib import Path

# Suppress harmless namespace warning if fpdf2 is imported in env with legacy packages
warnings.filterwarnings("ignore", category=UserWarning, module="fpdf")

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.routing import Route, Mount
from starlette.requests import Request
from starlette.responses import JSONResponse, HTMLResponse

# ── Load .env manually (no python-dotenv needed) ──────────────────────────────
_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    for _line in _env_file.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

from core.storage import init_storage
from routers.auth import routes as auth_routes
from routers.progress import routes as progress_routes
from routers.bookmarks import routes as bookmark_routes
from routers.quiz import routes as quiz_routes
from routers.solver_proxy import routes as solver_routes
from routers.certificates import routes as certificate_routes, list_my_certificates
from routers.verification import routes as verification_routes

# Calculus AI Chatbot routes — serves /api/chat/* (the site's "Ask tutor").
# The chatbot is a separate repo. By default it is expected next to this one
# (../Calculus-AI-Chatbot); set CHATBOT_DIR to load it from somewhere else,
# e.g. a submodule or a folder copied into the deploy image.
_CHATBOT_ROOT = Path(
    os.getenv("CHATBOT_DIR")
    or Path(__file__).resolve().parent.parent / "Calculus-AI-Chatbot"
).resolve()
if _CHATBOT_ROOT.is_dir() and str(_CHATBOT_ROOT) not in sys.path:
    sys.path.insert(0, str(_CHATBOT_ROOT))
try:
    from backend.app.routes.chat import routes as chat_routes  # noqa: E402
    from backend.app.database.db import init_db as init_chat_db  # noqa: E402
except ImportError as exc:
    # Without this warning a missing chatbot only shows up as a 404 on
    # /api/chat and the website's tutor silently stops answering.
    logging.getLogger(__name__).warning(
        "Ask Tutor disabled: could not load the chatbot from %s (%s). "
        "Set CHATBOT_DIR to the Calculus-AI-Chatbot folder.",
        _CHATBOT_ROOT,
        exc,
    )
    chat_routes = []
    init_chat_db = None

# ── Route handlers ────────────────────────────────────────────────────────────


async def root(request: Request):
    return JSONResponse(
        {
            "name": "CalcVoyager API",
            "version": "1.0.0",
            "status": "ok",
            "docs": "/docs",
            "health": "/api/health",
            "endpoints": {
                "auth": "/api/auth  (POST /signup  POST /login  GET /me  PATCH /me  POST /verify-email/send|confirm|link)",
                "progress": "/api/progress  (GET /  POST /section/complete  DELETE /section/{id})",
                "bookmarks": "/api/bookmarks  (GET /  POST /  DELETE /{id})",
                "quiz": "/api/quiz  (GET /  POST /)",
                "solver": "/api/solver  (POST /log  GET /history)",
                "certificates": "/api/certificates  (POST /generate  GET /verify?token=)",
                "verify_course": "/api/verify-course  (POST /  — body: {userProgress, courseData})",
            },
        }
    )


async def health(request: Request):
    return JSONResponse({"status": "ok"})


async def favicon(request: Request):
    # Return a minimal 1×1 transparent ICO so browsers stop logging 404s
    return HTMLResponse("", status_code=204)


_DOCS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>CalcVoyager API — Docs</title>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  body{font-family:system-ui,sans-serif;background:#0f1117;color:#e8e6e1;padding:2rem 1rem}
  h1{font-size:1.6rem;font-weight:800;color:#f0c66f;margin-bottom:.3rem}
  .sub{color:#9a917f;font-size:.9rem;margin-bottom:2rem}
  .group{margin-bottom:2rem}
  .group-title{font-size:.75rem;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
    color:#9a917f;margin-bottom:.6rem;padding-bottom:.4rem;border-bottom:1px solid #2a2620}
  .route{display:flex;align-items:flex-start;gap:.75rem;padding:.55rem .75rem;
    border-radius:6px;margin-bottom:.3rem;background:#1a1710}
  .method{font-size:.72rem;font-weight:700;padding:.2rem .5rem;border-radius:4px;
    min-width:52px;text-align:center;flex-shrink:0;margin-top:.1rem}
  .get {background:#1a3a2a;color:#4ade80}
  .post{background:#1a2a3a;color:#60a5fa}
  .del {background:#3a1a1a;color:#f87171}
  .path{font-family:monospace;font-size:.9rem;color:#e8e6e1}
  .desc{font-size:.82rem;color:#9a917f;margin-top:.15rem}
  .auth-note{background:#1e1a11;border:1px solid #3a3020;border-radius:6px;
    padding:.75rem 1rem;font-size:.85rem;color:#c8a84b;margin-bottom:2rem}
  a{color:#f0c66f;text-decoration:none}
  a:hover{text-decoration:underline}
</style>
</head>
<body>
<h1>CalcVoyager API</h1>
<p class="sub">Starlette + SQLite &nbsp;·&nbsp; v1.0.0 &nbsp;·&nbsp;
  <a href="/api/health">health check</a></p>

<div class="auth-note">
  🔒 Protected routes require <code>Authorization: Bearer &lt;token&gt;</code>.
  Get a token from <code>POST /api/auth/signup</code> or <code>POST /api/auth/login</code>.
</div>

<div class="group">
  <div class="group-title">Auth &nbsp;/api/auth</div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/auth/signup</div>
    <div class="desc">Create account. Body: <code>{"username","password","email?"}</code></div></div></div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/auth/login</div>
    <div class="desc">Sign in. Body: <code>{"username","password"}</code> → returns JWT token</div></div></div>
  <div class="route"><span class="method get">GET</span>
    <div><div class="path">/api/auth/me</div>
    <div class="desc">🔒 Current user profile</div></div></div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/auth/verify-email/send</div>
    <div class="desc">🔒 Email a 6-digit code + link. Body: <code>{"email?"}</code> (changes the address first)</div></div></div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/auth/verify-email/confirm</div>
    <div class="desc">🔒 Verify with the code. Body: <code>{"code"}</code></div></div></div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/auth/verify-email/link</div>
    <div class="desc">Verify from the email link. Body: <code>{"token"}</code></div></div></div>
</div>

<div class="group">
  <div class="group-title">Progress &nbsp;/api/progress</div>
  <div class="route"><span class="method get">GET</span>
    <div><div class="path">/api/progress/</div>
    <div class="desc">🔒 Full snapshot: completed sections, quiz scores, bookmarks, solver count</div></div></div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/progress/section/complete</div>
    <div class="desc">🔒 Mark a section complete. Body: <code>{"section_id":"partial-1"}</code></div></div></div>
  <div class="route"><span class="method del">DEL</span>
    <div><div class="path">/api/progress/section/{section_id}</div>
    <div class="desc">🔒 Unmark a section</div></div></div>
</div>

<div class="group">
  <div class="group-title">Bookmarks &nbsp;/api/bookmarks</div>
  <div class="route"><span class="method get">GET</span>
    <div><div class="path">/api/bookmarks/</div>
    <div class="desc">🔒 List all bookmarks</div></div></div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/bookmarks/</div>
    <div class="desc">🔒 Add bookmark. Body: <code>{"id","title","path"}</code></div></div></div>
  <div class="route"><span class="method del">DEL</span>
    <div><div class="path">/api/bookmarks/{id}</div>
    <div class="desc">🔒 Remove bookmark</div></div></div>
</div>

<div class="group">
  <div class="group-title">Quiz Scores &nbsp;/api/quiz</div>
  <div class="route"><span class="method get">GET</span>
    <div><div class="path">/api/quiz/</div>
    <div class="desc">🔒 All quiz scores for current user</div></div></div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/quiz/</div>
    <div class="desc">🔒 Save score. Body: <code>{"quiz_id","score","total"}</code></div></div></div>
</div>

<div class="group">
  <div class="group-title">Solver &nbsp;/api/solver</div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/solver/log</div>
    <div class="desc">Log a solver use (optional auth). Body: <code>{"expression?","result?"}</code></div></div></div>
  <div class="route"><span class="method get">GET</span>
    <div><div class="path">/api/solver/history</div>
    <div class="desc">🔒 Last 50 solver uses for current user</div></div></div>
</div>

<div class="group">
  <div class="group-title">Certificates &nbsp;/api/certificates</div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/certificates/generate</div>
    <div class="desc">🔒 Issue a signed certificate + QR code. Body: <code>{"course_id","course_title","username?"}</code></div></div></div>
  <div class="route"><span class="method get">GET</span>
    <div><div class="path">/api/certificates/verify?token=</div>
    <div class="desc">Public — verify a scanned certificate token</div></div></div>
</div>

<div class="group">
  <div class="group-title">Course Verification &nbsp;/api/verify-course</div>
  <div class="route"><span class="method post">POST</span>
    <div><div class="path">/api/verify-course/</div>
    <div class="desc">Checks course completion. Body: <code>{"userProgress","courseData"}</code></div></div></div>
</div>

<div class="group">
  <div class="group-title">System</div>
  <div class="route"><span class="method get">GET</span>
    <div><div class="path">/api/health</div>
    <div class="desc">Health check — always returns <code>{"status":"ok"}</code></div></div></div>
  <div class="route"><span class="method get">GET</span>
    <div><div class="path">/</div>
    <div class="desc">API info + endpoint index (JSON)</div></div></div>
</div>

</body>
</html>"""


async def docs(request: Request):
    return HTMLResponse(_DOCS_HTML)


# ── Startup ───────────────────────────────────────────────────────────────────


async def on_startup():
    await init_storage()
    if init_chat_db is not None:
        await init_chat_db()


@asynccontextmanager
async def lifespan(app):
    await on_startup()
    yield


# ── App ───────────────────────────────────────────────────────────────────────

# Local + production frontend origins. Extra origins via ALLOWED_ORIGINS (comma-separated).
# Defaults are always included so a localhost-only SnapDeploy env cannot wipe production CORS.
_DEFAULT_ORIGINS = (
    "http://localhost:3000,http://127.0.0.1:3000,"
    "http://localhost:3001,http://127.0.0.1:3001,"
    "http://localhost:5173,http://127.0.0.1:5173,"
    "https://calculus-runtime-frontend-ten.vercel.app,"
    "https://calculus-runtime-frontend-lyart.vercel.app,"
    "https://calculus.quantumlogiclimited.com,"
    "https://calculus.quantumlogicslimited.com"
)
_EXTRA_ORIGINS = os.getenv("ALLOWED_ORIGINS", "")
ALLOWED_ORIGINS = list(dict.fromkeys(
    origin.strip()
    for origin in f"{_DEFAULT_ORIGINS},{_EXTRA_ORIGINS}".split(",")
    if origin.strip()
))

_routes = [
    Route("/", root),
    Route("/docs", docs),
    Route("/favicon.ico", favicon),
    Route("/api/health", health),
    Route("/health", health),
    Route("/healthz", health),
    Route("/actuator/health", health),
    Route("/mine", list_my_certificates, methods=["GET"]),
    Mount("/api/auth", routes=auth_routes),
    Mount("/api/progress", routes=progress_routes),
    Mount("/api/bookmarks", routes=bookmark_routes),
    Mount("/api/quiz", routes=quiz_routes),
    Mount("/api/solver", routes=solver_routes),
    Mount("/api/certificates", routes=certificate_routes),
    Mount("/api/verify-course", routes=verification_routes),
]
if chat_routes:
    _routes.append(Mount("/api/chat", routes=chat_routes))

app = Starlette(
    debug=False,
    routes=_routes,
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=ALLOWED_ORIGINS,
            allow_origin_regex=r"^https?://.*",
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    ],
    lifespan=lifespan,
)

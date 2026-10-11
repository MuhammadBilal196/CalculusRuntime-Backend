# Running the Backend

How to start the CalcVoyager API locally, confirm it works, and stop it. For endpoint contracts see `API_REFERENCE.md`; for PR validation and deployment see `DEVELOPMENT_WORKFLOW.md`.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Environment](#environment)
- [Verify It Is Running](#verify-it-is-running)
- [Running With the Frontend](#running-with-the-frontend)
- [Other Ways to Run](#other-ways-to-run)
- [Stopping the Server](#stopping-the-server)
- [Troubleshooting](#troubleshooting)

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.10+ | Tested on 3.14 |
| Working directory `backend/` | `main.py` imports `core` and `routers` as top-level packages, and `DB_PATH` is resolved relative to the current directory |
| `backend/.env` | Optional, but without it JWTs are signed with a public default secret |

## Quick Start

Windows PowerShell:

```powershell
cd backend
python -m venv .venv                 # first time only
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt      # first time, and when requirements.txt changes
Copy-Item .env.example .env          # first time only, then edit it (see Environment)
uvicorn main:app --reload --port 8002
```

macOS / Linux:

```bash
cd backend
python -m venv .venv                 # first time only
source .venv/bin/activate
pip install -r requirements.txt      # first time, and when requirements.txt changes
cp .env.example .env                 # first time only, then edit it (see Environment)
uvicorn main:app --reload --port 8002
```

A healthy start looks like this:

```text
INFO:     Uvicorn running on http://127.0.0.1:8002 (Press CTRL+C to quit)
INFO:     Started reloader process [29752] using WatchFiles
INFO:     Started server process [30316]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
```

To run without activating the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --reload --port 8002
```

Use port `8002` locally. The frontend defaults `REACT_APP_API_URL` to `http://127.0.0.1:8002`. The `Procfile` and `Dockerfile` default to `8000` only because hosting platforms inject `PORT`.

## Environment

`main.py` reads `backend/.env` itself before importing storage. The loader is deliberately minimal, which has three consequences:

- Variables already set in the shell win over `.env`.
- Inline comments are **not** stripped. `KEY=value  # note` sets the value to `value  # note`.
- Quotes are **not** stripped. Write `KEY=value`, not `KEY="value"`.

Minimal `.env` for local development on SQLite:

```env
SECRET_KEY=<long random string>
TOKEN_EXPIRE_MINUTES=10080
DB_PATH=calcvoyager.db
PROGRESS_DB=sqlite
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

Generate a secret with:

```bash
python -c "import secrets; print(secrets.token_hex(64))"
```

> **Do not use `.env.example` unedited.** Its placeholder `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are both non-empty, which switches the backend to Supabase against a project that does not exist. The server starts, but every database request fails. Blank both values to stay on SQLite.

### Storage backend selection

Decided once at import time in `core/storage.py`:

| Condition | Backend used |
| --- | --- |
| `DB_BACKEND`, `DATABASE_BACKEND`, or `PROGRESS_DB` equals `supabase` | Supabase. Startup fails if the URL or key is missing |
| `SUPABASE_URL` and a Supabase key are both set | Supabase, whatever the selector says |
| Anything else | SQLite at `DB_PATH`. The file and schema are created on startup |

The Supabase key is read from `SUPABASE_SERVICE_ROLE_KEY`, then `SUPABASE_SECRET_KEY`, then `SUPABASE_KEY`. Before the first Supabase run, execute `supabase_schema.sql` in the Supabase SQL Editor.

### Optional variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `ALLOWED_ORIGINS` | empty | Extra CORS origins, comma-separated. Added to the built-in localhost and production origins, never replaces them |
| `FRONTEND_VERIFY_URL` | production `/verify` URL | Base URL encoded in certificate QR codes. Set to `http://localhost:3000/verify` to test verification locally |
| `CERT_TOKEN_EXPIRE_DAYS` | `3650` | Certificate token lifetime |
| `QUIZ_SUBMIT_GRACE_SECONDS` | `20` | Grace period after a quiz timer expires |

## Verify It Is Running

| Check | Expected |
| --- | --- |
| `curl http://127.0.0.1:8002/api/health` | `200` `{"status":"ok"}` |
| Open `http://127.0.0.1:8002/docs` | HTML endpoint list |
| Open `http://127.0.0.1:8002/` | JSON service index |
| `curl http://127.0.0.1:8002/api/auth/me` | `401` (no token) |

End-to-end check of storage and auth. This creates a user named `runcheck` in the active database.

PowerShell:

```powershell
$r = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8002/api/auth/signup `
  -ContentType application/json -Body '{"username":"runcheck","password":"secret123"}'
Invoke-RestMethod -Uri http://127.0.0.1:8002/api/auth/me `
  -Headers @{ Authorization = "Bearer $($r.access_token)" }
```

macOS / Linux:

```bash
curl -X POST http://127.0.0.1:8002/api/auth/signup \
  -H "Content-Type: application/json" \
  -d '{"username":"runcheck","password":"secret123"}'
curl http://127.0.0.1:8002/api/auth/me -H "Authorization: Bearer <access_token>"
```

Signup returns `201` with `access_token`; `/api/auth/me` then returns the user's `id`, `username`, `email`, and `created_at`. A second signup with the same username returns `409`.

To run this check without touching your real data, point a second instance at a scratch database:

```powershell
$env:DB_PATH = "$env:TEMP\runcheck.db"
uvicorn main:app --port 8012
```

## Running With the Frontend

Start the backend first, then the frontend. `frontend/.env.development` already points `REACT_APP_API_URL` at `http://127.0.0.1:8002`, so no extra configuration is needed when the backend runs on the default local port. See `../../frontend/docs/DEVELOPMENT_WORKFLOW.md` for the frontend commands.

`/api/chat/*` is mounted only when the `Calculus-AI-Chatbot` submodule exposes `backend/app/routes/chat.py`. When it does not, the import is skipped silently and those paths return `404`. The frontend reaches chat through its own `REACT_APP_CHAT_API_URL` (`http://127.0.0.1:8003`) instead.

## Other Ways to Run

| Goal | Command |
| --- | --- |
| No auto-reload | `uvicorn main:app --port 8002` |
| Reachable from other devices on the network | `uvicorn main:app --host 0.0.0.0 --port 8002` |
| Hosting platform (`Procfile`) | `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}` |

Keep to a single worker when using SQLite. Multiple workers cause `database is locked` errors.

### Docker

```bash
cd backend
docker build -t calcvoyager-backend .
docker run --rm -p 8002:8000 --env-file .env calcvoyager-backend
```

The container listens on `8000`; `-p 8002:8000` keeps the local URL the frontend expects.

- SQLite data lives inside the container and is lost when it is removed. Mount a volume for `DB_PATH` or use Supabase.
- There is no `.dockerignore`, so `COPY . .` puts `.env`, `.venv`, and the local database into the image. Do not push a locally built image to a registry.

## Stopping the Server

Press `Ctrl+C` in the terminal running uvicorn.

With `--reload`, uvicorn runs a reloader process and a child server process. If the terminal is gone and the port is still held, stop the whole tree.

Windows PowerShell:

```powershell
$procId = (Get-NetTCPConnection -LocalPort 8002 -State Listen).OwningProcess
taskkill /PID $procId /T /F
```

macOS / Linux:

```bash
lsof -ti :8002 | xargs kill
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `Error loading ASGI app. Could not import module "main".` | Started outside `backend/` | `cd backend` first |
| `[Errno 10048] error while attempting to bind on address ('127.0.0.1', 8002)` or `Address already in use` | Another process holds the port | Stop it (see [Stopping the Server](#stopping-the-server)), or pick another port and update `REACT_APP_API_URL` |
| `uvicorn` is not recognized | Virtual environment not active, or dependencies missing | Activate `.venv` and run `pip install -r requirements.txt` |
| `Activate.ps1 cannot be loaded because running scripts is disabled` | PowerShell execution policy | `Set-ExecutionPolicy -Scope Process RemoteSigned`, or call `.\.venv\Scripts\python.exe -m uvicorn ...` directly |
| `RuntimeError: SUPABASE_URL and SUPABASE_KEY are required when Supabase is enabled.` | Selector is `supabase` but credentials are blank | Set `PROGRESS_DB=sqlite`, or fill in the Supabase values |
| Server starts, but signup and login return `500 Database error` | Placeholder Supabase values left in `.env` | Blank `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`, then restart |
| Users or progress missing after a restart | Started from a different directory, so a relative `DB_PATH` created a new empty file | Run from `backend/`, or set an absolute `DB_PATH` |
| `.env` edits have no effect | Value is also set in the shell, or the server was not restarted (`--reload` watches `.py` files only) | Clear the shell variable and restart |
| `/api/chat/*` returns `404` | Chatbot routes not importable | Expected locally; see [Running With the Frontend](#running-with-the-frontend) |

# Backend Documentation

The backend is the CalcVoyager persistence API. It owns user accounts, JWT authentication, learning progress, bookmarks, quiz scores, and solver usage history.

## Table of Contents

- [Documentation Map](#documentation-map)
- [Runtime Summary](#runtime-summary)
- [Source Map](#source-map)
- [API Areas](#api-areas)
- [Configuration](#configuration)
- [Ownership Boundary](#ownership-boundary)

## Documentation Map

| File | Purpose |
| --- | --- |
| `README.md` | Backend overview and navigation |
| `RUN.md` | Start, verify, and stop the backend locally |
| `ARCHITECTURE.md` | Starlette app structure, routing, storage abstraction |
| `API_REFERENCE.md` | Backend endpoint contracts |
| `DATA_AND_AUTH.md` | Database schema, Supabase mapping, JWT/password flow |
| `DEVELOPMENT_WORKFLOW.md` | Setup, commands, validation, deployment, troubleshooting |

## Runtime Summary

| Item | Current implementation |
| --- | --- |
| Framework | Starlette |
| Server | Uvicorn |
| Persistence | SQLite by default; Supabase when configured |
| Auth | bcrypt password hashing + PyJWT bearer tokens |
| Schema owner | `core/db.py` for SQLite, `supabase_schema.sql` for Supabase |
| API docs | Custom HTML at `/docs` |
| Health check | `GET /api/health` |

## Source Map

| Path | Responsibility |
| --- | --- |
| `main.py` | App creation, CORS, routes, startup, HTML docs |
| `core/auth_utils.py` | Password hashing, JWT creation/decoding, request auth helpers |
| `core/db.py` | SQLite schema and async wrappers around blocking `sqlite3` calls |
| `core/storage.py` | Storage abstraction for SQLite and Supabase |
| `core/pdf_utils.py` | Certificate PDF rendering |
| `core/qr_utils.py` | Certificate QR code generation |
| `core/quiz_bank.py` | Server-side certification quiz question bank |
| `routers/` | Route handlers grouped by API area |
| `services/` | Cross-cutting business logic used by routers |
| `supabase_schema.sql` | Supabase table definitions and indexes |
| `.env.example` | Local and production environment variable reference |

## API Areas

| Area | Base path | Purpose |
| --- | --- | --- |
| System | `/`, `/docs`, `/api/health` | Service metadata, docs, health |
| Auth | `/api/auth` | Signup, login, current user |
| Progress | `/api/progress` | Completed sections and progress snapshot |
| Bookmarks | `/api/bookmarks` | User bookmark list and mutations |
| Quiz | `/api/quiz` | Quiz score list and best-score updates |
| Solver | `/api/solver` | Solver usage log and history |

## Configuration

Backend environment variables are documented in `.env.example`:

```env
SECRET_KEY=replace-with-a-long-random-string
TOKEN_EXPIRE_MINUTES=10080
DB_PATH=calcvoyager.db
PROGRESS_DB=supabase
SUPABASE_URL=https://xyzcompany.supabase.co
SUPABASE_SERVICE_ROLE_KEY=replace-with-your-backend-only-service-role-or-secret-key
ALLOWED_ORIGINS=http://localhost:3000
```

SQLite is used unless Supabase is enabled through environment variables.

## Ownership Boundary

Backend docs own endpoint contracts, authentication behavior, data schema, storage behavior, and backend operations. Frontend route/component details belong in `../../frontend/docs/`; cross-service deployment guidance belongs in `../../docs/DEPLOYMENT.md`.

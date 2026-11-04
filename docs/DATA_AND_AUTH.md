# Data and Authentication

## Table of Contents

- [Storage Backends](#storage-backends)
- [SQLite Schema](#sqlite-schema)
- [Supabase Schema](#supabase-schema)
- [Authentication Flow](#authentication-flow)
- [Authorization Rules](#authorization-rules)
- [Data Retention Notes](#data-retention-notes)
- [Security Considerations](#security-considerations)

## Storage Backends

The backend supports two storage modes:

| Mode | Activation | Code path |
| --- | --- | --- |
| SQLite | Default | `core/db.py` through `core/storage.py` |
| Supabase | `DB_BACKEND=supabase`, `DATABASE_BACKEND=supabase`, `PROGRESS_DB=supabase`, or Supabase URL/key present | Supabase client through `core/storage.py` |

SQLite creates the schema on startup. Supabase requires applying `supabase_schema.sql` before starting the service.

## SQLite Schema

Defined in `core/db.py`.

| Table | Key fields | Purpose |
| --- | --- | --- |
| `users` | `id`, `username`, `email`, `hashed_pw`, `created_at` | Account records |
| `sections` | `user_id`, `section_id`, `completed`, `completed_at` | Completed curriculum sections |
| `bookmarks` | `user_id`, `bm_id`, `title`, `path`, `added_at` | Saved pages/tools |
| `quiz_scores` | `user_id`, `quiz_id`, `score`, `total`, `taken_at` | Best quiz score per user and quiz |
| `solver_history` | `user_id`, `expression`, `result`, `created_at` | Solver usage history |

Foreign keys cascade deletes from `users` to dependent rows.

## Supabase Schema

`supabase_schema.sql` mirrors the SQLite tables using `bigint` identities and timestamp columns. It also creates indexes on user-owned tables.

The schema disables row-level security on the custom app tables because this backend uses its own JWT authentication and performs server-side Supabase writes. If RLS is re-enabled, policies must be designed for backend service-role access.

## Authentication Flow

1. `POST /api/auth/signup` validates username and password length.
2. Passwords are hashed with bcrypt in `auth_utils.hash_password`.
3. The user is inserted through `storage.create_user`.
4. The backend signs a JWT with `HS256`.
5. The token payload contains:
   - `sub`: user id as a string
   - `exp`: expiration timestamp
6. The frontend stores the token in localStorage.
7. Protected requests send `Authorization: Bearer <token>`.
8. `auth_utils.require_user` decodes the token and returns the user id.

### Email verification

New accounts must verify their email before they can open courses
(`users.verification_required = 1`). Accounts created earlier keep
`verification_required = 0` and can verify from their profile at any time.

1. `POST /api/auth/signup` now requires `email`. It creates the user and sends
   a 6-digit code plus a one-click link (`/verify-email?token=…` on the frontend).
2. `POST /api/auth/verify-email/confirm` `{code}` (signed in) or
   `POST /api/auth/verify-email/link` `{token}` (no sign-in needed) sets
   `users.email_verified = 1`.
3. `POST /api/auth/verify-email/send` `{email?}` resends (60 s cooldown) and can
   change the address first. Changing the email via `PATCH /api/auth/me` resets
   `email_verified`.
4. Only keyed SHA-256 hashes of the code and link token are stored, in
   `email_verifications` (one row per user). Codes expire after 15 minutes and
   allow 5 wrong tries.

Mail is sent with Resend (`RESEND_API_KEY`) or SMTP (`SMTP_HOST`, `SMTP_PORT`,
`SMTP_USER`, `SMTP_PASSWORD`), from `EMAIL_FROM`. `FRONTEND_URL` sets where the
link points. For local development `EMAIL_DEV_MODE=true` prints the code in the
backend console and returns it to the frontend instead of emailing it.

## Authorization Rules

| Resource | Rule |
| --- | --- |
| `/api/auth/signup` | Public |
| `/api/auth/login` | Public |
| `/api/auth/me` | Requires valid bearer token |
| Progress | User can read/write only their own rows |
| Bookmarks | User can read/write only their own rows |
| Quiz scores | User can read/write only their own rows |
| Solver log | Optional auth; stores only when user is authenticated |
| Solver history | Returns user history when authenticated; returns `[]` otherwise |

## Data Retention Notes

- Solver history is read with `LIMIT 50`, but old rows are not automatically deleted.
- Quiz scores are upserted by user and quiz id, keeping the best score.
- Bookmarks are unique by user and bookmark id.
- Completed sections are unique by user and section id.

## Security Considerations

- Set a strong `SECRET_KEY` outside local development.
- Keep Supabase service-role keys server-side only.
- Treat the SQLite database file as sensitive data.
- Avoid logging bearer tokens.
- Validate numeric quiz inputs before casting.
- Consider rate limiting auth endpoints before public production use.

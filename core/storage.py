import asyncio
import os
from typing import Any, Dict, Optional
from datetime import datetime, timezone

from . import db


DB_BACKEND = (
    os.getenv("DB_BACKEND")
    or os.getenv("DATABASE_BACKEND")
    or os.getenv("PROGRESS_DB")
    or ""
).strip().lower()
SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_SECRET_KEY")
    or os.getenv("SUPABASE_KEY")
    or ""
).strip()
USE_SUPABASE = DB_BACKEND == "supabase" or bool(SUPABASE_URL and SUPABASE_KEY)

if USE_SUPABASE:
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_KEY are required when Supabase is enabled."
        )

    from supabase import create_client

    if SUPABASE_KEY.startswith("sb_publishable_"):
        print(
            "[CalcVoyager] Warning: SUPABASE_KEY is a publishable key. "
            "Use SUPABASE_SERVICE_ROLE_KEY for backend writes, or disable RLS "
            "on the app tables in backend/supabase_schema.sql.",
            flush=True,
        )

    _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def _data(resp: Any) -> Any:
    if getattr(resp, "error", None):
        raise RuntimeError(str(resp.error))
    return getattr(resp, "data", None)


def _parse_completed_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc)

    if isinstance(value, str):
        try:
            numeric = int(value)
            return datetime.fromtimestamp(numeric, timezone.utc)
        except ValueError:
            pass

        try:
            dt = datetime.fromisoformat(value)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    return None


def _review_metadata_for_completed_at(value: Any) -> Dict[str, Any]:
    completed_dt = _parse_completed_at(value)
    if not completed_dt:
        return {"needs_review": False, "days_since_completion": 0}

    now = datetime.now(timezone.utc)
    days = int((now - completed_dt).total_seconds() // 86400)
    return {
    "needs_review": days >= 14,
    "days_since_completion": days,
}
 

async def init_storage() -> None:
    if USE_SUPABASE:
        return None
    await db.init_db()


async def get_user_by_username(username: str) -> Optional[Dict[str, Any]]:
    if USE_SUPABASE:
        return await asyncio.to_thread(_get_user_by_username_supabase, username)
    return await db.fetchone(
        "SELECT id, username, hashed_pw FROM users WHERE username = ?", (username,)
    )


async def create_user(username: str, email: Optional[str], hashed_pw: str) -> int:
    if USE_SUPABASE:
        return await asyncio.to_thread(_create_user_supabase, username, email, hashed_pw)
    return await db.execute(
        "INSERT INTO users (username, email, hashed_pw) VALUES (?, ?, ?)",
        (username, email, hashed_pw),
    )


async def get_user_profile(user_id: int) -> Optional[Dict[str, Any]]:
    if USE_SUPABASE:
        return await asyncio.to_thread(_get_user_profile_supabase, user_id)
    return await db.fetchone(
        "SELECT id, username, email, created_at FROM users WHERE id = ?", (user_id,)
    )


async def get_progress(user_id: int) -> Dict[str, Any]:
    if USE_SUPABASE:
        return await asyncio.to_thread(_get_progress_supabase, user_id)
    return await _get_progress_sqlite(user_id)


async def mark_section_complete(user_id: int, section_id: str) -> Optional[int]:
    if USE_SUPABASE:
        return await asyncio.to_thread(
            _mark_section_complete_supabase, user_id, section_id
        )
    # Stale JWTs can reference deleted users — fail cleanly instead of FK 500.
    profile = await db.fetchone("SELECT id FROM users WHERE id = ?", (user_id,))
    if not profile:
        raise PermissionError("User not found")
    await db.execute(
        "INSERT INTO sections (user_id, section_id) VALUES (?, ?) "
        "ON CONFLICT(user_id, section_id) DO UPDATE SET completed=1, completed_at=strftime('%s','now')",
        (user_id, section_id),
    )
    row = await db.fetchone(
        "SELECT completed_at FROM sections WHERE user_id = ? AND section_id = ?",
        (user_id, section_id),
    )
    return int(row["completed_at"]) if row and row.get("completed_at") is not None else None


async def unmark_section_complete(user_id: int, section_id: str) -> None:
    if USE_SUPABASE:
        return await asyncio.to_thread(
            _unmark_section_complete_supabase, user_id, section_id
        )
    await db.execute(
        "DELETE FROM sections WHERE user_id = ? AND section_id = ?",
        (user_id, section_id),
    )


async def list_bookmarks(user_id: int) -> list[Dict[str, Any]]:
    if USE_SUPABASE:
        return await asyncio.to_thread(_list_bookmarks_supabase, user_id)
    rows = await db.fetchall(
        "SELECT bm_id, title, path, added_at FROM bookmarks "
        "WHERE user_id = ? ORDER BY added_at DESC",
        (user_id,),
    )
    return [_bookmark_json(row) for row in rows]


async def add_bookmark(user_id: int, bm_id: str, title: str, path: str) -> None:
    if USE_SUPABASE:
        return await asyncio.to_thread(
            _add_bookmark_supabase, user_id, bm_id, title, path
        )
    await db.execute(
        "INSERT INTO bookmarks (user_id, bm_id, title, path) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, bm_id) DO NOTHING",
        (user_id, bm_id, title, path),
    )


async def remove_bookmark(user_id: int, bm_id: str) -> None:
    if USE_SUPABASE:
        return await asyncio.to_thread(_remove_bookmark_supabase, user_id, bm_id)
    await db.execute(
        "DELETE FROM bookmarks WHERE user_id = ? AND bm_id = ?",
        (user_id, bm_id),
    )


async def list_quiz_scores(user_id: int) -> Dict[str, Dict[str, int]]:
    if USE_SUPABASE:
        return await asyncio.to_thread(_list_quiz_scores_supabase, user_id)
    rows = await db.fetchall(
        "SELECT quiz_id, score, total FROM quiz_scores "
        "WHERE user_id = ? ORDER BY taken_at DESC",
        (user_id,),
    )
    return {
        row["quiz_id"]: {"score": row["score"], "total": row["total"]}
        for row in rows
    }


async def save_quiz_score(user_id: int, quiz_id: str, score: int, total: int) -> None:
    if USE_SUPABASE:
        return await asyncio.to_thread(
            _save_quiz_score_supabase, user_id, quiz_id, score, total
        )
    await db.execute(
        "INSERT INTO quiz_scores (user_id, quiz_id, score, total) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, quiz_id) DO UPDATE SET "
        "  score = CASE WHEN excluded.score > score THEN excluded.score ELSE score END, "
        "  total = excluded.total, "
        "  taken_at = strftime('%s','now')",
        (user_id, quiz_id, score, total),
    )


# ── Quiz attempt history (every attempt, never overwritten) ───────────────────


async def record_quiz_attempt(
    user_id: int, quiz_id: str, score: int, total: int, passed: bool
) -> None:
    if USE_SUPABASE:
        return await asyncio.to_thread(
            _record_quiz_attempt_supabase, user_id, quiz_id, score, total, passed
        )
    await db.execute(
        "INSERT INTO quiz_attempts (user_id, quiz_id, score, total, passed) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, quiz_id, score, total, 1 if passed else 0),
    )


async def list_quiz_attempts(
    user_id: int, quiz_id: Optional[str] = None
) -> list[Dict[str, Any]]:
    if USE_SUPABASE:
        return await asyncio.to_thread(_list_quiz_attempts_supabase, user_id, quiz_id)
    if quiz_id:
        rows = await db.fetchall(
            "SELECT quiz_id, score, total, passed, attempted_at FROM quiz_attempts "
            "WHERE user_id = ? AND quiz_id = ? ORDER BY attempted_at DESC",
            (user_id, quiz_id),
        )
    else:
        rows = await db.fetchall(
            "SELECT quiz_id, score, total, passed, attempted_at FROM quiz_attempts "
            "WHERE user_id = ? ORDER BY attempted_at DESC",
            (user_id,),
        )
    return [
        {
            "quizId": r["quiz_id"],
            "score": r["score"],
            "total": r["total"],
            "passed": bool(r["passed"]),
            "attemptedAt": r["attempted_at"],
        }
        for r in rows
    ]


# ── Certificates (permanent record, one per user+course) ──────────────────────


async def save_certificate_record(
    cert_id: str,
    user_id: int,
    course_id: str,
    course_title: str,
    full_name: str,
    score: Optional[int],
    total: Optional[int],
) -> Dict[str, Any]:
    if USE_SUPABASE:
        return await asyncio.to_thread(
            _save_certificate_record_supabase,
            cert_id, user_id, course_id, course_title, full_name, score, total,
        )
    existing = await db.fetchone(
        "SELECT cert_id FROM certificates WHERE user_id = ? AND course_id = ?",
        (user_id, course_id),
    )
    if existing:
        await db.execute(
            "UPDATE certificates SET full_name = ?, score = ?, total = ? "
            "WHERE user_id = ? AND course_id = ?",
            (full_name, score, total, user_id, course_id),
        )
        cert_id = existing["cert_id"]
    else:
        await db.execute(
            "INSERT INTO certificates "
            "(cert_id, user_id, course_id, course_title, full_name, score, total) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cert_id, user_id, course_id, course_title, full_name, score, total),
        )
    return await get_certificate_record(cert_id)


async def get_certificate_record(cert_id: str) -> Optional[Dict[str, Any]]:
    if USE_SUPABASE:
        return await asyncio.to_thread(_get_certificate_record_supabase, cert_id)
    row = await db.fetchone(
        "SELECT cert_id, user_id, course_id, course_title, full_name, score, total, "
        "issued_at FROM certificates WHERE cert_id = ?",
        (cert_id,),
    )
    return dict(row) if row else None


async def list_certificates_for_user(user_id: int) -> list[Dict[str, Any]]:
    """All certificates a user has earned, most recently issued first."""
    if USE_SUPABASE:
        return await asyncio.to_thread(_list_certificates_for_user_supabase, user_id)
    rows = await db.fetchall(
        "SELECT cert_id, course_id, course_title, full_name, score, total, issued_at "
        "FROM certificates WHERE user_id = ? ORDER BY issued_at DESC",
        (user_id,),
    )
    return [dict(r) for r in rows]


async def get_leaderboard_opt_in(user_id: int) -> bool:
    if USE_SUPABASE:
        try:
            return await asyncio.to_thread(_get_leaderboard_opt_in_supabase, user_id)
        except Exception:
            return False
    row = await db.fetchone(
        "SELECT leaderboard_opt_in FROM user_prefs WHERE user_id = ?",
        (user_id,),
    )
    return bool(row and row["leaderboard_opt_in"])


async def set_leaderboard_opt_in(user_id: int, opt_in: bool) -> bool:
    value = 1 if opt_in else 0
    if USE_SUPABASE:
        try:
            await asyncio.to_thread(_set_leaderboard_opt_in_supabase, user_id, bool(opt_in))
        except Exception:
            pass
        return bool(opt_in)
    await db.execute(
        "INSERT INTO user_prefs (user_id, leaderboard_opt_in) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET leaderboard_opt_in = excluded.leaderboard_opt_in",
        (user_id, value),
    )
    return bool(opt_in)


def _leaderboard_entry(
    user_id: int, username: str, topics: Any, quiz_count: Any, quiz_pct: Any
) -> Dict[str, Any]:
    return {
        "userId": int(user_id),
        "label": username or "Unknown",
        "topics": int(topics or 0),
        "quizCount": int(quiz_count or 0),
        "quizPct": int(round(quiz_pct or 0)),
    }


async def get_leaderboard() -> list[Dict[str, Any]]:
    """Stats for every user who opted in to the leaderboard."""
    if USE_SUPABASE:
        return await asyncio.to_thread(_get_leaderboard_supabase)
    rows = await db.fetchall(
        "SELECT u.id AS user_id, u.username, "
        "  (SELECT COUNT(*) FROM sections s WHERE s.user_id = u.id AND s.completed = 1) AS topics, "
        "  (SELECT COUNT(*) FROM quiz_scores q WHERE q.user_id = u.id) AS quiz_count, "
        "  (SELECT AVG(q.score * 100.0 / q.total) FROM quiz_scores q "
        "     WHERE q.user_id = u.id AND q.total > 0) AS quiz_pct "
        "FROM users u "
        "JOIN user_prefs p ON p.user_id = u.id "
        "WHERE p.leaderboard_opt_in = 1"
    )
    return [
        _leaderboard_entry(
            row["user_id"], row["username"], row["topics"], row["quiz_count"], row["quiz_pct"]
        )
        for row in rows
    ]


async def log_solver_use(
    user_id: Optional[int], expression: Optional[str], result: Optional[str]
) -> None:
    if not user_id:
        return None
    if USE_SUPABASE:
        return await asyncio.to_thread(
            _log_solver_use_supabase, user_id, expression, result
        )
    await db.execute(
        "INSERT INTO solver_history (user_id, expression, result) VALUES (?, ?, ?)",
        (user_id, expression, result),
    )


async def list_solver_history(user_id: int) -> list[Dict[str, Any]]:
    if USE_SUPABASE:
        return await asyncio.to_thread(_list_solver_history_supabase, user_id)
    return await db.fetchall(
        "SELECT expression, result, created_at FROM solver_history "
        "WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
        (user_id,),
    )


async def _get_progress_sqlite(user_id: int) -> Dict[str, Any]:
    section_rows = await db.fetchall(
        "SELECT section_id, completed_at FROM sections WHERE user_id = ?",
        (user_id,),
    )
    solver_count = (
        await db.scalar(
            "SELECT COUNT(*) FROM solver_history WHERE user_id = ?", (user_id,)
        )
        or 0
    )

    return {
        "completedSections": {row["section_id"]: True for row in section_rows},
        "completedSectionTimestamps": {
            row["section_id"]: row["completed_at"]
            for row in section_rows
            if row.get("completed_at") is not None
        },
        "completedSectionMetadata": {
            row["section_id"]: _review_metadata_for_completed_at(row["completed_at"])
            for row in section_rows
            if row.get("completed_at") is not None
        },
        "quizScores": await list_quiz_scores(user_id),
        "bookmarks": await list_bookmarks(user_id),
        "solverUses": solver_count,
        "leaderboardOptIn": await get_leaderboard_opt_in(user_id),
    }


def _first(resp: Any) -> Optional[Dict[str, Any]]:
    rows = _data(resp) or []
    return rows[0] if rows else None


def _bookmark_json(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": row["bm_id"],
        "title": row["title"],
        "path": row["path"],
        "addedAt": row["added_at"],
    }


if USE_SUPABASE:

    def _get_user_by_username_supabase(username: str) -> Optional[Dict[str, Any]]:
        resp = (
            _supabase.from_("users")
            .select("id,username,hashed_pw")
            .eq("username", username)
            .limit(1)
            .execute()
        )
        return _first(resp)

    def _create_user_supabase(
        username: str, email: Optional[str], hashed_pw: str
    ) -> int:
        payload = {"username": username, "email": email, "hashed_pw": hashed_pw}
        resp = _supabase.from_("users").insert(payload).execute()
        rows = _data(resp) or []
        if not rows:
            raise RuntimeError("Supabase did not return the created user.")
        return int(rows[0]["id"])

    def _get_user_profile_supabase(user_id: int) -> Optional[Dict[str, Any]]:
        resp = (
            _supabase.from_("users")
            .select("id,username,email,created_at")
            .eq("id", user_id)
            .limit(1)
            .execute()
        )
        return _first(resp)

    def _get_progress_supabase(user_id: int) -> Dict[str, Any]:
        section_resp = (
            _supabase.from_("sections")
            .select("section_id,completed_at")
            .eq("user_id", user_id)
            .execute()
        )
        section_rows = _data(section_resp) or []

        solver_resp = (
            _supabase.from_("solver_history")
            .select("id", count="exact")
            .eq("user_id", user_id)
            .execute()
        )

        return {
            "completedSections": {
                row["section_id"]: True for row in section_rows
            },
            "completedSectionTimestamps": {
                row["section_id"]: row.get("completed_at")
                for row in section_rows
                if row.get("completed_at") is not None
            },
            "completedSectionMetadata": {
                row["section_id"]: _review_metadata_for_completed_at(row.get("completed_at"))
                for row in section_rows
                if row.get("completed_at") is not None
            },
            "quizScores": _list_quiz_scores_supabase(user_id),
            "bookmarks": _list_bookmarks_supabase(user_id),
            "solverUses": getattr(solver_resp, "count", 0) or 0,
            "leaderboardOptIn": _get_leaderboard_opt_in_supabase(user_id),
        }

    def _mark_section_complete_supabase(user_id: int, section_id: str) -> Optional[int]:
        # Ensure completed_at is set/updated to current UTC time on upsert
        now_iso = datetime.utcnow().isoformat()
        resp = (
            _supabase.from_("sections")
            .upsert(
                {
                    "user_id": user_id,
                    "section_id": section_id,
                    "completed": True,
                    "completed_at": now_iso,
                },
                on_conflict="user_id,section_id",
            )
            .execute()
        )
        _data(resp)
        return now_iso

    def _unmark_section_complete_supabase(user_id: int, section_id: str) -> None:
        resp = (
            _supabase.from_("sections")
            .delete()
            .eq("user_id", user_id)
            .eq("section_id", section_id)
            .execute()
        )
        _data(resp)

    def _list_bookmarks_supabase(user_id: int) -> list[Dict[str, Any]]:
        resp = (
            _supabase.from_("bookmarks")
            .select("bm_id,title,path,added_at")
            .eq("user_id", user_id)
            .order("added_at", desc=True)
            .execute()
        )
        return [_bookmark_json(row) for row in (_data(resp) or [])]

    def _add_bookmark_supabase(
        user_id: int, bm_id: str, title: str, path: str
    ) -> None:
        resp = (
            _supabase.from_("bookmarks")
            .upsert(
                {"user_id": user_id, "bm_id": bm_id, "title": title, "path": path},
                on_conflict="user_id,bm_id",
            )
            .execute()
        )
        _data(resp)

    def _remove_bookmark_supabase(user_id: int, bm_id: str) -> None:
        resp = (
            _supabase.from_("bookmarks")
            .delete()
            .eq("user_id", user_id)
            .eq("bm_id", bm_id)
            .execute()
        )
        _data(resp)

    def _list_quiz_scores_supabase(user_id: int) -> Dict[str, Dict[str, int]]:
        resp = (
            _supabase.from_("quiz_scores")
            .select("quiz_id,score,total")
            .eq("user_id", user_id)
            .order("taken_at", desc=True)
            .execute()
        )
        return {
            row["quiz_id"]: {"score": row["score"], "total": row["total"]}
            for row in (_data(resp) or [])
        }

    def _save_quiz_score_supabase(
        user_id: int, quiz_id: str, score: int, total: int
    ) -> None:
        existing_resp = (
            _supabase.from_("quiz_scores")
            .select("score")
            .eq("user_id", user_id)
            .eq("quiz_id", quiz_id)
            .limit(1)
            .execute()
        )
        existing = _first(existing_resp)
        best_score = max(score, int(existing["score"])) if existing else score
        resp = (
            _supabase.from_("quiz_scores")
            .upsert(
                {
                    "user_id": user_id,
                    "quiz_id": quiz_id,
                    "score": best_score,
                    "total": total,
                },
                on_conflict="user_id,quiz_id",
            )
            .execute()
        )
        _data(resp)

    def _record_quiz_attempt_supabase(
        user_id: int, quiz_id: str, score: int, total: int, passed: bool
    ) -> None:
        resp = (
            _supabase.from_("quiz_attempts")
            .insert(
                {
                    "user_id": user_id,
                    "quiz_id": quiz_id,
                    "score": score,
                    "total": total,
                    "passed": passed,
                }
            )
            .execute()
        )
        _data(resp)

    def _list_quiz_attempts_supabase(
        user_id: int, quiz_id: Optional[str]
    ) -> list[Dict[str, Any]]:
        query = _supabase.from_("quiz_attempts").select("*").eq("user_id", user_id)
        if quiz_id:
            query = query.eq("quiz_id", quiz_id)
        resp = query.order("attempted_at", desc=True).execute()
        rows = _data(resp) or []
        return [
            {
                "quizId": row["quiz_id"],
                "score": row["score"],
                "total": row["total"],
                "passed": bool(row["passed"]),
                "attemptedAt": row["attempted_at"],
            }
            for row in rows
        ]

    def _save_certificate_record_supabase(
        cert_id: str,
        user_id: int,
        course_id: str,
        course_title: str,
        full_name: str,
        score: Optional[int],
        total: Optional[int],
    ) -> Dict[str, Any]:
        existing_resp = (
            _supabase.from_("certificates")
            .select("cert_id")
            .eq("user_id", user_id)
            .eq("course_id", course_id)
            .limit(1)
            .execute()
        )
        existing = _first(existing_resp)
        row_cert_id = existing["cert_id"] if existing else cert_id
        resp = (
            _supabase.from_("certificates")
            .upsert(
                {
                    "cert_id": row_cert_id,
                    "user_id": user_id,
                    "course_id": course_id,
                    "course_title": course_title,
                    "full_name": full_name,
                    "score": score,
                    "total": total,
                },
                on_conflict="user_id,course_id",
            )
            .execute()
        )
        _data(resp)
        return _get_certificate_record_supabase(row_cert_id)

    def _get_certificate_record_supabase(cert_id: str) -> Optional[Dict[str, Any]]:
        resp = (
            _supabase.from_("certificates")
            .select("*")
            .eq("cert_id", cert_id)
            .limit(1)
            .execute()
        )
        return _first(resp)

    def _list_certificates_for_user_supabase(user_id: int) -> list[Dict[str, Any]]:
        resp = (
            _supabase.from_("certificates")
            .select("cert_id,course_id,course_title,full_name,score,total,issued_at")
            .eq("user_id", user_id)
            .order("issued_at", desc=True)
            .execute()
        )
        return _data(resp) or []

    def _log_solver_use_supabase(
        user_id: int, expression: Optional[str], result: Optional[str]
    ) -> None:
        resp = (
            _supabase.from_("solver_history")
            .insert(
                {"user_id": user_id, "expression": expression, "result": result}
            )
            .execute()
        )
        _data(resp)

    def _list_solver_history_supabase(user_id: int) -> list[Dict[str, Any]]:
        resp = (
            _supabase.from_("solver_history")
            .select("expression,result,created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return _data(resp) or []

    def _get_leaderboard_supabase() -> list[Dict[str, Any]]:
        prefs_resp = (
            _supabase.from_("user_prefs")
            .select("user_id")
            .eq("leaderboard_opt_in", True)
            .execute()
        )
        user_ids = [row["user_id"] for row in (_data(prefs_resp) or [])]
        if not user_ids:
            return []

        users_resp = (
            _supabase.from_("users")
            .select("id,username")
            .in_("id", user_ids)
            .execute()
        )
        usernames = {row["id"]: row["username"] for row in (_data(users_resp) or [])}

        sections_resp = (
            _supabase.from_("sections")
            .select("user_id")
            .in_("user_id", user_ids)
            .eq("completed", True)
            .execute()
        )
        topic_counts: Dict[int, int] = {}
        for row in _data(sections_resp) or []:
            topic_counts[row["user_id"]] = topic_counts.get(row["user_id"], 0) + 1

        quiz_resp = (
            _supabase.from_("quiz_scores")
            .select("user_id,score,total")
            .in_("user_id", user_ids)
            .execute()
        )
        quiz_pcts: Dict[int, list] = {}
        for row in _data(quiz_resp) or []:
            if row.get("total"):
                quiz_pcts.setdefault(row["user_id"], []).append(
                    row["score"] * 100.0 / row["total"]
                )

        entries = []
        for uid in user_ids:
            pcts = quiz_pcts.get(uid, [])
            entries.append(
                _leaderboard_entry(
                    uid,
                    usernames.get(uid, "?"),
                    topic_counts.get(uid, 0),
                    len(pcts),
                    sum(pcts) / len(pcts) if pcts else 0,
                )
            )
        return entries

    def _get_leaderboard_opt_in_supabase(user_id: int) -> bool:
        try:
            resp = (
                _supabase.from_("user_prefs")
                .select("leaderboard_opt_in")
                .eq("user_id", user_id)
                .limit(1)
                .execute()
            )
            row = _first(resp)
            return bool(row and row.get("leaderboard_opt_in"))
        except Exception:
            return False

    def _set_leaderboard_opt_in_supabase(user_id: int, opt_in: bool) -> None:
        resp = (
            _supabase.from_("user_prefs")
            .upsert(
                {"user_id": user_id, "leaderboard_opt_in": bool(opt_in)},
                on_conflict="user_id",
            )
            .execute()
        )
        _data(resp)


# Learning events are untrusted study history, never certification evidence.
async def append_learning_event(user_id: int, event: dict) -> dict:
    import json
    payload = json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    if USE_SUPABASE:
        def write():
            _data(_supabase.table("learning_events").upsert(
                {"user_id": user_id, "event_id": event["eventId"], "payload": payload},
                on_conflict="user_id,event_id", ignore_duplicates=True).execute())
            return _first(_supabase.table("learning_events").select("id,payload")
                          .eq("user_id", user_id).eq("event_id", event["eventId"]).execute())
        row = await asyncio.to_thread(write)
    else:
        await db.execute("INSERT INTO learning_events(user_id,event_id,payload) VALUES (?,?,?) "
                         "ON CONFLICT(user_id,event_id) DO NOTHING", (user_id, event["eventId"], payload))
        row = await db.fetchone("SELECT id,payload FROM learning_events WHERE user_id=? AND event_id=?",
                                (user_id, event["eventId"]))
    if not row or row["payload"] != payload:
        raise ValueError("An event ID cannot be reused with different content.")
    return {"id": row["id"], "event": json.loads(row["payload"])}


async def list_learning_events(user_id: int, after: int = 0) -> list:
    import json
    if USE_SUPABASE:
        rows = await asyncio.to_thread(lambda: _data(_supabase.table("learning_events")
            .select("id,payload").eq("user_id", user_id).gt("id", after).order("id").limit(200).execute()))
    else:
        rows = await db.fetchall("SELECT id,payload FROM learning_events WHERE user_id=? AND id>? "
                                 "ORDER BY id LIMIT 200", (user_id, after))
    return [{"id": row["id"], "event": json.loads(row["payload"])} for row in (rows or [])]

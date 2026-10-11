"""Quiz scores routes.

Certification-quiz flow (`/start` + `/submit`) is server-authoritative:
questions are shuffled per attempt, correct answers never leave the
server, and grading happens here — not in the browser. See quiz_bank.py
for the question bank.

Every graded attempt is recorded twice:
  - quiz_scores  → best score per (user, quiz), used for certificate
                   eligibility checks in routers/certificates.py.
  - quiz_attempts → full history, never overwritten (powers the
                    "attempt history" view).
"""

import os
import time
import secrets

import jwt
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse

from core import storage
from core.auth_utils import require_user, err, SECRET_KEY, ALGORITHM
from core.quiz_bank import get_quiz, QUIZ_BANK, missing_required_sections

ATTEMPT_TOKEN_TYP = "quiz_attempt"
SECONDS_PER_QUESTION = 40
# Extra slack on top of the pure per-question budget to absorb network/render
# latency between the timer hitting 0 client-side and the submit request
# landing here. Keep this small — it's not meant to allow extra thinking time.
SUBMIT_GRACE_SECONDS = int(os.getenv("QUIZ_SUBMIT_GRACE_SECONDS", "20"))
MIN_PASS_PERCENT = 80


async def list_scores(request: Request):
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")

    return JSONResponse(await storage.list_quiz_scores(user_id))


async def save_score(request: Request):
    """POST /api/quiz/  — client-reported score.

    Only for non-certification quizzes (e.g. Practice Arena drills, whose
    ids look like "practice-<topic>-<difficulty>" and aren't gated behind
    a certificate). Any id that's actually a certification quiz (present in
    quiz_bank.QUIZ_BANK) is rejected here — those scores may ONLY come from
    /submit, which grades server-side, otherwise this endpoint would be a
    direct bypass of that whole flow (POST a fake score, then claim a cert).
    """
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")
    try:
        body = await request.json()
    except Exception:
        return err(400, "Invalid JSON.")

    quiz_id = (body.get("quiz_id") or "").strip()
    score = body.get("score")
    total = body.get("total")

    if not quiz_id or score is None or total is None:
        return err(400, "quiz_id, score, and total are required.")

    if quiz_id in QUIZ_BANK:
        return err(
            403,
            f"'{quiz_id}' is a certification quiz — scores are graded "
            f"server-side via /api/quiz/{quiz_id}/start and /submit, not "
            "reported directly.",
        )

    await storage.save_quiz_score(user_id, quiz_id, int(score), int(total))
    return JSONResponse({"ok": True}, status_code=201)


async def list_attempts(request: Request):
    """GET /api/quiz/attempts?quiz_id=...
    🔒 Full attempt history for the current user (optionally filtered).
    """
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")

    quiz_id = request.query_params.get("quiz_id") or None
    return JSONResponse(await storage.list_quiz_attempts(user_id, quiz_id))


def _sign_attempt(user_id: int, quiz_id: str, order, opt_perm, expires_in: int) -> str:
    now = int(time.time())
    payload = {
        "typ": ATTEMPT_TOKEN_TYP,
        "uid": user_id,
        "quiz_id": quiz_id,
        "order": order,
        "opt_perm": opt_perm,
        "iat": now,
        "exp": now + expires_in,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def _decode_attempt(token: str):
    """Returns (payload, error_response). Exactly one of these is None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None, err(
            400,
            "Quiz attempt expired — you ran out of time. Please start a new attempt.",
        )
    except Exception:
        return None, err(400, "Invalid or tampered attempt token.")

    if payload.get("typ") != ATTEMPT_TOKEN_TYP:
        return None, err(400, "Invalid attempt token.")

    return payload, None


async def start_quiz(request: Request):
    """POST /api/quiz/{quiz_id}/start
    🔒 Requires auth.

    Builds a fresh, per-attempt shuffled question set: question order and
    each question's option order are independently randomized with a CSPRNG
    (`secrets`), so no two users — and no two attempts by the same user —
    see the same layout. The response never includes which option is
    correct; that only ever lives server-side in quiz_bank.py.

    The `attempt_token` is a short-lived signed JWT encoding the shuffle
    (order + option permutation) so /submit can grade against quiz_bank.py
    without a database round trip, and so the time budget (exp claim) is
    enforced by JWT verification itself.
    """
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")

    quiz_id = request.path_params.get("quiz_id", "")
    quiz = get_quiz(quiz_id)
    if not quiz:
        return err(404, "Unknown quiz_id.")

    if quiz.get("required_sections"):
        missing = missing_required_sections(quiz, await storage.get_progress(user_id))
        if missing:
            return JSONResponse({"detail": "Complete the required course sections first.",
                                 "missing_sections": missing}, status_code=403)

    questions = quiz["questions"]
    rng = secrets.SystemRandom()

    order = list(range(len(questions)))
    rng.shuffle(order)

    public_questions = []
    opt_perm = []
    for pos, q_idx in enumerate(order):
        q = questions[q_idx]
        n_opts = len(q["options"])
        perm = list(range(n_opts))
        rng.shuffle(perm)
        opt_perm.append(perm)
        public_questions.append(
            {
                "index": pos,
                "q": q["q"],
                "options": [q["options"][i] for i in perm],
                "topic": q.get("topic"),
                "difficulty": q.get("difficulty"),
            }
        )

    total_time = len(order) * SECONDS_PER_QUESTION + SUBMIT_GRACE_SECONDS
    attempt_token = _sign_attempt(user_id, quiz_id, order, opt_perm, total_time)

    return JSONResponse(
        {
            "attempt_token": attempt_token,
            "quiz_id": quiz_id,
            "title": quiz["title"],
            "seconds_per_question": SECONDS_PER_QUESTION,
            "total_seconds": total_time,
            "questions": public_questions,
        }
    )


async def submit_quiz(request: Request):
    """POST /api/quiz/{quiz_id}/submit
    🔒 Requires auth. Body: {"attempt_token": str, "answers": [int|null, ...]}

    `answers[i]` is the option index the user clicked for the question shown
    at position i (i.e. the *shuffled* position from /start, not the
    original question index). Grading un-shuffles via `opt_perm`/`order`
    from the token and compares against quiz_bank.py, entirely server-side
    — the client never tells us the score, only which buttons were clicked.
    """
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")

    quiz_id = request.path_params.get("quiz_id", "")
    quiz = get_quiz(quiz_id)
    if not quiz:
        return err(404, "Unknown quiz_id.")

    if quiz.get("required_sections"):
        missing = missing_required_sections(quiz, await storage.get_progress(user_id))
        if missing:
            return JSONResponse({"detail": "Complete the required course sections first.",
                                 "missing_sections": missing}, status_code=403)

    try:
        body = await request.json()
    except Exception:
        return err(400, "Invalid JSON.")

    attempt_token = body.get("attempt_token")
    answers = body.get("answers")
    if not attempt_token or not isinstance(answers, list):
        return err(400, "attempt_token and answers[] are required.")

    payload, error = _decode_attempt(attempt_token)
    if error:
        return error

    if payload["uid"] != user_id:
        return err(403, "This attempt token belongs to a different user.")
    if payload["quiz_id"] != quiz_id:
        return err(400, "attempt_token does not match quiz_id in URL.")

    order = payload["order"]
    opt_perm = payload["opt_perm"]
    questions = quiz["questions"]

    if len(answers) != len(order):
        return err(400, f"Expected {len(order)} answers, got {len(answers)}.")

    if any(answer is not None and (type(answer) is not int or answer < 0
           or answer >= len(opt_perm[pos])) for pos, answer in enumerate(answers)):
        return err(400, "Each answer must be a valid option index or null.")

    review = []
    score = 0
    for pos, q_idx in enumerate(order):
        correct_original_idx = questions[q_idx]["correct"]
        perm = opt_perm[pos]  # perm[shown_idx] -> original option idx
        correct_shown_idx = perm.index(correct_original_idx)

        shown_answer = answers[pos]
        is_correct = shown_answer is not None and shown_answer == correct_shown_idx
        if is_correct:
            score += 1

        review.append(
            {
                "index": pos,
                "correct": is_correct,
                "correct_option": correct_shown_idx,
                "your_answer": shown_answer,
            }
        )

    total = len(order)
    pct = round((score / total) * 100, 2) if total else 0
    threshold = quiz.get("min_pass_percent", MIN_PASS_PERCENT)
    passed = total > 0 and score * 100 >= threshold * total

    # Best score per quiz (used for certificate eligibility)...
    await storage.save_quiz_score(user_id, quiz_id, score, total)
    # ...and the full, permanent attempt history (never overwritten).
    await storage.record_quiz_attempt(user_id, quiz_id, score, total, passed)

    return JSONResponse(
        {
            "score": score,
            "total": total,
            "pct": pct,
            "passed": passed,
            "min_pass_percent": threshold,
            "review": review,
        }
    )


routes = [
    Route("/", list_scores, methods=["GET"]),
    Route("/", save_score, methods=["POST"]),
    Route("/attempts", list_attempts, methods=["GET"]),
    Route("/{quiz_id}/start", start_quiz, methods=["POST"]),
    Route("/{quiz_id}/submit", submit_quiz, methods=["POST"]),
]

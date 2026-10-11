"""
Certificate Generation, PDF Rendering & Verification (Dev 3 scope — Team Epsilon).

Design:
- Each issued certificate is BOTH a signed JWT (self-verifying, used by the
  QR code / public verify endpoint) AND a permanent database row (so score,
  issue date, and the certificate itself survive across sessions/devices).
- Before issuing, the user's best recorded quiz score for `quiz_id` is
  checked against `min_quiz_score` (default 80%) — the backend does not
  just trust the client's "I passed" claim.
- One certificate per (user, course): re-requesting after already passing
  returns/refreshes the same cert_id rather than creating a duplicate.
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.auth_utils import require_user, err, SECRET_KEY, ALGORITHM
from core import qr_utils
from core import pdf_utils
from core import storage
from core.quiz_bank import get_quiz, missing_required_sections

CERT_TOKEN_EXPIRE_DAYS = int(os.getenv("CERT_TOKEN_EXPIRE_DAYS", "3650"))
FRONTEND_VERIFY_URL = os.getenv(
    "FRONTEND_VERIFY_URL",
    "https://calculus-runtime-frontend-lyart.vercel.app/verify",
)
DEFAULT_MIN_QUIZ_SCORE = 80


def _normalize_issued_at(issued_at):
    """Convert Supabase timestamptz / ISO string / datetime into Unix epoch int."""
    if issued_at is None:
        return int(datetime.now(timezone.utc).timestamp())
    if isinstance(issued_at, (int, float)):
        return int(issued_at)
    if isinstance(issued_at, datetime):
        return int(issued_at.timestamp())
    # ISO string
    s = str(issued_at).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        return int(dt.timestamp())
    except Exception:
        return int(datetime.now(timezone.utc).timestamp())


def _sign_certificate(
    cert_id: str, user_id: int, full_name: str, course_id: str, course_title: str
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "cert_id": cert_id,
        "uid": user_id,
        "username": full_name,
        "course_id": course_id,
        "course_title": course_title,
        "iat": now,
        "exp": now + timedelta(days=CERT_TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


async def generate_certificate(request: Request):
    """POST /api/certificates/generate
    🔒 Requires auth.
    Body: {
      "course_id", "course_title",
      "full_name"?,        # defaults to username if omitted
      "quiz_id"?,          # if given, the user's best score for this quiz
      "min_quiz_score"?,   # is checked against this threshold (default 80)
    }

    Requires the user to have already passed the course's quiz (if
    quiz_id is supplied). Permanently stores the certificate (score,
    issue date, full name) and returns the signed token + QR code.
    """
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")

    try:
        body = await request.json()
    except Exception:
        return err(400, "Invalid JSON.")

    course_id = (body.get("course_id") or "").strip()
    course_title = (body.get("course_title") or "").strip()
    if not course_id or not course_title:
        return err(400, "course_id and course_title are required.")

    profile = await storage.get_user_profile(user_id)
    username = (profile or {}).get("username") or f"user-{user_id}"
    full_name = (body.get("full_name") or "").strip() or username

    quiz_id = (body.get("quiz_id") or "").strip() or None
    if course_id == "linear-algebra":
        quiz_id = "quiz-linear-algebra"
        quiz = get_quiz(quiz_id)
        course_title = "Linear Algebra"
        min_quiz_score = quiz["min_pass_percent"]
        missing = missing_required_sections(quiz, await storage.get_progress(user_id))
        if missing:
            return JSONResponse({"detail": "Complete the required course sections first.",
                                 "missing_sections": missing}, status_code=403)
    else:
        min_quiz_score = int(body.get("min_quiz_score") or DEFAULT_MIN_QUIZ_SCORE)

    score = None
    total = None
    if quiz_id:
        scores = await storage.list_quiz_scores(user_id)
        attempt = scores.get(quiz_id)
        if not attempt or not attempt.get("total"):
            return err(403, "You haven't passed this course's certification quiz yet.")
        score, total = attempt["score"], attempt["total"]
        pct = round(score / total * 100, 2)
        if score * 100 < min_quiz_score * total:
            return err(
                403,
                f"Quiz score {pct}% is below the required {min_quiz_score}%.",
            )

    # Reuse an existing cert_id for this user+course if one already exists,
    # so re-requesting doesn't spawn duplicate certificates.
    cert_id = uuid.uuid4().hex
    record = await storage.save_certificate_record(
        cert_id, user_id, course_id, course_title, full_name, score, total
    )
    cert_id = record["cert_id"]

    token = _sign_certificate(cert_id, user_id, full_name, course_id, course_title)
    verify_url = f"{FRONTEND_VERIFY_URL}?token={token}"

    return JSONResponse(
        {
            "cert_id": cert_id,
            "token": token,
            "verify_url": verify_url,
            "qr_svg": qr_utils.generate_qr_svg(verify_url),
            "qr_png_base64": qr_utils.generate_qr_png_data_uri(verify_url),
            "pdf_url": f"/api/certificates/{cert_id}/pdf",
            "full_name": full_name,
            "score": score,
            "total": total,
            "issued_at": datetime.now(timezone.utc).isoformat(),
        },
        status_code=201,
    )


async def list_my_certificates(request: Request):
    """GET /api/certificates/mine
    🔒 Requires auth. Every certificate the current user has earned, most
    recently issued first — this is what powers the "My Certificates" page.
    """
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")

    records = await storage.list_certificates_for_user(user_id)
    return JSONResponse(
        [
            {
                "cert_id": r["cert_id"],
                "course_id": r["course_id"],
                "course_title": r["course_title"],
                "full_name": r["full_name"],
                "score": r.get("score"),
                "total": r.get("total"),
                "issued_at": r["issued_at"],
                "pdf_url": f"/api/certificates/{r['cert_id']}/pdf",
            }
            for r in records
        ]
    )


async def get_my_certificate_for_course(request: Request):
    """GET /api/certificates/mine/{course_id}
    🔒 Requires auth. Returns existing certificate for a specific course if earned, else 404.
    """
    user_id = require_user(request)
    if not user_id:
        return err(401, "Not authenticated.")

    course_id = request.path_params.get("course_id", "")
    records = await storage.list_certificates_for_user(user_id)
    for r in records:
        if r.get("course_id") == course_id:
            return JSONResponse(
                {
                    "cert_id": r["cert_id"],
                    "course_id": r["course_id"],
                    "course_title": r["course_title"],
                    "full_name": r["full_name"],
                    "score": r.get("score"),
                    "total": r.get("total"),
                    "issued_at": r["issued_at"],
                    "pdf_url": f"/api/certificates/{r['cert_id']}/pdf",
                }
            )
    return err(404, "No certificate found for this course.")


async def download_certificate_pdf(request: Request):
    """GET /api/certificates/{cert_id}/pdf
    Public route — renders and streams the certificate as a downloadable PDF.
    """
    cert_id = request.path_params.get("cert_id", "")
    record = await storage.get_certificate_record(cert_id)
    if not record:
        return err(404, "Certificate not found.")

    # Normalize issued_at so PDF generation never crashes on Supabase strings
    issued_at = _normalize_issued_at(record.get("issued_at"))

    token = _sign_certificate(
        record["cert_id"],
        record["user_id"],
        record["full_name"],
        record["course_id"],
        record["course_title"],
    )
    verify_url = f"{FRONTEND_VERIFY_URL}?token={token}"
    qr_bytes = qr_utils.generate_qr_png_bytes(verify_url)

    pdf_bytes = pdf_utils.build_certificate_pdf(
        full_name=record["full_name"],
        course_title=record["course_title"],
        cert_id=record["cert_id"],
        issued_at_epoch=issued_at,
        verify_url=verify_url,
        qr_png_bytes=qr_bytes,
        score=record.get("score"),
        total=record.get("total"),
    )

    filename = f"certificate-{record['course_id']}-{record['cert_id'][:8]}.pdf"
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def verify_certificate(request: Request):
    """GET /api/certificates/verify?token=...
    Public route (no auth) — this is what scanning the QR opens.
    """
    token = request.query_params.get("token", "")
    if not token:
        return err(400, "Missing token.")

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        return JSONResponse({"valid": False, "reason": "expired"}, status_code=400)
    except Exception:
        return JSONResponse({"valid": False, "reason": "invalid_signature"}, status_code=400)

    return JSONResponse(
        {
            "valid": True,
            "cert_id": payload.get("cert_id"),
            "full_name": payload.get("username"),
            "course_id": payload.get("course_id"),
            "course_title": payload.get("course_title"),
            "issued_at": payload.get("iat"),
        }
    )


routes = [
    Route("/generate", generate_certificate, methods=["POST"]),
    Route("/mine", list_my_certificates, methods=["GET"]),
    Route("/mine/{course_id}", get_my_certificate_for_course, methods=["GET"]),
    Route("/verify", verify_certificate, methods=["GET"]),
    Route("/{cert_id}/pdf", download_certificate_pdf, methods=["GET"]),
]

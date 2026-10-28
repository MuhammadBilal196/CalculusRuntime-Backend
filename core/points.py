"""Leaderboard points — one set of rules for every user.

The frontend shows the same table on the leaderboard page
(src/utils/points.js), so keep the two in sync.

    Topic quiz / practice drill   1 point per correct answer (best attempt)
    Timed mock exam               2 points per correct answer (best attempt)
    Certification exam            2 points per correct answer (best attempt)
    Module part completed         20 points
    Course certificate earned     200 points
"""

from typing import Any, Dict, Iterable, Optional

QUIZ_POINTS_PER_CORRECT = 1
MOCK_POINTS_PER_CORRECT = 2
EXAM_POINTS_PER_CORRECT = 2
POINTS_PER_SECTION = 20
POINTS_PER_CERTIFICATE = 200


def quiz_kind(quiz_id: str, certification_ids: Optional[set] = None) -> str:
    """'mock', 'exam' or 'quiz' for a stored quiz id."""
    quiz_id = quiz_id or ""
    if quiz_id.startswith("mock-") or quiz_id.startswith("mock:"):
        return "mock"
    if (certification_ids and quiz_id in certification_ids) or quiz_id.startswith("quiz-"):
        return "exam"
    return "quiz"


def compute_points(
    quiz_rows: Iterable[Dict[str, Any]],
    sections_completed: int,
    certificates: int,
    certification_ids: Optional[set] = None,
) -> Dict[str, int]:
    """Points breakdown from a user's best quiz scores, sections and certificates."""
    quiz_points = mock_points = exam_points = 0
    correct = answered = quiz_count = 0
    pct_sum = 0.0

    for row in quiz_rows:
        score = max(0, int(row.get("score") or 0))
        total = max(0, int(row.get("total") or 0))
        if total:
            score = min(score, total)
            pct_sum += score * 100.0 / total
            quiz_count += 1
        correct += score
        answered += total
        kind = quiz_kind(row.get("quiz_id", ""), certification_ids)
        if kind == "mock":
            mock_points += score * MOCK_POINTS_PER_CORRECT
        elif kind == "exam":
            exam_points += score * EXAM_POINTS_PER_CORRECT
        else:
            quiz_points += score * QUIZ_POINTS_PER_CORRECT

    module_points = max(0, int(sections_completed or 0)) * POINTS_PER_SECTION
    course_points = max(0, int(certificates or 0)) * POINTS_PER_CERTIFICATE

    return {
        "points": quiz_points + mock_points + exam_points + module_points + course_points,
        "quizPoints": quiz_points,
        "mockPoints": mock_points,
        "examPoints": exam_points,
        "modulePoints": module_points,
        "coursePoints": course_points,
        "correctAnswers": correct,
        "questionsAnswered": answered,
        "quizCount": quiz_count,
        "quizPct": int(round(pct_sum / quiz_count)) if quiz_count else 0,
        "topics": max(0, int(sections_completed or 0)),
        "certificates": max(0, int(certificates or 0)),
    }

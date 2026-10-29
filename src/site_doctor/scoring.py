"""Rule-based scores. Claude never sets these, so the same site always gets the same grade."""

from statistics import mean

from .models import CATEGORIES, IssueGroup

PENALTY = {"critical": 25, "high": 10, "medium": 4, "low": 1}
GRADES = ((90, "A"), (80, "B"), (70, "C"), (60, "D"))


def score(groups: list[IssueGroup]) -> tuple[dict[str, int], int, str]:
    """Return (score per category, overall score, letter grade)."""
    raw = dict.fromkeys(CATEGORIES, 100.0)
    for g in groups:
        # An issue on many pages costs more, up to double.
        spread = min(1 + 0.1 * (len(g.urls) - 1), 2)
        raw[g.category] -= PENALTY[g.severity] * spread
    scores = {c: round(max(v, 0)) for c, v in raw.items()}
    overall = round(mean(scores.values()))
    grade = next((letter for floor, letter in GRADES if overall >= floor), "F")
    return scores, overall, grade

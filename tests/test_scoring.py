import pytest

from site_doctor.models import CATEGORIES, IssueGroup
from site_doctor.scoring import score


def g(sev, n=1, cat="security", gid="x"):
    return IssueGroup(gid, cat, sev, "t", [f"u{i}" for i in range(n)], [], [])


def test_no_issues_is_perfect():
    scores, overall, grade = score([])
    assert set(scores) == set(CATEGORIES) and overall == 100 and grade == "A"


@pytest.mark.parametrize("groups,expected", [
    ([g("critical")], 75),
    ([g("high", n=11)], 80),
    ([g("high", n=21)], 80),
    ([g("critical", gid=str(i)) for i in range(5)], 0),
])
def test_category_penalties(groups, expected):
    assert score(groups)[0]["security"] == expected


def test_grade_boundaries():
    assert score([g("low")])[2] == "A"
    # security at 25, six categories at 100 → mean 89.3 → 89 → "B"
    assert score([g("critical", gid=str(i)) for i in range(3)])[2] == "B"


def test_other_categories_untouched():
    scores, _, _ = score([g("high", cat="seo")])
    assert scores["seo"] == 90 and scores["security"] == 100

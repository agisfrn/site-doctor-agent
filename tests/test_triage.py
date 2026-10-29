from site_doctor.models import Finding
from site_doctor.triage import group_findings


def test_grouping_merges_by_id_and_sorts():
    fs = [Finding("security.missing-csp", "security", "medium", "No CSP", f"u{i}")
          for i in range(3)]
    fs += [Finding("basics.js-error", "basics", "high", "JS error", "u0", "boom")]
    groups = group_findings(fs)
    assert [x.id for x in groups] == ["basics.js-error", "security.missing-csp"]
    assert groups[1].urls == ["u0", "u1", "u2"]


def test_group_takes_the_worst_severity_and_dedupes():
    fs = [Finding("basics.page-load-failed", "basics", "high", "Failed", "u1", "e1", ["a.png"]),
          Finding("basics.page-load-failed", "basics", "critical", "Failed", "u0", "e1", ["a.png"]),
          Finding("basics.page-load-failed", "basics", "high", "Failed", "u1", "e2")]
    [grp] = group_findings(fs)
    assert grp.severity == "critical"
    assert grp.urls == ["u1", "u0"] and grp.evidence == ["e1", "e2"]
    assert grp.screenshots == ["a.png"]


def test_evidence_is_capped_at_five():
    fs = [Finding("x.y", "seo", "low", "t", f"u{i}", f"e{i}") for i in range(8)]
    assert len(group_findings(fs)[0].evidence) == 5

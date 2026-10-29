from site_doctor.checks import accessibility
from tests.checks.helpers import make_capture


def test_axe_impacts_map_to_severities():
    cap = make_capture(axe_violations=[
        {"id": "image-alt", "impact": "critical", "help": "Images need alt", "nodes": 2},
        {"id": "color-contrast", "impact": "serious", "help": "Contrast", "nodes": 5},
        {"id": "region", "impact": None, "help": "Landmarks", "nodes": 1},
    ])
    assert [(f.id, f.severity, f.evidence) for f in accessibility.check(cap)] == [
        ("accessibility.image-alt", "critical", "2 element(s)"),
        ("accessibility.color-contrast", "high", "5 element(s)"),
        ("accessibility.region", "low", "1 element(s)"),
    ]

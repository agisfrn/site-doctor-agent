import json
from pathlib import Path

import pytest

from site_doctor.config import ScanConfig, normalize_target
from site_doctor.models import CATEGORIES, Finding, PageResult, ScanResult, Usage


def test_scan_result_round_trips_through_json():
    f = Finding("seo.missing-description", "seo", "medium", "No meta description", "https://a.com/")
    r = ScanResult(
        target="https://a.com/", model="claude-opus-5-5", started_at="2026-10-29T10:00:00",
        duration_s=1.5, pages=[PageResult("https://a.com/", 200, "A", 0.4, 900.0, [f])],
        findings=[f], groups=[], scores={c: 100 for c in CATEGORIES}, overall=100,
        grade="A", summary="ok", usage=Usage(10, 20),
    )
    assert ScanResult.from_dict(json.loads(json.dumps(r.to_dict()))) == r


@pytest.mark.parametrize("raw,expected", [
    ("  example.com/ ", "https://example.com/"),
    ("http://x.com", "http://x.com"),
    ("https://x.com/a?b=1", "https://x.com/a?b=1"),
])
def test_normalize_target(raw, expected):
    assert normalize_target(raw) == expected


def test_run_dir_uses_host_and_stamp():
    cfg = ScanConfig(url="http://127.0.0.1:8123/", stamp="20261029-100000")
    assert cfg.run_dir == Path("reports") / "127.0.0.1-8123-20261029-100000"

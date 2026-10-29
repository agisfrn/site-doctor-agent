import json

import pytest

from site_doctor.models import Usage
from site_doctor.report import estimate_cost, render_html, write_report


def test_report_contains_grade_issues_and_escapes(tmp_path, sample_result):
    sample_result.groups[0].title = "<script>alert(1)</script>"
    html = write_report(sample_result, tmp_path).read_text(encoding="utf-8")
    assert sample_result.grade in html and "&lt;script&gt;" in html
    assert "<script>alert(1)" not in html
    assert all(g.id in html for g in sample_result.groups)
    data = json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))
    assert data["grade"] == sample_result.grade


def test_report_shows_explanations_screenshots_and_pages(sample_result):
    html = render_html(sample_result)
    assert "Define undefinedFn." in html and "Mostly healthy" in html
    assert 'src="screenshots/01-home-desktop.png"' in html
    assert "https://a.com/about" in html and "$0.66" in html


def test_report_is_self_contained(sample_result):
    html = render_html(sample_result)
    assert "http://cdn" not in html and "https://cdn" not in html and "<link" not in html


def test_partial_banner(sample_result):
    sample_result.partial, sample_result.partial_reason = True, "interrupted"
    assert "interrupted" in render_html(sample_result)


def test_estimate_cost():
    assert estimate_cost("claude-opus-5-5", Usage(1_000_000, 100_000)) == pytest.approx(6.0)
    assert estimate_cost("unknown-model", Usage(1, 1)) is None


def test_fast_lcp_is_shown_in_milliseconds(sample_result):
    html = render_html(sample_result)
    assert "980 ms" in html and "0.0s" not in html


def test_estimate_cost_prices_cache_tokens():
    usage = Usage(input_tokens=0, output_tokens=0, cache_read_input_tokens=1_000_000,
                  cache_creation_input_tokens=1_000_000)
    assert estimate_cost("claude-opus-5-5", usage) == pytest.approx(0.20 + 5.00)


def test_old_findings_json_without_cache_fields_still_loads(sample_result):
    from site_doctor.models import ScanResult

    data = sample_result.to_dict()
    data["usage"] = {"input_tokens": 5, "output_tokens": 6}
    assert ScanResult.from_dict(data).usage.cache_read_input_tokens == 0


def test_report_site_map_and_long_page_lists(sample_result):
    from site_doctor.models import PageResult

    sample_result.mode, sample_result.links_checked = "full", 1340
    sample_result.page_groups = [{"pattern": "/products/*", "pages": 30, "issues": 12}]
    sample_result.pages = [PageResult(f"https://a.com/p{i}", 200, "", 0.1, None, [])
                           for i in range(60)]
    html = render_html(sample_result)
    assert "Site map" in html and "/products/*" in html
    assert "Full scan" in html and "1,340 links checked" in html
    assert "Show all 60 pages" in html


def test_smart_scan_has_no_site_map(sample_result):
    sample_result.mode = "smart"
    html = render_html(sample_result)
    assert "Smart scan" in html and "Site map" not in html

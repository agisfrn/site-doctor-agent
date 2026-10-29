from site_doctor.checks import seo
from tests.checks.helpers import make_capture


def test_clean_capture_has_no_seo_findings():
    assert seo.check(make_capture()) == []


def test_seo_page_rules():
    cap = make_capture(meta={**make_capture().meta, "description": None, "h1_count": 2})
    assert {f.id for f in seo.check(cap)} == {"seo.missing-description", "seo.h1-count"}


def test_missing_canonical_and_og():
    cap = make_capture(meta={**make_capture().meta, "canonical": None, "og_image": None})
    assert {f.id for f in seo.check(cap)} == {"seo.missing-canonical", "seo.missing-og"}


def test_seo_site_rules():
    fetch = {"https://a.com/robots.txt": 200, "https://a.com/sitemap.xml": 404}.get
    [f] = seo.check_site("https://a.com", fetch)
    assert (f.id, f.url) == ("seo.missing-sitemap", "https://a.com/sitemap.xml")


def test_unreachable_robots_counts_as_missing():
    fetch = {"https://a.com/robots.txt": "Error: timeout", "https://a.com/sitemap.xml": 200}.get
    assert [f.id for f in seo.check_site("https://a.com", fetch)] == ["seo.missing-robots"]

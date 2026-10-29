import pytest

from site_doctor.browser import Browser
from site_doctor.demo_server import serve


@pytest.fixture(scope="session")
def demo_url():
    with serve() as url:
        yield url


@pytest.fixture(scope="session")
def browser():
    with Browser() as b:
        yield b


@pytest.fixture
def sample_result():
    from site_doctor.models import Finding, PageResult, ScanResult, Usage
    from site_doctor.scoring import score
    from site_doctor.triage import group_findings

    findings = [
        Finding("basics.js-error", "basics", "high", "Uncaught JavaScript error",
                "https://a.com/", "ReferenceError: undefinedFn is not defined",
                ["screenshots/01-home-desktop.png"]),
        Finding("security.missing-csp", "security", "medium", "No Content-Security-Policy header",
                "https://a.com/"),
        Finding("security.missing-csp", "security", "medium", "No Content-Security-Policy header",
                "https://a.com/about"),
        Finding("links.broken-internal", "links", "high", "Broken internal link",
                "https://a.com/missing", "HTTP 404 · linked from https://a.com/"),
    ]
    groups = group_findings(findings)
    groups[0].explanation, groups[0].fix = "Scripts crash on load.", "Define undefinedFn."
    scores, overall, grade = score(groups)
    pages = [PageResult("https://a.com/", 200, "Home", 0.42, 980.0, findings[:2]),
             PageResult("https://a.com/about", 200, "About", 0.31, None, findings[2:3])]
    return ScanResult(target="https://a.com/", model="claude-opus-5-5",
                      started_at="2026-10-29T10:00:00", duration_s=42.0, pages=pages,
                      findings=findings, groups=groups, scores=scores, overall=overall,
                      grade=grade, summary="Mostly healthy; fix the JS error first.",
                      usage=Usage(120_000, 9_000))


@pytest.fixture(scope="session")
def crawl_url():
    from tests.crawl_site import crawl_routes
    from tests.servers import serve_routes

    with serve_routes(crawl_routes()) as url:
        yield url

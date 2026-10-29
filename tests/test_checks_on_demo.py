"""End to end: real captures of the demo site through every check."""

from site_doctor.checks import run_page_checks

PLANTED = [
    "basics.js-error", "basics.console-error",
    "accessibility.image-alt", "accessibility.color-contrast", "accessibility.label",
    "seo.missing-description", "seo.h1-count",
    "performance.oversized-image", "performance.cls-high",
    "mobile.horizontal-scroll", "mobile.small-tap-targets", "mobile.missing-viewport",
]


def test_planted_bugs_are_found(browser, demo_url):
    ids = {f.id for page in ["index", "about", "products", "contact"]
           for f in run_page_checks(browser.capture(f"{demo_url}/{page}.html"))}
    missing = [p for p in PLANTED if p not in ids]
    assert missing == []


def test_clean_page_only_has_header_findings(browser, demo_url):
    ids = {f.id for f in run_page_checks(browser.capture(demo_url + "/clean.html"))}
    assert ids <= {"security.missing-csp", "security.missing-nosniff",
                   "security.missing-frame-protection"}

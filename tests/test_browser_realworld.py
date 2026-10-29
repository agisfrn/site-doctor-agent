"""Regression tests for real-world pages the demo site doesn't exercise (final review fixes)."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from site_doctor.browser import Browser, NotAPage
from site_doctor.demo_server import DEMO_DIR
from tests.servers import serve_routes

INDEX = (DEMO_DIR / "index.html").read_text(encoding="utf-8")
HERO = (DEMO_DIR / "hero.png").read_bytes()


@pytest.mark.parametrize("path,ctype,body", [
    ("/photo.png", "image/png", HERO),
    ("/style.css", "text/css", "body { color: red }"),
    ("/feed.xml", "application/xml", "<rss></rss>"),
    ("/data.json", "application/json", '{"a": 1}'),
])
def test_non_html_responses_are_not_pages(browser, path, ctype, body):
    with serve_routes({path: {"headers": {"Content-Type": ctype}, "body": body}}) as url:
        with pytest.raises(NotAPage) as e:
            browser.capture(url + path)
    assert e.value.status == 200


def test_downloads_are_not_pages(browser):
    pdf = {"headers": {"Content-Type": "application/pdf",
                       "Content-Disposition": "attachment; filename=b.pdf"}, "body": b"%PDF-1.4"}
    with serve_routes({"/brochure.pdf": pdf}) as url:
        with pytest.raises(NotAPage):
            browser.capture(url + "/brochure.pdf")


def test_axe_runs_despite_a_strict_csp(browser):
    route = {"headers": {"Content-Type": "text/html",
                         "Content-Security-Policy": "default-src 'self'; script-src 'self'"},
             "body": INDEX}
    with serve_routes({"/": route}) as url:
        cap = browser.capture(url + "/")
    # Before the fix axe was blocked and this was empty. (The strict CSP also blocks the page's
    # inline styles, so the low-contrast text is gone; that's real browser behaviour.)
    assert "image-alt" in {v["id"] for v in cap.axe_violations}


def test_a_stalled_image_does_not_fail_the_page():
    page = '<!doctype html><title>Slow</title><h1>Hi</h1><img src="/hang.png" alt="x">'
    routes = {"/": {"body": page},
              "/hang.png": {"headers": {"Content-Type": "image/png"}, "body": HERO, "delay": 8}}

    def scan(url):  # its own thread: Playwright allows one sync instance per thread
        with Browser(mobile=False, timeout_ms=3000) as b:
            return b.capture(url + "/")

    with serve_routes(routes) as url, ThreadPoolExecutor(1) as pool:
        cap = pool.submit(scan, url).result()
    assert cap.status == 200 and cap.title == "Slow"


def test_cross_origin_asset_sizes_are_measured(browser):
    image = {"/hero.png": {"headers": {"Content-Type": "image/png"}, "body": HERO}}
    with serve_routes(image) as cdn:
        cdn = cdn.replace("127.0.0.1", "localhost")  # a different origin, no Timing-Allow-Origin
        page = f'<!doctype html><title>T</title><img src="{cdn}/hero.png" width="200" alt="h">'
        with serve_routes({"/": {"body": page}}) as site:
            cap = browser.capture(site + "/")
    sizes = {r.url: r.transfer_size for r in cap.resources}
    assert sizes[f"{cdn}/hero.png"] >= len(HERO)

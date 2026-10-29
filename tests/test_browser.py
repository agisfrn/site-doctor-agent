import pytest

from site_doctor.browser import PageLoadError


def test_capture_collects_page_data(browser, demo_url):
    cap = browser.capture(demo_url + "/index.html")
    assert cap.status == 200 and cap.title
    assert any("undefinedFn" in e for e in cap.page_errors)
    assert demo_url + "/missing-page" in cap.links
    assert cap.meta["h1_count"] == 2 and cap.meta["description"] is None
    assert cap.timings.load_ms is not None and cap.load_time_s > 0
    assert cap.mobile is not None and cap.mobile.viewport_width == 390


def test_capture_dead_host_raises(browser):
    with pytest.raises(PageLoadError):
        browser.capture("http://127.0.0.1:9/")


def test_fetch_status(browser, demo_url):
    assert browser.fetch_status(demo_url + "/missing-page") == 404
    assert browser.fetch_status(demo_url + "/error") == 500
    assert isinstance(browser.fetch_status("https://broken.example.invalid/"), str)


def test_screenshot_writes_png(browser, demo_url, tmp_path):
    p = browser.screenshot(demo_url + "/clean.html", tmp_path / "s.png", mobile=True)
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_load_time_excludes_the_network_idle_wait(browser, demo_url):
    # networkidle needs 500ms of silence; that's not part of how long the page took to load
    assert browser.capture(demo_url + "/clean.html").load_time_s < 0.45

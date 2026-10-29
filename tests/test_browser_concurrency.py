import _thread
import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from site_doctor.browser import Browser, NotAPage, PageLoadError
from tests.servers import serve_routes


def test_capture_many_returns_every_page(browser, demo_url):
    urls = [f"{demo_url}/{p}" for p in ["", "about.html", "products.html", "contact.html",
                                        "clean.html", "index.html"]]
    results = browser.capture_many(urls)
    assert set(results) == set(urls)
    assert all(not isinstance(r, Exception) and r.status == 200 for r in results.values())


def test_one_bad_url_does_not_sink_the_batch(browser, demo_url):
    dead, image = "http://127.0.0.1:9/", demo_url + "/hero.png"
    results = browser.capture_many([demo_url + "/", dead, image])
    assert results[demo_url + "/"].status == 200
    assert isinstance(results[dead], PageLoadError)
    assert isinstance(results[image], NotAPage)


def timed_capture_many(urls):
    with Browser(mobile=False, concurrency=4) as b:
        start = time.perf_counter()
        results = b.capture_many(urls)
        assert all(r.status == 200 for r in results.values())
        return time.perf_counter() - start


def test_capture_many_is_concurrent():
    # 4 pages that each take 2s to answer: one at a time is 8s+ (plus ~1s of checks per page);
    # with concurrency 4 it's ~3.5s. The margin keeps this stable on a busy machine.
    routes = {f"/p{i}": {"body": "<title>p</title>", "delay": 2} for i in range(4)}
    with serve_routes(routes) as url, ThreadPoolExecutor(1) as pool:
        elapsed = pool.submit(timed_capture_many, [f"{url}/p{i}" for i in range(4)]).result()
    assert elapsed < 6


def test_fetch_many_and_fetch_text(browser, demo_url):
    urls = [demo_url + "/missing-page", demo_url + "/error", demo_url + "/"]
    assert browser.fetch_many(urls) == dict(zip(urls, [404, 500, 200], strict=True))
    status, text = browser.fetch_text(demo_url + "/clean.html")
    assert status == 200 and "Shipping" in text
    status, text = browser.fetch_text("http://127.0.0.1:9/robots.txt")
    assert isinstance(status, str) and text == ""


def test_call_is_interruptible(browser):
    timer = threading.Timer(0.3, _thread.interrupt_main)
    timer.start()
    start = time.perf_counter()
    with pytest.raises(KeyboardInterrupt):
        browser._call(asyncio.sleep(10))
    assert time.perf_counter() - start < 2
    timer.cancel()


def test_browser_ignores_console_signals_and_exits_cleanly(monkeypatch):
    # Ctrl+C must reach Python only: if the Node driver also gets it, it kills the browser,
    # closing fails, and the partial report is never written.
    from site_doctor import browser as browser_mod

    real_stop = browser_mod._Engine.stop

    async def stop_then_fail(self):
        await real_stop(self)
        raise RuntimeError("Connection closed while reading from the driver")

    monkeypatch.setattr(browser_mod._Engine, "stop", stop_then_fail)
    with Browser(mobile=False) as b:
        assert b._engine.launch_options["handle_sigint"] is False
    # leaving the block didn't raise

"""Playwright capture of everything the checks need about a page.

The async Playwright API runs on a background event-loop thread so several pages load at once,
while callers use the plain synchronous Browser at the bottom of this file.
"""

from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, async_playwright

from .models import Image, MobileData, PageCapture, Resource, Timings

DESKTOP = {"viewport": {"width": 1280, "height": 800}}
MOBILE = {
    "viewport": {"width": 390, "height": 844},
    "is_mobile": True,
    "has_touch": True,
    "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 "
                  "(KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
}

PAGE_DATA_JS = """() => {
  const attr = (sel, a) => {
    const el = document.querySelector(sel);
    const v = el && (el.getAttribute(a) || "").trim();
    return v || null;
  };
  const nav = performance.getEntriesByType("navigation")[0];
  const resources = performance.getEntriesByType("resource").map(r => ({
    url: r.name, type: r.initiatorType,
    transfer_size: Math.round(r.transferSize || r.encodedBodySize || 0),
  }));
  if (nav) {
    const size = Math.round(nav.transferSize || nav.encodedBodySize || 0);
    resources.push({url: location.href, type: "document", transfer_size: size});
  }
  return {
    meta: {
      description: attr('meta[name="description"]', "content"),
      canonical: attr('link[rel="canonical"]', "href"),
      og_title: attr('meta[property="og:title"]', "content"),
      og_image: attr('meta[property="og:image"]', "content"),
      viewport: attr('meta[name="viewport"]', "content"),
      h1_count: document.querySelectorAll("h1").length,
    },
    links: [...new Set([...document.querySelectorAll("a[href]")]
      .map(a => a.href.split("#")[0]).filter(h => /^https?:/.test(h)))],
    images: [...document.images].map(i => {
      const r = i.getBoundingClientRect();
      return {src: i.currentSrc || i.src, natural_w: i.naturalWidth, natural_h: i.naturalHeight,
              rendered_w: Math.round(r.width), rendered_h: Math.round(r.height)};
    }),
    resources,
  };
}"""

TIMINGS_JS = """() => new Promise(resolve => {
  const nav = performance.getEntriesByType("navigation")[0];
  let lcp = null, cls = null;
  try {
    new PerformanceObserver(list => {
      const e = list.getEntries();
      if (e.length) lcp = e[e.length - 1].startTime;
    }).observe({type: "largest-contentful-paint", buffered: true});
  } catch (e) {}
  try {
    cls = 0;
    new PerformanceObserver(list => {
      for (const e of list.getEntries()) if (!e.hadRecentInput) cls += e.value;
    }).observe({type: "layout-shift", buffered: true});
  } catch (e) { cls = null; }
  setTimeout(() => resolve({
    ttfb_ms: nav ? nav.responseStart : null,
    load_ms: nav && nav.loadEventEnd > 0 ? nav.loadEventEnd : null,
    lcp_ms: lcp, cls: cls,
  }), 100);
})"""

# A target is too small when it's under 24x24 CSS px, or shorter than 12px whatever its width.
# (Normal 16px inline text links are ~18px tall; flagging those would flag every page.)
MOBILE_JS = """() => {
  const small = [...document.querySelectorAll("a, button, input, select")].filter(el => {
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return false;
    return (r.width < 24 && r.height < 24) || r.height < 12;
  }).length;
  // screen.width, not innerWidth: phones zoom out to fit wide pages, which grows innerWidth.
  return {doc_width: document.documentElement.scrollWidth, viewport_width: screen.width,
          small_tap_targets: small};
}"""


class PageLoadError(Exception):
    """The page could not be loaded at all."""


class NotAPage(PageLoadError):
    """The URL answered, but with something that isn't an HTML page (image, PDF, feed...)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _first_line(e: BaseException) -> str:
    text = str(e).strip()
    return text.splitlines()[0] if text else type(e).__name__


AXE_PATH = Path(__file__).parent / "checks" / "vendor" / "axe.min.js"
AXE_JS = """async () => {
  const r = await axe.run(document, {resultTypes: ["violations"]});
  return r.violations.map(v => ({id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length}));
}"""


HTML_TYPES = {"text/html", "application/xhtml+xml"}
MAX_SIZED_REQUESTS = 300


@functools.cache
def _axe_source() -> str:
    return AXE_PATH.read_text(encoding="utf-8")


async def run_axe(page: Page) -> list[dict]:
    # Evaluated through the DevTools protocol, not a <script> tag, so the page's own
    # Content-Security-Policy can't block it.
    try:
        await page.evaluate(_axe_source())
        return await page.evaluate(AXE_JS)
    except PlaywrightError:
        return []


async def _settle(page: Page, timeout_ms: int, start: float | None = None) -> float:
    """Best-effort waits after the document arrives. Returns seconds from `start` to load."""
    start = time.perf_counter() if start is None else start
    load_time = None
    for state, timeout in (("load", timeout_ms), ("networkidle", 5000)):
        try:
            await page.wait_for_load_state(state, timeout=timeout)
        except PlaywrightError:
            pass
        if load_time is None:  # the clock stops at the load event, not after network idle
            load_time = time.perf_counter() - start
    return load_time


class _Engine:
    """All the async Playwright work. Lives on the Browser's event-loop thread."""

    def __init__(self, mobile: bool, timeout_ms: int, concurrency: int):
        self.mobile = mobile
        self.timeout_ms = timeout_ms
        self.concurrency = concurrency

    async def start(self) -> None:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(channel="chromium")
        self._ctx = await self._browser.new_context(**DESKTOP)
        self._mobile_ctx = await self._browser.new_context(**MOBILE) if self.mobile else None
        self._tabs = asyncio.Semaphore(self.concurrency)
        self._fetches = asyncio.Semaphore(10)
        self._per_host: dict[str, asyncio.Semaphore] = defaultdict(lambda: asyncio.Semaphore(2))

    async def stop(self) -> None:
        await self._browser.close()
        await self._pw.stop()

    async def _new_page(self, mobile: bool = False) -> Page:
        page = await (self._mobile_ctx if mobile else self._ctx).new_page()
        page.set_default_timeout(self.timeout_ms)
        return page

    # --- pages ------------------------------------------------------------------------------

    async def capture(self, url: str) -> PageCapture:
        async with self._tabs:
            page = await self._new_page()
            console, errors, requests, finished = [], [], [], []
            page.on("console", lambda m: m.type == "error" and console.append(m.text))
            page.on("pageerror", lambda e: errors.append(_first_line(e)))
            page.on("request", lambda r: requests.append(r.url))
            page.on("requestfinished", lambda r: finished.append(r))
            start = time.perf_counter()
            try:
                try:
                    # Only the document has to arrive; a stalled image or widget mustn't fail it.
                    response = await page.goto(url, wait_until="domcontentloaded")
                except PlaywrightError as e:
                    if "Download is starting" in str(e):
                        raise NotAPage("a file download, not a page") from e
                    raise PageLoadError(_first_line(e)) from e
                if response:
                    ctype = response.headers.get("content-type", "").split(";")[0].strip().lower()
                    if ctype and ctype not in HTML_TYPES:
                        raise NotAPage(f"not an HTML page ({ctype})", response.status)
                load_time = round(await _settle(page, self.timeout_ms, start), 2)

                data = await page.evaluate(PAGE_DATA_JS)
                await self._fill_sizes(data["resources"], finished)
                capture = PageCapture(
                    url=url,
                    final_url=page.url,
                    status=response.status if response else None,
                    headers=await response.all_headers() if response else {},
                    title=await page.title(),
                    meta=data["meta"],
                    links=data["links"],
                    console_errors=list(console),
                    page_errors=list(errors),
                    resources=[Resource(**r) for r in data["resources"]],
                    requested_urls=list(requests),
                    images=[Image(**i) for i in data["images"]],
                    timings=Timings(**await page.evaluate(TIMINGS_JS)),
                    axe_violations=await run_axe(page),
                    mobile=None,
                    load_time_s=load_time,
                )
            finally:
                await page.close()
        capture.mobile = await self._capture_mobile(url)
        return capture

    async def capture_or_error(self, url: str) -> PageCapture | PageLoadError:
        try:
            return await self.capture(url)
        except PageLoadError as e:
            return e
        except PlaywrightError as e:
            return PageLoadError(_first_line(e))

    async def capture_many(self, urls: list[str]) -> dict[str, PageCapture | PageLoadError]:
        results = await asyncio.gather(*(self.capture_or_error(u) for u in urls))
        return dict(zip(urls, results, strict=True))

    @staticmethod
    async def _fill_sizes(resources: list[dict], finished: list) -> None:
        """Resource Timing reports 0 bytes for cross-origin (CDN) assets; ask the network."""
        missing = {r["url"] for r in resources if not r["transfer_size"]}
        for request in finished[:MAX_SIZED_REQUESTS]:
            if request.url not in missing:
                continue
            try:
                size = (await request.sizes()).get("responseBodySize", 0)
            except PlaywrightError:
                continue
            for r in resources:
                if r["url"] == request.url and not r["transfer_size"]:
                    r["transfer_size"] = max(size, 0)

    async def _capture_mobile(self, url: str) -> MobileData | None:
        if not self._mobile_ctx:
            return None
        async with self._tabs:
            page = await self._new_page(mobile=True)
            try:
                await page.goto(url, wait_until="domcontentloaded")
                try:
                    await page.wait_for_load_state("load", timeout=self.timeout_ms)
                except PlaywrightError:
                    pass
                return MobileData(**await page.evaluate(MOBILE_JS))
            except PlaywrightError:
                return None
            finally:
                await page.close()

    async def screenshot(self, url: str, path: Path, mobile: bool) -> Path:
        async with self._tabs:
            page = await self._new_page(mobile=mobile and self._mobile_ctx is not None)
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await _settle(page, self.timeout_ms)
                path.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(path), full_page=True)
            finally:
                await page.close()
        return path

    # --- plain HTTP ---------------------------------------------------------------------------

    async def _get(self, url: str):
        host = urlsplit(url).netloc.lower()
        async with self._fetches, self._per_host[host]:
            return await self._ctx.request.get(url, timeout=15_000, max_redirects=5)

    async def fetch_status(self, url: str) -> int | str:
        try:
            response = await self._get(url)
            status = response.status
            await response.dispose()
            return status
        except Exception as e:
            return f"{type(e).__name__}: {_first_line(e)}"

    async def fetch_many(self, urls: list[str]) -> dict[str, int | str]:
        results = await asyncio.gather(*(self.fetch_status(u) for u in urls))
        return dict(zip(urls, results, strict=True))

    async def fetch_text(self, url: str) -> tuple[int | str, str]:
        try:
            response = await self._get(url)
            status, text = response.status, await response.text()
            await response.dispose()
            return status, text
        except Exception as e:
            return f"{type(e).__name__}: {_first_line(e)}", ""


class Browser:
    """Synchronous front door to the async engine, for ordinary (non-async) code."""

    def __init__(self, mobile: bool = True, timeout_ms: int = 30_000, concurrency: int = 4):
        self.mobile = mobile
        self.timeout_ms = timeout_ms
        self.concurrency = concurrency

    def __enter__(self) -> Browser:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True,
                                        name="site-doctor-browser")
        self._thread.start()
        self._engine = _Engine(self.mobile, self.timeout_ms, self.concurrency)
        try:
            self._call(self._engine.start())
        except BaseException:
            self._shutdown_loop()
            raise
        return self

    def __exit__(self, *exc) -> None:
        try:
            self._call(self._engine.stop())
        finally:
            self._shutdown_loop()

    def _shutdown_loop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        if not self._thread.is_alive():
            self._loop.close()

    def _call(self, coro):
        """Run a coroutine on the engine's loop. Polls, so Ctrl+C lands within a fraction of
        a second instead of after the current page finishes."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            while True:
                try:
                    return future.result(timeout=0.2)
                except TimeoutError:
                    continue
        except KeyboardInterrupt:
            future.cancel()
            raise

    def capture(self, url: str) -> PageCapture:
        return self._call(self._engine.capture(url))

    def capture_many(self, urls: list[str]) -> dict[str, PageCapture | PageLoadError]:
        return self._call(self._engine.capture_many(urls))

    def fetch_status(self, url: str) -> int | str:
        return self._call(self._engine.fetch_status(url))

    def fetch_many(self, urls: list[str]) -> dict[str, int | str]:
        return self._call(self._engine.fetch_many(urls))

    def fetch_text(self, url: str) -> tuple[int | str, str]:
        return self._call(self._engine.fetch_text(url))

    def screenshot(self, url: str, path: Path, mobile: bool = False) -> Path:
        return self._call(self._engine.screenshot(url, path, mobile))

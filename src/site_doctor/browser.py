"""A thin Playwright wrapper that records everything the checks need about a page."""

from __future__ import annotations

import time
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

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
  return {doc_width: document.documentElement.scrollWidth, viewport_width: window.innerWidth,
          small_tap_targets: small};
}"""


class PageLoadError(Exception):
    """The page could not be loaded at all."""


def _first_line(e: BaseException) -> str:
    text = str(e).strip()
    return text.splitlines()[0] if text else type(e).__name__


def run_axe(page: Page) -> list[dict]:
    return []


class Browser:
    def __init__(self, mobile: bool = True, timeout_ms: int = 30_000):
        self.mobile = mobile
        self.timeout_ms = timeout_ms

    def __enter__(self) -> Browser:
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(channel="chromium")
        self._ctx = self._browser.new_context(**DESKTOP)
        self._page = self._ctx.new_page()
        self._page.set_default_timeout(self.timeout_ms)
        self._console: list[str] = []
        self._errors: list[str] = []
        self._requests: list[str] = []
        self._page.on("console", lambda m: m.type == "error" and self._console.append(m.text))
        self._page.on("pageerror", lambda e: self._errors.append(_first_line(e)))
        self._page.on("request", lambda r: self._requests.append(r.url))
        self._mobile_page = None
        if self.mobile:
            self._mobile_page = self._browser.new_context(**MOBILE).new_page()
            self._mobile_page.set_default_timeout(self.timeout_ms)
        return self

    def __exit__(self, *exc) -> None:
        self._browser.close()
        self._pw.stop()

    def capture(self, url: str) -> PageCapture:
        self._console.clear()
        self._errors.clear()
        self._requests.clear()
        start = time.perf_counter()
        try:
            response = self._page.goto(url, wait_until="load")
        except PlaywrightError as e:
            raise PageLoadError(_first_line(e)) from e
        load_time = round(time.perf_counter() - start, 2)
        try:
            self._page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightError:
            pass

        data = self._page.evaluate(PAGE_DATA_JS)
        return PageCapture(
            url=url,
            final_url=self._page.url,
            status=response.status if response else None,
            headers=response.all_headers() if response else {},
            title=self._page.title(),
            meta=data["meta"],
            links=data["links"],
            console_errors=list(self._console),
            page_errors=list(self._errors),
            resources=[Resource(**r) for r in data["resources"]],
            requested_urls=list(self._requests),
            images=[Image(**i) for i in data["images"]],
            timings=Timings(**self._page.evaluate(TIMINGS_JS)),
            axe_violations=run_axe(self._page),
            mobile=self._capture_mobile(url),
            load_time_s=load_time,
        )

    def _capture_mobile(self, url: str) -> MobileData | None:
        if not self._mobile_page:
            return None
        try:
            self._mobile_page.goto(url, wait_until="load")
            return MobileData(**self._mobile_page.evaluate(MOBILE_JS))
        except PlaywrightError:
            return None

    def fetch_status(self, url: str) -> int | str:
        try:
            return self._ctx.request.get(url, timeout=15_000, max_redirects=5).status
        except Exception as e:
            return f"{type(e).__name__}: {_first_line(e)}"

    def screenshot(self, url: str, path: Path, mobile: bool = False) -> Path:
        page = self._mobile_page if mobile and self._mobile_page else self._page
        if page.url.split("#")[0].rstrip("/") != url.split("#")[0].rstrip("/"):
            page.goto(url, wait_until="load")
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
        return path

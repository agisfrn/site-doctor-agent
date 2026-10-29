"""Smart mode: Claude decides where to look, the code runs every check."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime

from .browser import NotAPage, PageLoadError
from .checks import basics, links, run_page_checks, seo
from .config import ScanConfig
from .events import Event, ScanStarted
from .models import SEVERITIES, Finding, PageResult, ScanResult
from .session import ToolAgent, build_result, make_tool
from .urls import normalize_url, origin_of

__all__ = ["Agent", "normalize_url", "origin_of"]

MAX_SAME_SITE_LINKS = 80
MAX_EXTERNAL_LINKS = 30

TOOLS = [
    make_tool("open_page",
              "Load a page of the site in a real browser and run every check on it (status, JS "
              "errors, accessibility, SEO, performance, security, mobile). Returns issue counts, "
              "the top issues and the page's links. Each new page uses one page from your budget.",
              url="Absolute URL on the site being examined"),
    make_tool("check_link",
              "Request a URL without opening it and return its HTTP status. Cheap: use it for "
              "external links, assets and internal links you don't need to open.",
              url="Absolute URL to check", _found_on="URL of the page the link appeared on"),
    make_tool("screenshot",
              "Save desktop and phone screenshots of a page as evidence. Use it on pages with "
              "serious issues.",
              url="Absolute URL of the page"),
    make_tool("finish",
              "End the examination. Call this when you've covered the important pages or your "
              "page budget is used up.",
              summary="Two or three sentences on the site's overall health and biggest problems"),
]


def system_prompt(config: ScanConfig) -> str:
    return f"""You are Site Doctor, an expert website QA engineer examining {config.url}.

Every time you call open_page, the code runs a full set of checks on that page and records the \
findings; you don't need to judge severities or list problems yourself. Your job is to explore \
the site well within a budget of {config.max_pages} pages:
- Start with the URL you were given, then open the pages that matter most: the main navigation, \
key landing pages, and pages from different sections or templates.
- Prefer one page per template (e.g. one product page, not ten).
- Use check_link (it's cheap) for external links, downloads and internal links you won't open.
- Take a screenshot of every page where you see critical or high issues.
- Call finish with a short summary when you've covered the site or the budget is used up.
Keep any text between tool calls to one short sentence."""


class Agent(ToolAgent):
    def __init__(self, client, browser, config: ScanConfig,
                 on_event: Callable[[Event], None] = lambda e: None):
        super().__init__(client, browser, config, on_event)
        self.pages: dict[str, PageResult] = {}
        self.summaries: dict[str, dict] = {}
        self.link_findings: list[Finding] = []
        self.site_findings: list[Finding] = []
        self.link_status: dict[str, int | str] = {}
        self.origin = origin_of(normalize_url(config.url))
        self.origin_confirmed = False

    # --- tools ------------------------------------------------------------------------------

    def _tool_open_page(self, url: str):
        key = normalize_url(url)
        if origin_of(key) != self.origin:
            return {"error": f"open_page only works on {self.origin}. "
                             "Use check_link for other sites."}, ""
        if key in self.summaries:
            return self.summaries[key], "already opened"
        if len(self.pages) >= self.config.max_pages:
            return {"error": f"Page budget of {self.config.max_pages} used up. "
                             "Call finish with your summary."}, ""
        try:
            cap = self.browser.capture(url)
        except NotAPage as e:
            return {"error": f"{e}: it's a file, not a page. Use check_link for files."}, ""
        except PageLoadError as e:
            is_start = key == normalize_url(self.config.url)
            failure = basics.load_failure(key, str(e), is_start)
            self.pages[key] = PageResult(key, None, "", 0.0, None, [failure], error=str(e))
            return {"error": f"Page failed to load: {e}"}, ""

        if not self.origin_confirmed:
            # The start URL may redirect (http → https, a.com → www.a.com): follow it.
            self.origin = origin_of(normalize_url(cap.final_url))
            self.origin_confirmed = True
            self.site_findings = seo.check_site(self.origin, self.browser.fetch_status)

        findings = run_page_checks(cap)
        final = normalize_url(cap.final_url)
        result = PageResult(final, cap.status, cap.title, cap.load_time_s, cap.timings.lcp_ms,
                            findings)
        self.pages[final] = result
        summary = self._page_summary(cap, findings)
        self.summaries[key] = self.summaries[final] = summary
        status = cap.status if cap.status is not None else "?"
        return summary, f"{status} · {cap.load_time_s}s · {len(findings)} issues"

    def _page_summary(self, cap, findings: list[Finding]) -> dict:
        ordered = sorted(findings, key=lambda f: SEVERITIES.index(f.severity))
        top = list(dict.fromkeys(f.title for f in ordered))[:5]
        here = normalize_url(cap.final_url)
        same_site, external = [], []
        for link in cap.links:
            target = normalize_url(link)
            if origin_of(target) == self.origin:
                visited = target == here or target in self.summaries or target in self.pages
                same_site.append({"url": target, "visited": visited})
            else:
                external.append(link)
        same_site = list({d["url"]: d for d in same_site}.values())
        return {
            "url": cap.final_url,
            "status": cap.status,
            "title": cap.title,
            "load_time_s": cap.load_time_s,
            "counts": {"by_category": dict(Counter(f.category for f in findings)),
                       "by_severity": dict(Counter(f.severity for f in findings))},
            "top_issues": top,
            "same_site_links": same_site[:MAX_SAME_SITE_LINKS],
            "external_links": external[:MAX_EXTERNAL_LINKS],
            "pages_left": self.config.max_pages - len(self.pages),
        }

    def _tool_check_link(self, url: str, found_on: str = ""):
        if url not in self.link_status:
            status = self.browser.fetch_status(url)
            self.link_status[url] = status
            internal = origin_of(normalize_url(url)) == self.origin
            finding = links.link_finding(url, status, internal, found_on)
            if finding:
                self.link_findings.append(finding)
        status = self.link_status[url]
        if isinstance(status, str):
            return {"url": url, "status": None, "error_detail": status}, "✗ unreachable"
        broken = status >= 400
        return {"url": url, "status": status}, f"{status} {'✗ broken' if broken else '✓'}"

    # --- results ----------------------------------------------------------------------------

    def _all_findings(self) -> list[Finding]:
        found = [f for p in self.pages.values() for f in p.findings]
        return found + self.link_findings + self.site_findings

    def _status(self) -> tuple[int, int, dict[str, int]]:
        counts = Counter(f.severity for f in self._all_findings())
        return len(self.pages), self.config.max_pages, {s: counts.get(s, 0) for s in SEVERITIES}

    def run(self) -> ScanResult:
        started, t0 = datetime.now(), time.perf_counter()
        self.emit(ScanStarted(self.config.url, self.config.model, self.config.max_pages))
        self.guard(lambda: self._loop(system_prompt(self.config), TOOLS,
                                      f"Examine this website: {self.config.url}",
                                      self.config.max_steps))
        return build_result(self, started=started, t0=t0, pages=list(self.pages.values()),
                            findings=self._all_findings(), mode="smart")

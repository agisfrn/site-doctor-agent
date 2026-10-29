"""Full mode: after the crawler has checked every page, Claude reviews the results like an
analyst: it reads a compact overview, inspects the pages that matter, and takes screenshots."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable

from .config import ScanConfig
from .crawler import CrawlResult, group_pages
from .events import Event
from .models import SEVERITIES
from .scoring import PENALTY
from .session import ToolAgent, make_tool
from .triage import group_findings
from .urls import normalize_url

MAX_STEPS = 30
MAX_SCREENSHOTS = 12
MAX_OVERVIEW_CHARS = 24_000  # roughly 6 000 tokens
MAX_DETAIL_LINKS = 80
MAX_DETAIL_FINDINGS = 40
# (page groups, top issues, worst pages), tried in order until the overview fits
OVERVIEW_SIZES = [(30, 20, 15), (20, 15, 10), (10, 10, 8), (5, 5, 5)]

TOOLS = [
    make_tool("page_details",
              "Everything the checks found on one crawled page: status, load time, every finding "
              "with its evidence, and the page's links with their HTTP status.",
              url="URL of a crawled page"),
    make_tool("screenshot",
              f"Save desktop and phone screenshots of a page as evidence (at most "
              f"{MAX_SCREENSHOTS} per scan). Use it on pages with critical or high issues.",
              url="URL of a crawled page"),
    make_tool("finish",
              "End the review once you've looked at the pages that best show the site's problems.",
              summary="Two or three sentences on the site's overall health and biggest problems"),
]

SYSTEM = """You are Site Doctor, an expert website QA engineer. A crawler has already loaded \
every page of {url} in a real browser and run the full set of checks on each one, so every \
finding is already recorded and graded. You review the results:
- Read the overview: page groups (templates), the most widespread issues, the worst pages.
- Use page_details on the pages that best show the site's biggest problems: the worst pages, \
and one page from each page group that has issues.
- Screenshot pages with critical or high issues (at most {shots}).
- Call finish with a short summary for the site owner. If robots.txt blocked pages, say so.
Keep any text between tool calls to one short sentence."""


def _trim(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _overview(crawl: CrawlResult, groups_n: int, issues_n: int, worst_n: int) -> dict:
    findings = crawl.findings
    broken = sum(1 for s in crawl.link_status.values() if not (isinstance(s, int) and s < 400))
    issue_groups = group_findings(findings)
    worst = sorted(
        ({"url": _trim(url), "status": page.status, "issues": len(page.findings),
          "score": sum(PENALTY[f.severity] for f in page.findings)}
         for url, page in crawl.pages.items()),
        key=lambda p: (-p["score"], p["url"]))
    return {
        "site": crawl.origin,
        "totals": {
            "pages": len(crawl.pages),
            "status_codes": dict(Counter(str(p.status) for p in crawl.pages.values())),
            "links_checked": len(crawl.link_status),
            "broken_links": broken,
            "blocked_by_robots_txt": crawl.robots_blocked,
            "by_severity": {s: sum(1 for f in findings if f.severity == s) for s in SEVERITIES},
            "by_category": dict(Counter(f.category for f in findings)),
        },
        "page_groups": group_pages(crawl.pages)[:groups_n],
        "top_issues": [{"id": g.id, "title": _trim(g.title, 120), "severity": g.severity,
                        "pages": len(g.urls)} for g in issue_groups[:issues_n]],
        "worst_pages": worst[:worst_n],
    }


def build_overview(crawl: CrawlResult, max_chars: int = MAX_OVERVIEW_CHARS) -> dict:
    """A size-capped summary of the whole crawl, so Claude's cost doesn't grow with the site."""
    for sizes in OVERVIEW_SIZES:
        overview = _overview(crawl, *sizes)
        if len(json.dumps(overview, indent=1)) <= max_chars:  # measured as it's sent
            return overview
    return overview


class Analyst(ToolAgent):
    def __init__(self, client, browser, config: ScanConfig, crawl: CrawlResult,
                 on_event: Callable[[Event], None] = lambda e: None):
        super().__init__(client, browser, config, on_event)
        self.crawl = crawl

    def _status(self) -> tuple[int, int, dict[str, int]]:
        counts = Counter(f.severity for f in self.crawl.findings)
        return (len(self.crawl.pages), self.config.max_pages,
                {s: counts.get(s, 0) for s in SEVERITIES})

    def _known(self, url: str) -> str | None:
        key = normalize_url(url)
        return key if key in self.crawl.pages else None

    def _tool_page_details(self, url: str):
        key = self._known(url)
        if key is None:
            return {"error": f"{url} wasn't crawled. Use a URL from the overview."}, ""
        page = self.crawl.pages[key]
        findings = [{"id": f.id, "severity": f.severity, "title": f.title,
                     "evidence": _trim(f.evidence)} for f in page.findings[:MAX_DETAIL_FINDINGS]]
        if page.error:
            return {"url": key, "failed_to_load": page.error, "findings": findings}, "failed page"
        page_links = []
        for link in self.crawl.links_from.get(key, [])[:MAX_DETAIL_LINKS]:
            crawled = link in self.crawl.pages
            status = "crawled page" if crawled else self.crawl.link_status.get(link)
            page_links.append({"url": link, "status": status})
        return {
            "url": key, "status": page.status, "title": page.title,
            "load_time_s": page.load_time_s, "lcp_ms": page.lcp_ms,
            "findings": findings, "links": page_links,
        }, f"{len(page.findings)} issues"

    def _tool_screenshot(self, url: str):
        if self._known(url) is None:
            return {"error": f"{url} wasn't crawled. Use a URL from the overview."}, ""
        taken = sum(1 for paths in self.screenshots.values() for p in paths if "desktop" in p)
        if taken >= MAX_SCREENSHOTS:
            return {"error": f"Screenshot limit of {MAX_SCREENSHOTS} reached."}, ""
        return super()._tool_screenshot(url)

    def run(self) -> None:
        overview = json.dumps(build_overview(self.crawl), indent=1)
        self._loop(SYSTEM.format(url=self.config.url, shots=MAX_SCREENSHOTS), TOOLS,
                   f"Overview of the completed crawl of {self.config.url}:\n{overview}", MAX_STEPS)

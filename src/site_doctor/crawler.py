"""Full-site crawl: robots.txt, sitemaps and every same-site link, then a check of every link."""

from __future__ import annotations

import re
from collections import Counter, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import defusedxml.ElementTree as ET
from defusedxml import DefusedXmlException

from .agent import normalize_url, origin_of
from .browser import NotAPage, PageLoadError
from .checks import basics, links, run_page_checks, seo
from .config import ScanConfig
from .events import CrawlProgress, Event
from .models import Finding, PageCapture, PageResult

SKIP_EXTENSIONS = (".pdf", ".zip", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".mp4",
                   ".mp3", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".xml",
                   ".json", ".txt")
MAX_SITEMAP_URLS = 5000
SITEMAP_DEPTH = 2
LINK_CHUNK = 50


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, origin: str) -> bool:
    """Same host, ignoring http/https and a leading www."""
    return urlsplit(url).scheme in ("http", "https") and _host(url) == _host(origin)


_TEMPLATE_SEGMENT = re.compile(r"^(\d+|[0-9a-fA-F]{8,}|.{31,})$")


def page_pattern(url: str) -> str:
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return "/" + "/".join("*" if _TEMPLATE_SEGMENT.match(s) else s for s in segments)


def group_pages(pages: dict[str, PageResult]) -> list[dict]:
    """Group pages by URL template, e.g. /products/item-0 … item-29 → /products/*."""
    patterns = {url: page_pattern(url) for url in pages}
    children: dict[str, set[str]] = {}
    for pattern in patterns.values():
        parent = pattern.rsplit("/", 1)[0]
        if parent:  # never collapse top-level pages into "/*"
            children.setdefault(parent, set()).add(pattern)
    collapse = {parent for parent, kids in children.items() if len(kids) >= 3}

    counts, issues = Counter(), Counter()
    for url, pattern in patterns.items():
        parent = pattern.rsplit("/", 1)[0]
        key = parent + "/*" if parent in collapse else pattern
        counts[key] += 1
        issues[key] += len(pages[url].findings)
    groups = [{"pattern": k, "pages": n, "issues": issues[k]} for k, n in counts.items()]
    return sorted(groups, key=lambda g: (-g["pages"], g["pattern"]))


@dataclass
class CrawlResult:
    pages: dict[str, PageResult]
    findings: list[Finding]
    link_status: dict[str, int | str]
    links_from: dict[str, list[str]]
    origin: str
    stopped_reason: str = ""
    sitemap_urls: list[str] = field(default_factory=list)


class Crawler:
    def __init__(self, browser, config: ScanConfig,
                 on_event: Callable[[Event], None] = lambda e: None):
        self.browser = browser
        self.config = config
        self.emit = on_event
        self.start = normalize_url(config.url)
        self.origin = origin_of(self.start)
        self.origin_confirmed = False
        self.pages: dict[str, PageResult] = {}
        self.links_from: dict[str, list[str]] = {}
        self.found_on: dict[str, str] = {}
        self.captured: set[str] = set()
        self.link_status: dict[str, int | str] = {}
        self.findings: list[Finding] = []
        self.sitemap_urls: list[str] = []
        self.queue: deque[str] = deque()
        self.seen: set[str] = set()
        self.stopped_reason = ""
        self.robots = RobotFileParser()

    # --- discovery ----------------------------------------------------------------------------

    def _load_robots(self) -> list[str]:
        status, text = self.browser.fetch_text(self.origin + "/robots.txt")
        if self.config.ignore_robots or not (isinstance(status, int) and status < 400):
            self.robots.allow_all = True
        else:
            self.robots.parse(text.splitlines())
        return list(self.robots.site_maps() or []) if isinstance(status, int) else []

    def _read_sitemaps(self, roots: list[str]) -> None:
        todo, done = [(u, 0) for u in roots], set()
        while todo and len(self.sitemap_urls) < MAX_SITEMAP_URLS:
            url, depth = todo.pop(0)
            if url in done:
                continue
            done.add(url)
            status, text = self.browser.fetch_text(url)
            if not (isinstance(status, int) and status < 400 and text.strip()):
                continue
            try:
                root = ET.fromstring(text)
            except (ET.ParseError, DefusedXmlException):  # sitemaps are untrusted input
                continue
            locs = [(el.text or "").strip() for el in root.iter() if el.tag.split("}")[-1] == "loc"]
            if root.tag.split("}")[-1] == "sitemapindex":
                if depth < SITEMAP_DEPTH:
                    todo += [(loc, depth + 1) for loc in locs if loc]
                continue
            room = MAX_SITEMAP_URLS - len(self.sitemap_urls)
            self.sitemap_urls += [loc for loc in locs if loc][:room]

    def _is_candidate(self, url: str) -> bool:
        path = urlsplit(url).path.lower()
        return (same_site(url, self.origin) and not path.endswith(SKIP_EXTENSIONS)
                and self.robots.can_fetch("*", url))

    def _enqueue(self, url: str) -> None:
        if url not in self.seen and self._is_candidate(url):
            self.seen.add(url)
            self.queue.append(url)

    # --- crawl --------------------------------------------------------------------------------

    def _progress(self, checked: int = 0, total: int = 0) -> None:
        self.emit(CrawlProgress(len(self.pages), len(self.queue), checked, total))

    def _record(self, url: str, result: PageCapture | PageLoadError) -> None:
        if isinstance(result, NotAPage):
            return  # a file: it gets a status check with the other links
        if isinstance(result, PageLoadError):
            failure = basics.load_failure(url, str(result), is_start=url == self.start)
            self.pages[url] = PageResult(url, None, "", 0.0, None, [failure], error=str(result))
            return
        if result.status and result.status >= 400 and url != self.start:
            self.link_status[url] = result.status  # a broken link, reported with its linking page
            return
        self.captured.add(url)
        final = normalize_url(result.final_url)
        if not self.origin_confirmed:
            self.origin = origin_of(final)  # follow a redirected start URL (http → https, www)
            self.origin_confirmed = True
        self.seen.add(final)
        if final in self.pages:
            return  # a redirect to a page we already have
        findings = run_page_checks(result)
        self.pages[final] = PageResult(final, result.status, result.title, result.load_time_s,
                                       result.timings.lcp_ms, findings)
        page_links = [normalize_url(link) for link in result.links]
        self.links_from[final] = page_links
        for link in page_links:
            self.found_on.setdefault(link, final)
            self._enqueue(link)

    def _crawl(self) -> None:
        sitemap_roots = [self.origin + "/sitemap.xml", *self._load_robots()]
        self._read_sitemaps(sitemap_roots)
        self.seen.add(self.start)
        self.queue.append(self.start)
        for url in self.sitemap_urls:
            self._enqueue(normalize_url(url))

        while self.queue and len(self.pages) < self.config.max_pages:
            room = min(self.config.concurrency, self.config.max_pages - len(self.pages))
            batch = [self.queue.popleft() for _ in range(min(room, len(self.queue)))]
            for url, result in self.browser.capture_many(batch).items():
                self._record(url, result)
            self._progress()
        if self.queue:
            self.stopped_reason = "page limit"

    def _check_links(self) -> None:
        to_check = [u for u in self.found_on
                    if u not in self.pages and u not in self.captured and u not in self.link_status]
        for i in range(0, len(to_check), LINK_CHUNK):
            self.link_status.update(self.browser.fetch_many(to_check[i:i + LINK_CHUNK]))
            self._progress(min(i + LINK_CHUNK, len(to_check)), len(to_check))
        for url in [u for u in self.found_on if u in self.link_status]:
            finding = links.link_finding(url, self.link_status[url],
                                         internal=same_site(url, self.origin),
                                         found_on=self.found_on.get(url, ""))
            if finding:
                self.findings.append(finding)

    def run(self) -> CrawlResult:
        try:
            self._crawl()
            self._check_links()
            if self.captured:
                self.findings += seo.check_site(self.origin, self.browser.fetch_status)
        except KeyboardInterrupt:
            self.stopped_reason = "interrupted"
        page_findings = [f for p in self.pages.values() for f in p.findings]
        return CrawlResult(self.pages, page_findings + self.findings, self.link_status,
                           self.links_from, self.origin, self.stopped_reason, self.sitemap_urls)

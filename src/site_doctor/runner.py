"""Picks the scan mode: full (the crawler visits every page, Claude analyses) or smart (Claude
navigates a sample of pages itself)."""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import datetime

from .agent import Agent
from .analyst import Analyst
from .config import ScanConfig
from .crawler import Crawler, group_pages
from .events import Event, ScanStarted
from .models import ScanResult
from .session import build_result


def scan(client, browser, config: ScanConfig,
         on_event: Callable[[Event], None] = lambda e: None) -> ScanResult:
    if config.mode == "smart":
        return Agent(client, browser, config, on_event).run()

    started, t0 = datetime.now(), time.perf_counter()
    on_event(ScanStarted(config.url, config.model, config.max_pages))
    crawl = Crawler(browser, config, on_event).run()
    analyst = Analyst(client, browser, config, crawl, on_event)
    if crawl.stopped_reason == "interrupted":
        analyst.partial_reason = "interrupted"
    else:
        analyst.guard(analyst.run)
    return build_result(analyst, started=started, t0=t0, pages=list(crawl.pages.values()),
                        findings=crawl.findings, mode="full",
                        links_checked=len(crawl.link_status), page_groups=group_pages(crawl.pages))

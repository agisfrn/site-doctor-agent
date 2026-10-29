from urllib.parse import urlsplit

import pytest

from site_doctor.agent import normalize_url
from site_doctor.config import ScanConfig
from site_doctor.crawler import Crawler, group_pages, same_site
from site_doctor.events import CrawlProgress
from site_doctor.models import PageResult
from tests.checks.helpers import make_capture
from tests.crawl_site import DISALLOWED, PAGES, PDF
from tests.fakes import FakeCrawlBrowser


def cfg(url, **kw):
    return ScanConfig(url=url + "/" if not url.endswith("/") else url, mode="full",
                      **{"max_pages": 200, **kw})


def paths(result):
    return {urlsplit(u).path for u in result.pages}


@pytest.fixture(scope="module")
def default_crawl(browser, crawl_url):
    events = []
    result = Crawler(browser, cfg(crawl_url), events.append).run()
    return result, events


# --- against the generated fixture site, with a real browser ---------------------------------

def test_finds_every_page_including_sitemap_orphans(default_crawl):
    assert paths(default_crawl[0]) == PAGES


def test_respects_robots_unless_told_not_to(default_crawl, browser, crawl_url):
    assert DISALLOWED not in paths(default_crawl[0])
    assert DISALLOWED in paths(Crawler(browser, cfg(crawl_url, ignore_robots=True)).run())


def test_pdf_is_a_link_not_a_page(default_crawl, crawl_url):
    result = default_crawl[0]
    assert PDF not in paths(result)
    assert result.link_status[crawl_url + PDF] == 200


def test_broken_links_found_once_with_their_page(default_crawl, crawl_url):
    result = default_crawl[0]
    broken = [f for f in result.findings if f.id == "links.broken-internal"]
    assert [f.url for f in broken] == [crawl_url + "/products/item-999"]
    assert "linked from " + crawl_url + "/products/item-" in broken[0].evidence
    assert any(f.id == "links.unreachable" for f in result.findings)


def test_page_limit(browser, crawl_url):
    result = Crawler(browser, cfg(crawl_url, max_pages=5)).run()
    assert len(result.pages) == 5 and result.stopped_reason == "page limit"


def test_progress_events(default_crawl):
    progress = [e for e in default_crawl[1] if isinstance(e, CrawlProgress)]
    assert progress and progress[-1].links_checked == progress[-1].links_total > 0


def test_other_sites_in_sitemap_are_skipped(default_crawl):
    assert not any("elsewhere.example" in u for u in default_crawl[0].pages)


def test_failed_start_page_ends_with_one_critical_finding(browser):
    result = Crawler(browser, cfg("http://127.0.0.1:9")).run()
    [f] = [f for f in result.findings if f.id == "basics.page-load-failed"]
    assert f.severity == "critical" and len(result.pages) == 1


# --- with a fake browser ---------------------------------------------------------------------

def site(n, **kw):
    """https://a.com/ linking to n pages /p0../p{n-1}."""
    links = [f"https://a.com/p{i}" for i in range(n)]
    pages = {"https://a.com/": make_capture(url="https://a.com/", final_url="https://a.com/",
                                            links=links)}
    pages.update({u: make_capture(url=u, final_url=u) for u in links})
    return FakeCrawlBrowser(pages, **kw)


def test_missing_robots_and_sitemap_mean_crawl_everything():
    assert len(Crawler(site(3), cfg("https://a.com")).run().pages) == 4


def test_interrupt_returns_partial():                                              # Review Focus 5
    result = Crawler(site(10, interrupt_on_batch=2), cfg("https://a.com", concurrency=1)).run()
    assert result.stopped_reason == "interrupted" and len(result.pages) == 1


def test_huge_sitemap_is_capped():                                                 # Review Focus 3
    locs = "".join(f"<url><loc>https://a.com/s{i}</loc></url>" for i in range(6000))
    browser = site(0, texts={"https://a.com/sitemap.xml": (200, f"<urlset>{locs}</urlset>")})
    crawler = Crawler(browser, cfg("https://a.com", max_pages=10))
    result = crawler.run()
    assert len(crawler.sitemap_urls) == 5000 and len(result.pages) == 10


def test_tracking_parameter_variants_are_one_page():                               # Review Focus 1
    variants = ["https://a.com/x?utm_source=n", "https://a.com/x?fbclid=1", "https://a.com/x#top"]
    pages = {"https://a.com/": make_capture(url="https://a.com/", final_url="https://a.com/",
                                            links=variants),
             "https://a.com/x": make_capture(url="https://a.com/x", final_url="https://a.com/x")}
    result = Crawler(FakeCrawlBrowser(pages), cfg("https://a.com")).run()
    assert set(result.pages) == {"https://a.com/", "https://a.com/x"}


def test_same_site_ignores_www():                                                  # Review Focus 2
    assert same_site("https://www.a.com/x", "https://a.com")
    assert same_site("https://a.com/x", "https://www.a.com")
    assert not same_site("https://b.com/", "https://a.com")


def test_group_pages_collapses_templates():
    pages = {f"https://a.com/products/item-{i}": PageResult(
        f"https://a.com/products/item-{i}", 200, "", 0.1, None, []) for i in range(30)}
    pages["https://a.com/"] = PageResult("https://a.com/", 200, "", 0.1, None, [])
    assert group_pages(pages)[0] == {"pattern": "/products/*", "pages": 30, "issues": 0}


def test_normalize_url_drops_tracking_params():                                    # Review Focus 1
    assert normalize_url("https://a.com/x?utm_source=n&id=3&fbclid=z") == "https://a.com/x?id=3"


# --- review fixes ----------------------------------------------------------------------------

def test_offsite_redirect_is_a_link_not_a_page():
    pages = {"https://a.com/": make_capture(url="https://a.com/", final_url="https://a.com/",
                                            links=["https://a.com/careers"]),
             "https://a.com/careers": make_capture(url="https://a.com/careers",
                                                   final_url="https://boards.greenhouse.io/a")}
    result = Crawler(FakeCrawlBrowser(pages), cfg("https://a.com")).run()
    assert set(result.pages) == {"https://a.com/"}
    assert result.link_status["https://a.com/careers"] == 200


def test_ignore_robots_still_reads_its_sitemap(browser, crawl_url):
    assert "/orphan" in paths(Crawler(browser, cfg(crawl_url, ignore_robots=True)).run())


def test_disallow_all_is_reported():
    browser = site(5, texts={"https://a.com/robots.txt": (200, "User-agent: *\nDisallow: /\n")})
    result = Crawler(browser, cfg("https://a.com")).run()
    assert len(result.pages) == 1 and result.robots_blocked == 5


def test_links_from_pages_come_before_the_sitemap():
    locs = "".join(f"<url><loc>https://a.com/s{i}</loc></url>" for i in range(20))
    browser = site(2, texts={"https://a.com/sitemap.xml": (200, f"<urlset>{locs}</urlset>")})
    result = Crawler(browser, cfg("https://a.com", max_pages=3)).run()
    assert set(result.pages) == {"https://a.com/", "https://a.com/p0", "https://a.com/p1"}


def test_browser_crash_stops_the_crawl():
    from site_doctor.browser import BrowserCrashed

    browser = site(10)
    for i in range(10):
        browser.pages[f"https://a.com/p{i}"] = BrowserCrashed("Target closed")
    result = Crawler(browser, cfg("https://a.com", concurrency=1)).run()
    assert result.stopped_reason == "browser crashed" and len(result.pages) == 1

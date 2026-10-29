import json

from site_doctor.config import ScanConfig
from site_doctor.runner import scan
from tests.checks.helpers import make_capture
from tests.fakes import FakeBrowser, FakeClient, FakeCrawlBrowser, response, text, tool_use

TRIAGE = response(text(json.dumps({"summary": "T", "issues": []})), stop="end_turn")


def small_site(**kw):
    links = ["https://a.com/p0", "https://a.com/p1", "https://ext.example/"]
    pages = {"https://a.com/": make_capture(url="https://a.com/", final_url="https://a.com/",
                                            links=links, page_errors=["boom"])}
    for u in links[:2]:
        pages[u] = make_capture(url=u, final_url=u)
    return FakeCrawlBrowser(pages, **kw)


def test_full_mode_crawls_then_analyses(tmp_path):
    client = FakeClient([response(tool_use("finish", summary="S")), TRIAGE])
    events = []
    result = scan(client, small_site(), ScanConfig(url="https://a.com/", out_dir=tmp_path),
                  events.append)
    assert result.mode == "full" and len(result.pages) == 3 and result.summary == "T"
    assert result.links_checked == 1 and result.page_groups
    assert not result.partial and len(client.calls) == 2


def test_smart_mode_is_the_v2_agent(tmp_path):
    client = FakeClient([response(tool_use("finish", summary="S"))])
    config = ScanConfig(url="https://a.com/", out_dir=tmp_path, mode="smart")
    assert scan(client, FakeBrowser(), config).mode == "smart"


def test_interrupted_crawl_skips_the_analyst(tmp_path):
    client = FakeClient([])
    result = scan(client, small_site(interrupt_on_batch=1),
                  ScanConfig(url="https://a.com/", out_dir=tmp_path))
    assert (result.partial, result.partial_reason) == (True, "interrupted")
    assert client.calls == []


def test_analyst_api_error_keeps_the_crawl(tmp_path):
    import anthropic
    import httpx2

    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x"))
    result = scan(FakeClient([error]), small_site(), ScanConfig(url="https://a.com/",
                                                                 out_dir=tmp_path))
    assert result.partial and len(result.pages) == 3
    assert "basics.js-error" in {f.id for f in result.findings}


def test_dead_site_skips_the_analyst(tmp_path):
    from site_doctor.browser import PageLoadError

    client = FakeClient([])
    browser = FakeCrawlBrowser({"https://a.com/": PageLoadError("net::ERR_NAME_NOT_RESOLVED")})
    result = scan(client, browser, ScanConfig(url="https://a.com/", out_dir=tmp_path))
    assert client.calls == [] and result.partial and "load" in result.partial_reason


def test_crashed_browser_is_partial(tmp_path):
    from site_doctor.browser import BrowserCrashed

    browser = small_site()
    browser.pages["https://a.com/p0"] = browser.pages["https://a.com/p1"] = BrowserCrashed("x")
    client = FakeClient([])
    result = scan(client, browser, ScanConfig(url="https://a.com/", out_dir=tmp_path,
                                               concurrency=1))
    assert result.partial and result.partial_reason == "browser crashed" and client.calls == []


def test_ctrl_c_during_explanations_keeps_the_report(tmp_path):
    client = FakeClient([response(tool_use("finish", summary="S")), KeyboardInterrupt()])
    result = scan(client, small_site(), ScanConfig(url="https://a.com/", out_dir=tmp_path))
    assert result.partial and result.partial_reason == "interrupted" and result.groups

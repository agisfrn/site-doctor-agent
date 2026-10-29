import json

import anthropic
import httpx2

from site_doctor.analyst import Analyst, build_overview
from site_doctor.config import ScanConfig
from site_doctor.crawler import CrawlResult
from site_doctor.models import Finding, PageResult
from tests.fakes import FakeBrowser, FakeClient, response, tool_use

SEVERITY_CYCLE = ["critical", "high", "medium", "low"]


def crawl_with(pages: int, distinct_issues: int = 5) -> CrawlResult:
    results, findings = {}, []
    for i in range(pages):
        url = f"https://a.com/products/item-{i}"
        page_findings = [
            Finding(f"seo.rule-{(i + k) % distinct_issues}", "seo", SEVERITY_CYCLE[(i + k) % 4],
                    f"Problem number {(i + k) % distinct_issues}", url, "evidence")
            for k in range(i % 4)
        ]
        results[url] = PageResult(url, 200, f"Item {i}", 0.2, 900.0, page_findings)
        findings += page_findings
    return CrawlResult(pages=results, findings=findings,
                       link_status={"https://a.com/gone": 404},
                       links_from={u: ["https://a.com/gone", "https://a.com/products/item-0"]
                                   for u in results},
                       origin="https://a.com")


def analyst(script, crawl, tmp_path):
    client = FakeClient(script)
    browser = FakeBrowser()
    config = ScanConfig(url="https://a.com/", out_dir=tmp_path)
    return Analyst(client, browser, config, crawl), client, browser


def results(agent):
    return [{**b, "data": json.loads(b["content"])}
            for m in agent.messages if m["role"] == "user" and isinstance(m["content"], list)
            for b in m["content"]]


def test_overview_has_groups_issues_and_worst_pages():
    ov = build_overview(crawl_with(60))
    assert ov["totals"]["pages"] == 60 and ov["totals"]["broken_links"] == 1
    assert ov["page_groups"][0] == {"pattern": "/products/*", "pages": 60,
                                    "issues": sum(i % 4 for i in range(60))}
    assert len(ov["worst_pages"]) == 15 and len(ov["top_issues"]) <= 20
    scores = [p["score"] for p in ov["worst_pages"]]
    assert scores == sorted(scores, reverse=True)


def test_overview_stays_under_the_size_cap():
    assert len(json.dumps(build_overview(crawl_with(1000, distinct_issues=400)))) <= 24_000


def test_analyst_inspects_screenshots_and_finishes(tmp_path):
    worst = "https://a.com/products/item-3"
    script = [response(tool_use("page_details", url=worst)),
              response(tool_use("screenshot", url=worst)),
              response(tool_use("finish", summary="S"))]
    agent, client, browser = analyst(script, crawl_with(10), tmp_path)
    agent.run()
    assert agent.notes == "S" and browser.shots == [(worst, False), (worst, True)]
    details = results(agent)[0]["data"]
    assert details["status"] == 200 and len(details["findings"]) == 3
    assert {"url": "https://a.com/gone", "status": 404} in details["links"]
    first = client.calls[0]
    assert '"worst_pages"' in first["messages"][0]["content"]
    assert first["cache_control"] == {"type": "ephemeral"}


def test_screenshot_cap(tmp_path):
    script = [response(*[tool_use("screenshot", url=f"https://a.com/products/item-{i}")
                         for i in range(13)]),
              response(tool_use("finish", summary="S"))]
    agent, *_ = analyst(script, crawl_with(20), tmp_path)
    agent.run()
    shots = results(agent)[:13]  # the 14th result is finish
    assert [r["is_error"] for r in shots] == [False] * 12 + [True]
    assert "12" in shots[-1]["data"]["error"]


def test_page_details_unknown_url_is_an_error(tmp_path):
    script = [response(tool_use("page_details", url="https://a.com/nope")),
              response(tool_use("finish", summary="S"))]
    agent, *_ = analyst(script, crawl_with(3), tmp_path)
    agent.run()
    [first, _] = results(agent)
    assert first["is_error"] and "https://a.com/nope" in first["data"]["error"]


def test_api_error_keeps_crawl_findings(tmp_path):
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    crawl = crawl_with(8)
    before = len(crawl.findings)
    agent, *_ = analyst([anthropic.APIConnectionError(request=request)], crawl, tmp_path)
    agent.guard(agent.run)
    assert agent.partial_reason.startswith("API error") and len(crawl.findings) == before

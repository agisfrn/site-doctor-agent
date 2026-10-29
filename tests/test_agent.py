import json

import anthropic
import httpx2 as httpx
import pytest

from site_doctor.agent import Agent, normalize_url
from site_doctor.browser import PageLoadError
from site_doctor.config import ScanConfig
from site_doctor.events import ScanFinished, StepFinished, StepStarted, TriageStarted
from tests.checks.helpers import make_capture
from tests.fakes import FakeBrowser, FakeClient, response, text, tool_use


def make_agent(script, pages, tmp_path, statuses=None, **cfg):
    client = FakeClient(script)
    browser = FakeBrowser(pages, statuses)
    events = []
    config = ScanConfig(url=cfg.pop("url", "https://a.com/"), out_dir=tmp_path, **cfg)
    agent = Agent(client, browser, config, on_event=events.append)
    return agent, client, browser, events


def tool_results(agent):
    """Every tool_result block the agent sent back, in order, parsed."""
    out = []
    for m in agent.messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for block in m["content"]:
                out.append({**block, "data": json.loads(block["content"])})
    return out


def page(url, **kw):
    return make_capture(url=url, final_url=kw.pop("final_url", url), **kw)


def test_normalize_url():
    assert normalize_url("https://A.com") == "https://a.com/"
    assert normalize_url("https://a.com/x/#top") == "https://a.com/x"
    assert normalize_url("https://a.com/x?b=1#c") == "https://a.com/x?b=1"


def test_crawl_opens_pages_and_finishes(tmp_path):
    script = [
        response(text("Starting with the home page."), tool_use("open_page", url="https://a.com/")),
        response(tool_use("check_link", url="https://a.com/missing", found_on="https://a.com/")),
        response(tool_use("finish", summary="done")),
        response(text("not json"), stop="end_turn"),  # the triage call (see Task 10 tests)
    ]
    pages = {"https://a.com/": page("https://a.com/", links=["https://a.com/missing"])}
    agent, client, browser, events = make_agent(script, pages, tmp_path,
                                                statuses={"https://a.com/missing": 404})
    result = agent.run()
    assert len(result.pages) == 1 and result.summary == "done"
    assert "links.broken-internal" in {f.id for f in result.findings}
    assert [e.step for e in events if isinstance(e, StepStarted)] == [1, 2, 3]
    finished = [e for e in events if isinstance(e, StepFinished)]
    assert finished[1].summary == "404 ✗ broken"
    assert len(client.calls) == 4


def test_open_page_summary_lists_links_and_counts(tmp_path):
    script = [response(tool_use("open_page", url="https://a.com/")),
              response(tool_use("finish", summary="s"))]
    cap = page("https://a.com/", status=200, page_errors=["boom"],
               links=["https://a.com/", "https://a.com/about", "https://other.com/"])
    agent, *_ = make_agent(script, {"https://a.com/": cap}, tmp_path)
    agent.run()
    data = tool_results(agent)[0]["data"]
    assert data["status"] == 200 and data["pages_left"] == 14
    assert data["same_site_links"] == [{"url": "https://a.com/", "visited": True},
                                       {"url": "https://a.com/about", "visited": False}]
    assert data["external_links"] == ["https://other.com/"]
    assert data["counts"]["by_severity"]["high"] >= 1
    assert "Uncaught JavaScript error" in data["top_issues"]


def test_page_budget_is_enforced(tmp_path):
    script = [response(tool_use("open_page", url="https://a.com/")),
              response(tool_use("open_page", url="https://a.com/about")),
              response(tool_use("finish", summary="s"))]
    pages = {u: page(u) for u in ["https://a.com/", "https://a.com/about"]}
    agent, _, browser, _ = make_agent(script, pages, tmp_path, max_pages=1)
    agent.run()
    second = tool_results(agent)[1]
    assert second["is_error"] and "finish" in second["data"]["error"]
    assert browser.captured == ["https://a.com/"]


def test_reopening_a_page_is_free(tmp_path):
    script = [response(tool_use("open_page", url="https://a.com/")),
              response(tool_use("open_page", url="https://a.com/#top")),
              response(tool_use("finish", summary="s"))]
    agent, _, browser, _ = make_agent(script, {"https://a.com/": page("https://a.com/")}, tmp_path)
    result = agent.run()
    assert len(result.pages) == 1 and len(browser.captured) == 1
    assert not any(r["is_error"] for r in tool_results(agent))


def test_other_origin_is_refused_but_redirected_origin_is_used(tmp_path):
    script = [response(tool_use("open_page", url="http://a.com/")),
              response(tool_use("open_page", url="https://www.a.com/x")),
              response(tool_use("open_page", url="https://evil.com/")),
              response(tool_use("finish", summary="s"))]
    pages = {"http://a.com/": page("http://a.com/", final_url="https://www.a.com/"),
             "https://www.a.com/x": page("https://www.a.com/x")}
    agent, _, browser, _ = make_agent(script, pages, tmp_path, url="http://a.com/")
    agent.run()
    results = tool_results(agent)
    assert [r["is_error"] for r in results[:3]] == [False, False, True]
    assert "check_link" in results[2]["data"]["error"]
    assert "https://evil.com/" not in browser.captured


def test_load_failure_becomes_finding_and_crawl_continues(tmp_path):
    script = [response(tool_use("open_page", url="https://a.com/")),
              response(tool_use("open_page", url="https://a.com/file.pdf")),
              response(tool_use("open_page", url="https://a.com/about")),
              response(tool_use("finish", summary="s"))]
    pages = {"https://a.com/": page("https://a.com/"),
             "https://a.com/file.pdf": PageLoadError("Download is starting"),
             "https://a.com/about": page("https://a.com/about")}
    agent, *_ = make_agent(script, pages, tmp_path)
    result = agent.run()
    assert tool_results(agent)[1]["is_error"]
    [failed] = [f for f in result.findings if f.id == "basics.page-load-failed"]
    assert failed.severity == "high" and failed.evidence == "Download is starting"
    assert len(result.pages) == 3


def test_start_page_failure_is_critical(tmp_path):
    script = [response(tool_use("open_page", url="https://a.com/")),
              response(tool_use("finish", summary="s"))]
    agent, *_ = make_agent(script, {}, tmp_path)
    [f] = [f for f in agent.run().findings if f.id == "basics.page-load-failed"]
    assert f.severity == "critical"


def test_link_lists_are_capped(tmp_path):
    links = [f"https://a.com/p{i}" for i in range(300)] + [f"https://x{i}.com/" for i in range(100)]
    script = [response(tool_use("open_page", url="https://a.com/")),
              response(tool_use("finish", summary="s"))]
    pages = {"https://a.com/": page("https://a.com/", links=links)}
    agent, *_ = make_agent(script, pages, tmp_path)
    agent.run()
    data = tool_results(agent)[0]["data"]
    assert len(data["same_site_links"]) == 80 and len(data["external_links"]) == 30


def test_step_cap_stops_the_crawl(tmp_path):
    script = [response(tool_use("check_link", url=f"https://a.com/{i}")) for i in range(3)]
    agent, client, browser, _ = make_agent(script, {}, tmp_path, max_steps=2)
    agent.run()
    assert browser.fetched == ["https://a.com/0", "https://a.com/1"] and len(client.calls) == 2


def test_end_turn_stops_the_crawl(tmp_path):
    agent, client, *_ = make_agent([response(text("All done."), stop="end_turn")], {}, tmp_path)
    agent.run()
    assert len(client.calls) == 1


def test_screenshots_attach_to_that_pages_findings(tmp_path):
    script = [response(tool_use("open_page", url="https://a.com/")),
              response(tool_use("screenshot", url="https://a.com/")),
              response(tool_use("finish", summary="s"))]
    cap = page("https://a.com/", page_errors=["boom"])
    agent, _, browser, _ = make_agent(script, {"https://a.com/": cap}, tmp_path)
    result = agent.run()
    [f] = [f for f in result.findings if f.id == "basics.js-error"]
    assert f.screenshots == ["screenshots/01-home-desktop.png", "screenshots/01-home-mobile.png"]
    assert (agent.config.run_dir / f.screenshots[0]).exists()
    assert browser.shots == [("https://a.com/", False), ("https://a.com/", True)]


def test_requests_use_fallback_and_effort(tmp_path):
    agent, client, *_ = make_agent([response(stop="end_turn")], {}, tmp_path)
    agent.run()
    kw = client.calls[0]
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["output_config"] == {"effort": "medium"} and kw["model"] == "claude-opus-5-5"
    assert {t["name"] for t in kw["tools"]} == {"open_page", "check_link", "screenshot", "finish"}


def test_usage_is_accumulated(tmp_path):
    script = [response(tool_use("check_link", url="https://a.com/x")), response(stop="end_turn")]
    agent, *_ = make_agent(script, {}, tmp_path)
    usage = agent.run().usage
    assert (usage.input_tokens, usage.output_tokens) == (200, 40)


# --- Task 10: triage call and partial runs -------------------------------------------------

REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
CRAWL = [
    response(tool_use("open_page", url="https://a.com/")),
    response(tool_use("check_link", url="https://a.com/missing")),
    response(tool_use("finish", summary="done")),
]


def crawl_agent(tmp_path, *after):
    pages = {"https://a.com/": page("https://a.com/")}
    return make_agent(CRAWL + list(after), pages, tmp_path,
                      statuses={"https://a.com/missing": 404})


def test_triage_fills_explanations_and_ignores_unknown_ids(tmp_path):
    triage = json.dumps({"summary": "S", "issues": [
        {"id": "links.broken-internal", "explanation": "E", "fix": "F"},
        {"id": "nope", "explanation": "x", "fix": "y"}]})
    agent, client, _, events = crawl_agent(tmp_path, response(text(triage), stop="end_turn"))
    result = agent.run()
    group = next(g for g in result.groups if g.id == "links.broken-internal")
    assert (group.explanation, group.fix) == ("E", "F")
    assert result.summary == "S" and not result.partial
    others = [g for g in result.groups if g.id != "links.broken-internal"]
    assert all(g.explanation == "" for g in others)
    last = client.calls[-1]
    assert last["output_config"]["format"]["type"] == "json_schema" and "tools" not in last
    assert any(isinstance(e, TriageStarted) for e in events)


def test_bad_triage_json_keeps_report(tmp_path):
    agent, *_ = crawl_agent(tmp_path, response(text("not json"), stop="end_turn"))
    result = agent.run()
    assert result.summary == "done" and not result.partial
    assert all(g.explanation == "" for g in result.groups)


def test_triage_refusal_keeps_report(tmp_path):
    agent, *_ = crawl_agent(tmp_path, response(stop="refusal"))
    result = agent.run()
    assert result.summary == "done" and not result.partial


def test_triage_api_error_keeps_report(tmp_path):
    agent, *_ = crawl_agent(tmp_path, anthropic.APIConnectionError(request=REQUEST))
    result = agent.run()
    assert result.summary == "done" and not result.partial


def test_api_error_gives_partial_result_without_triage(tmp_path):
    script = [response(tool_use("open_page", url="https://a.com/")),
              anthropic.APIConnectionError(request=REQUEST)]
    pages = {"https://a.com/": page("https://a.com/", page_errors=["boom"])}
    agent, client, _, events = make_agent(script, pages, tmp_path)
    result = agent.run()
    assert result.partial and result.partial_reason.startswith("API error")
    assert "basics.js-error" in {f.id for f in result.findings}
    assert len(client.calls) == 2
    assert events[-1] == ScanFinished(True, result.partial_reason)


def test_refusal_during_crawl_is_partial(tmp_path):
    agent, client, *_ = make_agent([response(stop="refusal")], {}, tmp_path)
    result = agent.run()
    assert (result.partial, result.partial_reason) == (True, "model declined")
    assert len(client.calls) == 1


def test_ctrl_c_is_partial(tmp_path):
    script = [response(tool_use("open_page", url="https://a.com/")), KeyboardInterrupt()]
    agent, client, *_ = make_agent(script, {"https://a.com/": page("https://a.com/")}, tmp_path)
    result = agent.run()
    assert (result.partial, result.partial_reason) == (True, "interrupted")
    assert len(result.pages) == 1 and len(client.calls) == 2


def test_auth_error_propagates(tmp_path):
    err = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx.Response(401, request=REQUEST), body=None)
    agent, *_ = make_agent([err], {}, tmp_path)
    with pytest.raises(anthropic.AuthenticationError):
        agent.run()


def test_no_findings_means_no_triage_call(tmp_path):
    script = [response(tool_use("finish", summary="nothing to see"))]
    agent, client, *_ = make_agent(script, {}, tmp_path)
    assert agent.run().summary == "nothing to see" and len(client.calls) == 1


def test_non_html_target_is_not_a_page_finding_or_budget(tmp_path):
    from site_doctor.browser import NotAPage

    script = [response(tool_use("open_page", url="https://a.com/")),
              response(tool_use("open_page", url="https://a.com/brochure.pdf")),
              response(tool_use("open_page", url="https://a.com/about")),
              response(tool_use("finish", summary="s"))]
    pages = {"https://a.com/": page("https://a.com/"),
             "https://a.com/brochure.pdf": NotAPage("not an HTML page (application/pdf)", 200),
             "https://a.com/about": page("https://a.com/about")}
    agent, *_ = make_agent(script, pages, tmp_path, max_pages=2)
    result = agent.run()
    pdf = tool_results(agent)[1]
    assert pdf["is_error"] and "check_link" in pdf["data"]["error"]
    assert not any(f.id == "basics.page-load-failed" for f in result.findings)
    assert len(result.pages) == 2  # the PDF didn't use up the 2-page budget


def test_crawl_requests_use_prompt_caching_and_count_cache_tokens(tmp_path):
    from types import SimpleNamespace

    cached = response(stop="end_turn")
    cached.usage = SimpleNamespace(input_tokens=50, output_tokens=10,
                                   cache_read_input_tokens=4000, cache_creation_input_tokens=300)
    agent, client, *_ = make_agent([cached], {}, tmp_path)
    usage = agent.run().usage
    assert client.calls[0]["cache_control"] == {"type": "ephemeral"}
    assert (usage.cache_read_input_tokens, usage.cache_creation_input_tokens) == (4000, 300)

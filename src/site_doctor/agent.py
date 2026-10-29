"""The agent: Claude decides where to look, the code runs every check."""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import anthropic

from .browser import PageLoadError
from .checks import basics, links, run_page_checks, seo
from .config import ScanConfig
from .events import (
    AgentNote,
    Event,
    ScanFinished,
    ScanStarted,
    StepFinished,
    StepStarted,
    TriageStarted,
)
from .models import SEVERITIES, Finding, IssueGroup, PageResult, ScanResult, Usage
from .scoring import score
from .triage import group_findings

BETAS = ["server-side-fallback-2026-07-01"]
MAX_SAME_SITE_LINKS = 80
MAX_EXTERNAL_LINKS = 30


def _tool(name: str, description: str, **props) -> dict:
    required = [k for k in props if not k.startswith("_")]
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {k.lstrip("_"): {"type": "string", "description": v}
                           for k, v in props.items()},
            "required": required,
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool("open_page",
          "Load a page of the site in a real browser and run every check on it (status, JS "
          "errors, accessibility, SEO, performance, security, mobile). Returns issue counts, the "
          "top issues and the page's links. Each new page uses one page from your budget.",
          url="Absolute URL on the site being examined"),
    _tool("check_link",
          "Request a URL without opening it and return its HTTP status. Cheap: use it for "
          "external links, assets and internal links you don't need to open.",
          url="Absolute URL to check", _found_on="URL of the page the link appeared on"),
    _tool("screenshot",
          "Save desktop and phone screenshots of a page as evidence. Use it on pages with "
          "serious issues.",
          url="Absolute URL of the page"),
    _tool("finish",
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


TRIAGE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "issues"],
    "properties": {
        "summary": {"type": "string"},
        "issues": {"type": "array", "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "explanation", "fix"],
            "properties": {"id": {"type": "string"}, "explanation": {"type": "string"},
                           "fix": {"type": "string"}},
        }},
    },
}

TRIAGE_SYSTEM = """You are Site Doctor, writing the report for a website examination.
You get the issues the automated checks found (already de-duplicated and graded) and the \
crawl notes. For every issue, write:
- explanation: one or two plain-English sentences on what's wrong and who it affects.
- fix: a concrete, specific fix a developer can act on (name the header, tag or setting).
Then write summary: three or four sentences for the site owner on overall health and what to \
fix first. Don't change severities, don't invent issues, and keep each issue's id exactly."""


def normalize_url(url: str) -> str:
    p = urlsplit(url.strip())
    path = p.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme.lower()}://{p.netloc.lower()}"


def _slug(url: str) -> str:
    path = urlsplit(url).path.strip("/")
    return re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-")[:40] or "home"


class Agent:
    def __init__(self, client, browser, config: ScanConfig,
                 on_event: Callable[[Event], None] = lambda e: None):
        self.client = client
        self.browser = browser
        self.config = config
        self.emit = on_event
        self.messages: list[dict] = []
        self.pages: dict[str, PageResult] = {}
        self.summaries: dict[str, dict] = {}
        self.link_findings: list[Finding] = []
        self.site_findings: list[Finding] = []
        self.link_status: dict[str, int | str] = {}
        self.screenshots: dict[str, list[str]] = {}
        self.origin = origin_of(normalize_url(config.url))
        self.origin_confirmed = False
        self.steps = 0
        self.usage = Usage()
        self.crawl_summary = ""
        self.finished = False
        self.partial_reason = ""

    # --- Claude -----------------------------------------------------------------------------

    def _create(self, **kw):
        response = self.client.beta.messages.create(
            model=self.config.model, max_tokens=16000, betas=BETAS, fallbacks="default", **kw)
        self.usage.input_tokens += response.usage.input_tokens
        self.usage.output_tokens += response.usage.output_tokens
        return response

    def _crawl(self) -> None:
        self.messages.append({"role": "user",
                              "content": f"Examine this website: {self.config.url}"})
        while not self.finished and self.steps < self.config.max_steps:
            response = self._create(system=system_prompt(self.config), tools=TOOLS,
                                    messages=self.messages,
                                    output_config={"effort": self.config.effort})
            self.messages.append({"role": "assistant", "content": response.content})
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    self.emit(AgentNote(block.text.strip()))
            if response.stop_reason == "refusal":
                self.partial_reason = "model declined"
                return
            if response.stop_reason != "tool_use":
                return

            results = []
            for call in (b for b in response.content if b.type == "tool_use"):
                if self.steps >= self.config.max_steps:
                    break
                results.append(self._run_tool(call))
            self.messages.append({"role": "user", "content": results})

    # --- tools ------------------------------------------------------------------------------

    def _run_tool(self, call) -> dict:
        self.steps += 1
        url = call.input.get("url", "")
        self.emit(StepStarted(self.steps, call.name, url))
        handler = getattr(self, f"_tool_{call.name}", None)
        try:
            if handler is None:
                raise ValueError(f"Unknown tool {call.name}")
            data, summary = handler(**call.input)
            ok = "error" not in data
        except Exception as e:  # a tool failing is information for Claude, not a crash
            data, summary, ok = {"error": f"{type(e).__name__}: {e}"}, "error", False
        self.emit(StepFinished(self.steps, call.name, url, summary if ok else f"⚠ {data['error']}",
                               ok, len(self.pages), self.config.max_pages, self._tally()))
        return {"type": "tool_result", "tool_use_id": call.id,
                "content": json.dumps(data), "is_error": not ok}

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

    def _tool_screenshot(self, url: str):
        n = sum(1 for paths in self.screenshots.values() for p in paths if "desktop" in p) + 1
        run_dir = self.config.run_dir
        views = [("desktop", False)] + ([("mobile", True)] if self.config.mobile else [])
        saved = []
        for view, mobile in views:
            path = run_dir / "screenshots" / f"{n:02d}-{_slug(url)}-{view}.png"
            self.browser.screenshot(url, path, mobile=mobile)
            saved.append(path.relative_to(run_dir).as_posix())
        self.screenshots.setdefault(normalize_url(url), []).extend(saved)
        return {"saved": saved}, f"saved {len(saved)} screenshots"

    def _tool_finish(self, summary: str):
        self.crawl_summary = summary
        self.finished = True
        return {"ok": True}, "done"

    def _explain(self, groups: list[IssueGroup], crawl_summary: str) -> str:
        """Ask Claude to explain each issue. Fills groups in place; returns the overall summary."""
        issues = [{"id": g.id, "category": g.category, "severity": g.severity, "title": g.title,
                   "pages": len(g.urls), "urls": g.urls[:5], "evidence": g.evidence}
                  for g in groups]
        content = (f"Site: {self.config.url}\nCrawl notes: {crawl_summary or '(none)'}\n\n"
                   f"Issues:\n{json.dumps(issues, indent=1)}")
        try:
            response = self._create(
                system=TRIAGE_SYSTEM, messages=[{"role": "user", "content": content}],
                output_config={"effort": self.config.effort,
                               "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}})
            if response.stop_reason == "refusal":
                return crawl_summary
            data = json.loads(next(b.text for b in response.content if b.type == "text"))
        except Exception:  # the report is still useful without explanations
            return crawl_summary
        by_id = {g.id: g for g in groups}
        for item in data.get("issues", []):
            group = by_id.get(item.get("id"))
            if group:
                group.explanation = str(item.get("explanation", ""))
                group.fix = str(item.get("fix", ""))
        return str(data.get("summary") or crawl_summary)

    # --- results ----------------------------------------------------------------------------

    def _all_findings(self) -> list[Finding]:
        found = [f for p in self.pages.values() for f in p.findings]
        found += self.link_findings + self.site_findings
        for f in found:
            f.screenshots = list(self.screenshots.get(normalize_url(f.url), []))
        return found

    def _tally(self) -> dict[str, int]:
        counts = Counter(f.severity for f in self._all_findings())
        return {s: counts.get(s, 0) for s in SEVERITIES}

    def run(self) -> ScanResult:
        started = datetime.now()
        t0 = time.perf_counter()
        self.emit(ScanStarted(self.config.url, self.config.model, self.config.max_pages))
        try:
            self._crawl()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise
        except anthropic.APIError as e:
            self.partial_reason = f"API error: {e}"
        except KeyboardInterrupt:
            self.partial_reason = "interrupted"

        findings = self._all_findings()
        groups = group_findings(findings)
        partial = bool(self.partial_reason)
        summary = self.crawl_summary
        if groups and not partial:
            self.emit(TriageStarted(len(groups)))
            summary = self._explain(groups, summary)
        scores, overall, grade = score(groups)
        self.emit(ScanFinished(partial, self.partial_reason))
        return ScanResult(
            target=self.config.url, model=self.config.model,
            started_at=started.isoformat(timespec="seconds"),
            duration_s=round(time.perf_counter() - t0, 1),
            pages=list(self.pages.values()), findings=findings, groups=groups,
            scores=scores, overall=overall, grade=grade, summary=summary,
            usage=self.usage, partial=partial, partial_reason=self.partial_reason,
        )

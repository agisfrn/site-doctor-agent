"""What both scan modes share: the Claude tool loop (usage, caching, refusals), screenshots,
partial-run handling, the explanation call, and building the final ScanResult."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime

import anthropic

from .config import ScanConfig
from .events import AgentNote, Event, ScanFinished, StepFinished, StepStarted, TriageStarted
from .models import SEVERITIES, Finding, IssueGroup, PageResult, ScanResult, Usage
from .scoring import score
from .triage import group_findings
from .urls import normalize_url, slug

BETAS = ["server-side-fallback-2026-07-01"]


def make_tool(name: str, description: str, **props) -> dict:
    """A tool with string parameters; a parameter named with a leading _ is optional."""
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


class ToolAgent:
    """Base for Claude-driven work. Subclasses define `_tool_<name>` methods and `_status`."""

    def __init__(self, client, browser, config: ScanConfig,
                 on_event: Callable[[Event], None] = lambda e: None):
        self.client = client
        self.browser = browser
        self.config = config
        self.emit = on_event
        self.messages: list[dict] = []
        self.steps = 0
        self.usage = Usage()
        self.notes = ""  # Claude's summary from the finish tool
        self.finished = False
        self.partial_reason = ""
        self.screenshots: dict[str, list[str]] = {}

    # --- Claude -----------------------------------------------------------------------------

    def _create(self, **kw):
        response = self.client.beta.messages.create(
            model=self.config.model, max_tokens=16000, betas=BETAS, fallbacks="default", **kw)
        u = response.usage
        self.usage.input_tokens += u.input_tokens
        self.usage.output_tokens += u.output_tokens
        self.usage.cache_read_input_tokens += getattr(u, "cache_read_input_tokens", 0) or 0
        self.usage.cache_creation_input_tokens += getattr(u, "cache_creation_input_tokens", 0) or 0
        return response

    def _loop(self, system: str, tools: list[dict], first_message: str, max_steps: int) -> None:
        self.messages.append({"role": "user", "content": first_message})
        while not self.finished and self.steps < max_steps:
            # Each step resends the whole conversation; caching makes the repeated prefix cheap.
            response = self._create(system=system, tools=tools, messages=self.messages,
                                    output_config={"effort": self.config.effort},
                                    cache_control={"type": "ephemeral"})
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
                if self.steps >= max_steps:
                    break
                results.append(self._run_tool(call))
            self.messages.append({"role": "user", "content": results})

    def guard(self, fn: Callable[[], object]) -> None:
        """Run `fn`; API failures and Ctrl+C end the run early (a partial report) instead of
        crashing. Credential problems still stop everything: there's nothing to report."""
        try:
            fn()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise
        except anthropic.APIError as e:
            self.partial_reason = f"API error: {e}"
        except KeyboardInterrupt:
            self.partial_reason = "interrupted"

    # --- tools ------------------------------------------------------------------------------

    def _status(self) -> tuple[int, int, dict[str, int]]:
        """(pages used, page budget, findings per severity) for progress events."""
        return 0, 0, dict.fromkeys(SEVERITIES, 0)

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
        used, budget, tally = self._status()
        self.emit(StepFinished(self.steps, call.name, url, summary if ok else f"⚠ {data['error']}",
                               ok, used, budget, tally))
        return {"type": "tool_result", "tool_use_id": call.id,
                "content": json.dumps(data), "is_error": not ok}

    def _tool_screenshot(self, url: str):
        n = sum(1 for paths in self.screenshots.values() for p in paths if "desktop" in p) + 1
        run_dir = self.config.run_dir
        views = [("desktop", False)] + ([("mobile", True)] if self.config.mobile else [])
        saved = []
        for view, mobile in views:
            path = run_dir / "screenshots" / f"{n:02d}-{slug(url)}-{view}.png"
            self.browser.screenshot(url, path, mobile=mobile)
            saved.append(path.relative_to(run_dir).as_posix())
        self.screenshots.setdefault(normalize_url(url), []).extend(saved)
        return {"saved": saved}, f"saved {len(saved)} screenshots"

    def _tool_finish(self, summary: str):
        self.notes = summary
        self.finished = True
        return {"ok": True}, "done"

    # --- the explanation call ---------------------------------------------------------------

    def explain(self, groups: list[IssueGroup], notes: str) -> str:
        """Ask Claude to explain each issue. Fills groups in place; returns the overall summary."""
        issues = [{"id": g.id, "category": g.category, "severity": g.severity, "title": g.title,
                   "pages": len(g.urls), "urls": g.urls[:5], "evidence": g.evidence}
                  for g in groups]
        content = (f"Site: {self.config.url}\nCrawl notes: {notes or '(none)'}\n\n"
                   f"Issues:\n{json.dumps(issues, indent=1)}")
        try:
            response = self._create(
                system=TRIAGE_SYSTEM, messages=[{"role": "user", "content": content}],
                output_config={"effort": self.config.effort,
                               "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}})
            if response.stop_reason == "refusal":
                return notes
            data = json.loads(next(b.text for b in response.content if b.type == "text"))
        except Exception:  # the report is still useful without explanations
            return notes
        by_id = {g.id: g for g in groups}
        for item in data.get("issues", []):
            group = by_id.get(item.get("id"))
            if group:
                group.explanation = str(item.get("explanation", ""))
                group.fix = str(item.get("fix", ""))
        return str(data.get("summary") or notes)


def build_result(agent: ToolAgent, *, started: datetime, t0: float, pages: list[PageResult],
                 findings: list[Finding], mode: str, links_checked: int = 0,
                 page_groups: list[dict] | None = None) -> ScanResult:
    """Group, explain (unless the run stopped early), score, and package the scan."""
    for f in findings:
        f.screenshots = list(agent.screenshots.get(normalize_url(f.url), []))
    groups = group_findings(findings)
    partial = bool(agent.partial_reason)
    summary = agent.notes
    if groups and not partial:
        agent.emit(TriageStarted(len(groups)))
        summary = agent.explain(groups, summary)
    scores, overall, grade = score(groups)
    agent.emit(ScanFinished(partial, agent.partial_reason))
    config = agent.config
    return ScanResult(
        target=config.url, model=config.model,
        started_at=started.isoformat(timespec="seconds"),
        duration_s=round(time.perf_counter() - t0, 1),
        pages=pages, findings=findings, groups=groups,
        scores=scores, overall=overall, grade=grade, summary=summary,
        usage=agent.usage, partial=partial, partial_reason=agent.partial_reason,
        mode=mode, links_checked=links_checked, page_groups=page_groups or [],
    )

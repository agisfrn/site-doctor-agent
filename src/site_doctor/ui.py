"""Terminal output: a Rich live view for demos, plain lines for logs and CI."""

from __future__ import annotations

import sys
from collections import deque
from typing import TextIO
from urllib.parse import urlsplit

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from .events import (
    AgentNote,
    Event,
    ScanFinished,
    ScanStarted,
    StepFinished,
    StepStarted,
    TriageStarted,
)
from .models import SEVERITIES

SEVERITY_STYLE = {"critical": "bold red", "high": "dark_orange", "medium": "yellow",
                  "low": "dodger_blue1"}
LOG_LINES = 12


class _Formatter:
    """Shared wording for both UIs."""

    origin = ""

    def short(self, url: str) -> str:
        p = urlsplit(url)
        if f"{p.scheme}://{p.netloc}" == self.origin:
            return (p.path or "/") + (f"?{p.query}" if p.query else "")
        return url

    def step_line(self, e: StepFinished) -> str:
        return f"[{e.step:02d}] {e.tool} {self.short(e.url)} → {e.summary}"

    @staticmethod
    def note_line(e: AgentNote) -> str:
        first = e.text.strip().splitlines()[0] if e.text.strip() else ""
        return f"     💬 {first[:120]}"

    @staticmethod
    def triage_line(e: TriageStarted) -> str:
        return f"🧠 Writing explanations for {e.groups} issues…"

    def remember(self, e: ScanStarted) -> None:
        p = urlsplit(e.target)
        self.origin = f"{p.scheme}://{p.netloc}"


class PlainUI(_Formatter):
    def __init__(self, out: TextIO = sys.stdout):
        self.out = out

    def __enter__(self) -> PlainUI:
        return self

    def __exit__(self, *exc) -> None:
        pass

    def _print(self, line: str) -> None:
        print(line, file=self.out, flush=True)

    def handle(self, e: Event) -> None:
        if isinstance(e, ScanStarted):
            self.remember(e)
            self._print(f"🩺 Site Doctor examining {e.target}  "
                        f"({e.model}, up to {e.max_pages} pages)")
        elif isinstance(e, AgentNote):
            self._print(self.note_line(e))
        elif isinstance(e, StepFinished):
            self._print(self.step_line(e))
        elif isinstance(e, TriageStarted):
            self._print(self.triage_line(e))
        elif isinstance(e, ScanFinished) and e.partial:
            self._print(f"⚠ Stopped early: {e.reason}")


class LiveUI(_Formatter):
    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.target = self.model = ""
        self.lines: deque[Text] = deque(maxlen=LOG_LINES)
        self.current = ""
        self.pages = (0, 0)
        self.tally = dict.fromkeys(SEVERITIES, 0)
        self._live: Live | None = None

    def __enter__(self) -> LiveUI:
        self._live = Live(self.render(), console=self.console, refresh_per_second=8,
                          transient=False)
        self._live.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._live:
            self.current = ""
            self._live.update(self.render())
            self._live.__exit__(*exc)

    def render(self) -> Group:
        header = Text.assemble(("🩺 Site Doctor  ", "bold"), (self.target, "cyan"),
                               ("  ·  ", "dim"), (self.model, "dim"))
        log = Text("\n").join(self.lines) if self.lines else Text("Starting…", style="dim")
        footer = Text.assemble((f"pages {self.pages[0]}/{self.pages[1]}", "bold"), "   ")
        for s in SEVERITIES:
            footer.append("● ", style=SEVERITY_STYLE[s])
            footer.append(f"{self.tally[s]} {s}   ")
        parts = [Panel(header, border_style="cyan"), log]
        if self.current:
            parts.append(Text(f"  ⟳ {self.current}", style="dim italic"))
        parts.append(Panel(footer, border_style="dim"))
        return Group(*parts)

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self.render())

    def handle(self, e: Event) -> None:
        if isinstance(e, ScanStarted):
            self.remember(e)
            self.target, self.model, self.pages = e.target, e.model, (0, e.max_pages)
        elif isinstance(e, AgentNote):
            self.lines.append(Text(self.note_line(e), style="dim"))
        elif isinstance(e, StepStarted):
            self.current = f"{e.tool} {self.short(e.url)}"
        elif isinstance(e, StepFinished):
            line = Text(self.step_line(e), style=None if e.ok else "yellow")
            if "✗" in e.summary:
                line.stylize("red", line.plain.index("✗"))
            self.lines.append(line)
            self.current = ""
            self.pages = (e.pages_used, e.max_pages)
            self.tally = dict(e.tally)
        elif isinstance(e, TriageStarted):
            self.current = self.triage_line(e)
        elif isinstance(e, ScanFinished):
            self.current = ""
            if e.partial:
                self.lines.append(Text(f"⚠ Stopped early: {e.reason}", style="yellow"))
        self._refresh()


def make_ui(plain: bool) -> PlainUI | LiveUI:
    if plain or not sys.stdout.isatty():
        return PlainUI()
    return LiveUI()

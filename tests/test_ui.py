import io

from rich.console import Console

from site_doctor.events import (
    AgentNote,
    ScanFinished,
    ScanStarted,
    StepFinished,
    StepStarted,
    TriageStarted,
)
from site_doctor.ui import LiveUI, PlainUI, make_ui

TALLY = {"critical": 1, "high": 2, "medium": 0, "low": 3}
EVENTS = [
    ScanStarted("https://a.com/", "claude-opus-5-5", 15),
    AgentNote("Starting with the home page.\nSecond line is dropped."),
    StepStarted(7, "open_page", "https://a.com/pricing"),
    StepFinished(7, "open_page", "https://a.com/pricing", "200 · 1.2s · 3 issues", True, 3, 15,
                 TALLY),
    StepFinished(8, "check_link", "https://other.com/x", "404 ✗ broken", True, 3, 15, TALLY),
    TriageStarted(12),
    ScanFinished(False, ""),
]


def test_plain_ui_formats_steps():
    out = io.StringIO()
    ui = PlainUI(out)
    for e in EVENTS:
        ui.handle(e)
    text = out.getvalue()
    assert "[07] open_page /pricing → 200 · 1.2s · 3 issues" in text
    assert "[08] check_link https://other.com/x → 404 ✗ broken" in text
    assert "💬 Starting with the home page." in text and "Second line" not in text
    assert "Writing explanations for 12 issues" in text


def test_live_ui_renders_log_and_tally():
    console = Console(file=io.StringIO(), width=120, force_terminal=True, color_system=None)
    ui = LiveUI(console)
    for e in EVENTS:
        ui.handle(e)
    console.print(ui.render())
    text = console.file.getvalue()
    assert "https://a.com/" in text and "claude-opus-5-5" in text
    assert "[07] open_page /pricing" in text
    assert "pages 3/15" in text and "1 critical" in text and "3 low" in text


def test_make_ui_falls_back_to_plain_when_not_a_terminal():
    assert isinstance(make_ui(plain=True), PlainUI)

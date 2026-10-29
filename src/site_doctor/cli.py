"""The `site-doctor` command."""

from __future__ import annotations

import json
import logging
import sys
import webbrowser
from enum import StrEnum
from pathlib import Path

import anthropic
import typer
from playwright.sync_api import Error as PlaywrightError
from rich.console import Console

from . import __version__, runner
from .browser import Browser
from .config import ScanConfig, normalize_target
from .demo_server import serve
from .models import ScanResult
from .report import write_report
from .ui import make_ui

app = typer.Typer(add_completion=False, no_args_is_help=True, rich_markup_mode="rich",
                  help="🩺 An AI agent that examines a website and writes a scored report.")

NO_CREDENTIALS = ("No Anthropic credentials found. Set ANTHROPIC_API_KEY "
                  "(or run `ant auth login`).")
NO_CHROMIUM = "Chromium isn't installed. Run: playwright install chromium"


class Effort(StrEnum):
    low = "low"
    medium = "medium"
    high = "high"
    xhigh = "xhigh"
    max = "max"


def _utf8_output() -> None:
    """Windows consoles often default to cp1252, which can't print the emoji we use."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")


class _DropKeyPrecedenceNotice(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return "takes precedence over the SDK's profile" not in record.getMessage()


def _quiet_sdk_notices() -> None:
    """The SDK warns when an API key shadows a saved profile; harmless, and noisy in a demo."""
    logging.getLogger("anthropic.lib.credentials._auth").addFilter(_DropKeyPrecedenceNotice())


def main() -> None:
    _utf8_output()
    _quiet_sdk_notices()
    app()


def _fail(message: str) -> None:
    typer.echo(f"✗ {message}", err=True)
    raise typer.Exit(code=1)


def _preflight(client, model: str) -> None:
    """One cheap request before crawling, so a bad key or model fails now, not after the crawl."""
    try:
        client.models.retrieve(model)
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.CredentialsError):
        _fail(NO_CREDENTIALS)
    except anthropic.NotFoundError:
        _fail(f"Unknown model: {model}. Check --model (e.g. claude-opus-5-5).")
    except anthropic.APIError:
        pass  # a temporary problem; the scan itself will retry and report it


def run_scan(config: ScanConfig, plain: bool, open_report: bool = False) -> Path:
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError:
        _fail(NO_CREDENTIALS)
    _preflight(client, config.model)
    try:
        with (Browser(mobile=config.mobile, concurrency=config.concurrency) as browser,
              make_ui(plain) as ui):
            result = runner.scan(client, browser, config, ui.handle)
            examined = any(p.error is None for p in result.pages)
            # Saved before the browser closes: closing after Ctrl+C can be slow.
            path = write_report(result, config.run_dir) if examined else None
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.CredentialsError):
        _fail(NO_CREDENTIALS)
    except PlaywrightError as e:
        if "Executable doesn't exist" in str(e):
            _fail(NO_CHROMIUM)
        raise

    if not examined:
        # Nothing was examined (bad --model, network down...): a grade would be a lie.
        _fail(f"The scan stopped before any page was examined: {result.partial_reason}")
    console = Console()
    if result.partial:
        console.print("[yellow]⚠ Partial report: the scan stopped early "
                      f"({result.partial_reason}).")
    console.print(f"\n[bold green]✓[/] Grade [bold]{result.grade}[/] · {result.overall}/100 · "
                  f"{len(result.groups)} issues on {len(result.pages)} pages")
    console.print(f"  Report: [link={path.resolve().as_uri()}]{path}[/link]")
    if open_report:
        webbrowser.open(path.resolve().as_uri())
    return path


class Mode(StrEnum):
    full = "full"
    smart = "smart"


# mode: (default --max-pages, highest allowed)
PAGE_LIMITS = {Mode.full: (200, 1000), Mode.smart: (15, 100)}

ModeOpt = typer.Option(Mode.full, "--mode",
                       help="full: crawl every page, then Claude analyses. "
                            "smart: Claude picks a sample of pages to open.")
MaxPages = typer.Option(None, "--max-pages", min=1,
                        help="Most pages to open (default 200 in full mode, 15 in smart).")
ConcurrencyOpt = typer.Option(4, "--concurrency", min=1, max=16,
                              help="Pages loaded at the same time.")
RobotsOpt = typer.Option(False, "--ignore-robots", help="Crawl pages robots.txt disallows.")
EffortOpt = typer.Option(Effort.medium, "--effort", help="How hard Claude thinks.")
ModelOpt = typer.Option("claude-opus-5-5", "--model", help="Claude model to use.")
MobileOpt = typer.Option(True, "--mobile/--no-mobile", help="Also check a phone-size view.")
OutOpt = typer.Option(Path("reports"), "--out", help="Folder for reports.")
PlainOpt = typer.Option(False, "--plain", help="Plain line output instead of the live view.")


def _config(url: str, mode: Mode, max_pages: int | None, **kw) -> ScanConfig:
    default, limit = PAGE_LIMITS[mode]
    if max_pages is not None and max_pages > limit:
        raise typer.BadParameter(f"{mode.value} mode opens at most {limit} pages.",
                                 param_hint="--max-pages")
    return ScanConfig(url=url, mode=mode.value, max_pages=max_pages or default, **kw)


@app.command()
def scan(
    url: str = typer.Argument(..., help="Website to examine, e.g. https://example.com"),
    mode: Mode = ModeOpt, max_pages: int = MaxPages, concurrency: int = ConcurrencyOpt,
    ignore_robots: bool = RobotsOpt, effort: Effort = EffortOpt, model: str = ModelOpt,
    mobile: bool = MobileOpt, out: Path = OutOpt, plain: bool = PlainOpt,
    open_report: bool = typer.Option(False, "--open", help="Open the report when done."),
) -> None:
    """Examine a website and write a report."""
    config = _config(normalize_target(url), mode, max_pages, concurrency=concurrency,
                     ignore_robots=ignore_robots, effort=effort.value, model=model,
                     mobile=mobile, out_dir=out)
    run_scan(config, plain, open_report)


@app.command()
def demo(
    mode: Mode = ModeOpt, max_pages: int = MaxPages, concurrency: int = ConcurrencyOpt,
    effort: Effort = EffortOpt, model: str = ModelOpt,
    mobile: bool = MobileOpt, out: Path = OutOpt, plain: bool = PlainOpt,
    open_report: bool = typer.Option(True, "--open/--no-open", help="Open the report when done."),
) -> None:
    """Examine the bundled demo shop, a local site with planted bugs."""
    with serve() as url:
        config = _config(url + "/", mode, max_pages, concurrency=concurrency,
                         effort=effort.value, model=model, mobile=mobile, out_dir=out)
        run_scan(config, plain, open_report)


@app.command()
def render(
    findings_json: Path = typer.Argument(..., exists=True, dir_okay=False,
                                         help="A findings.json from an earlier scan."),
    out: Path = typer.Option(None, "--out", help="Folder to write into (default: same folder)."),
) -> None:
    """Rebuild report.html from findings.json, without calling the API."""
    result = ScanResult.from_dict(json.loads(findings_json.read_text(encoding="utf-8")))
    path = write_report(result, out or findings_json.parent)
    typer.echo(f"✓ Report: {path}")


@app.callback(invoke_without_command=True)
def _version(version: bool = typer.Option(False, "--version", help="Show the version.")) -> None:
    if version:
        typer.echo(f"site-doctor {__version__}")
        raise typer.Exit()

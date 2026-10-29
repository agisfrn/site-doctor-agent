"""The `site-doctor` command."""

from __future__ import annotations

import json
import sys
import webbrowser
from enum import StrEnum
from pathlib import Path

import anthropic
import typer
from playwright.sync_api import Error as PlaywrightError
from rich.console import Console

from . import __version__
from .agent import Agent
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


def main() -> None:
    _utf8_output()
    app()


def _fail(message: str) -> None:
    typer.echo(f"✗ {message}", err=True)
    raise typer.Exit(code=1)


def run_scan(config: ScanConfig, plain: bool, open_report: bool = False) -> Path:
    try:
        client = anthropic.Anthropic()
    except anthropic.AnthropicError:
        _fail(NO_CREDENTIALS)
    try:
        with Browser(mobile=config.mobile) as browser, make_ui(plain) as ui:
            result = Agent(client, browser, config, on_event=ui.handle).run()
    except (anthropic.AuthenticationError, anthropic.PermissionDeniedError,
            anthropic.CredentialsError):
        _fail(NO_CREDENTIALS)
    except PlaywrightError as e:
        if "Executable doesn't exist" in str(e):
            _fail(NO_CHROMIUM)
        raise

    path = write_report(result, config.run_dir)
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


MaxPages = typer.Option(15, "--max-pages", min=1, max=100, help="Most pages to open.")
EffortOpt = typer.Option(Effort.medium, "--effort", help="How hard Claude thinks.")
ModelOpt = typer.Option("claude-opus-5-5", "--model", help="Claude model to use.")
MobileOpt = typer.Option(True, "--mobile/--no-mobile", help="Also check a phone-size view.")
OutOpt = typer.Option(Path("reports"), "--out", help="Folder for reports.")
PlainOpt = typer.Option(False, "--plain", help="Plain line output instead of the live view.")


@app.command()
def scan(
    url: str = typer.Argument(..., help="Website to examine, e.g. https://example.com"),
    max_pages: int = MaxPages, effort: Effort = EffortOpt, model: str = ModelOpt,
    mobile: bool = MobileOpt, out: Path = OutOpt, plain: bool = PlainOpt,
    open_report: bool = typer.Option(False, "--open", help="Open the report when done."),
) -> None:
    """Examine a website and write a report."""
    config = ScanConfig(url=normalize_target(url), max_pages=max_pages, effort=effort.value,
                        model=model, mobile=mobile, out_dir=out)
    run_scan(config, plain, open_report)


@app.command()
def demo(
    max_pages: int = MaxPages, effort: Effort = EffortOpt, model: str = ModelOpt,
    mobile: bool = MobileOpt, out: Path = OutOpt, plain: bool = PlainOpt,
    open_report: bool = typer.Option(True, "--open/--no-open", help="Open the report when done."),
) -> None:
    """Examine the bundled demo shop, a local site with planted bugs."""
    with serve() as url:
        config = ScanConfig(url=url + "/", max_pages=max_pages, effort=effort.value,
                            model=model, mobile=mobile, out_dir=out)
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

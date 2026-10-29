import json

from typer.testing import CliRunner

from site_doctor import cli
from site_doctor.cli import app
from tests.fakes import FakeBrowserCM, browser_raising, raising_auth_error_client

runner = CliRunner()


def test_help_lists_commands():
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert all(cmd in res.output for cmd in ["scan", "demo", "render"])


def test_render_rebuilds_report(tmp_path, sample_result):
    src = tmp_path / "findings.json"
    src.write_text(json.dumps(sample_result.to_dict()), encoding="utf-8")
    res = runner.invoke(app, ["render", str(src), "--out", str(tmp_path / "out")])
    assert res.exit_code == 0, res.output
    assert (tmp_path / "out" / "report.html").exists()


def test_scan_without_credentials_explains(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "Browser", FakeBrowserCM)
    monkeypatch.setattr(cli.anthropic, "Anthropic", raising_auth_error_client)
    res = runner.invoke(app, ["scan", "example.com", "--out", str(tmp_path), "--plain"])
    assert res.exit_code == 1 and "ANTHROPIC_API_KEY" in res.output


def test_client_construction_failure_explains(monkeypatch, tmp_path):
    def no_key():
        raise cli.anthropic.AnthropicError("The api_key client option must be set")

    monkeypatch.setattr(cli.anthropic, "Anthropic", no_key)
    res = runner.invoke(app, ["scan", "example.com", "--out", str(tmp_path), "--plain"])
    assert res.exit_code == 1 and "ANTHROPIC_API_KEY" in res.output


def test_missing_chromium_explains(monkeypatch, tmp_path):
    missing = browser_raising("Executable doesn't exist at /ms-playwright/chromium")
    monkeypatch.setattr(cli, "Browser", missing)
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda: object())
    res = runner.invoke(app, ["scan", "example.com", "--out", str(tmp_path), "--plain"])
    assert res.exit_code == 1 and "playwright install chromium" in res.output


def test_bad_effort_is_rejected(tmp_path):
    res = runner.invoke(app, ["scan", "example.com", "--effort", "huge", "--out", str(tmp_path)])
    assert res.exit_code != 0


def test_output_survives_a_non_utf8_console(monkeypatch):
    import io
    import sys

    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    cli._utf8_output()
    print("🩺 ✓ ✗", flush=True)
    assert "🩺".encode() in raw.getvalue()


def test_missing_credentials_file_explains(monkeypatch, tmp_path):
    from tests.fakes import FakeClient

    error = cli.anthropic.CredentialsError("Credentials file not found at x (profile 'default').")
    monkeypatch.setattr(cli, "Browser", FakeBrowserCM)
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda: FakeClient([error]))
    res = runner.invoke(app, ["scan", "example.com", "--out", str(tmp_path), "--plain"])
    assert res.exit_code == 1 and "ANTHROPIC_API_KEY" in res.output


def test_scan_that_fails_before_any_page_exits_1(monkeypatch, tmp_path):
    import httpx2

    from tests.fakes import FakeClient

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = cli.anthropic.NotFoundError(
        "model: claude-typo", response=httpx2.Response(404, request=request), body=None)
    monkeypatch.setattr(cli, "Browser", FakeBrowserCM)
    monkeypatch.setattr(cli.anthropic, "Anthropic", lambda: FakeClient([error]))
    res = runner.invoke(app, ["scan", "example.com", "--out", str(tmp_path), "--plain"])
    assert res.exit_code == 1
    assert "claude-typo" in res.output and "Grade" not in res.output


def test_only_the_key_precedence_notice_is_silenced(caplog):
    import logging

    cli._quiet_sdk_notices()
    log = logging.getLogger("anthropic.lib.credentials._auth")  # where the SDK logs it
    with caplog.at_level(logging.WARNING, logger="anthropic"):
        log.warning("%s is set and takes precedence over the SDK's profile / federation "
                    "auto-discovery; unset %s to use the auto-discovered credential.",
                    "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")
        log.warning("Retrying request after rate limit")
    assert [r.getMessage() for r in caplog.records] == ["Retrying request after rate limit"]

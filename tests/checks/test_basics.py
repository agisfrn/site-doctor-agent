import pytest

from site_doctor.checks import basics
from tests.checks.helpers import make_capture


def test_clean_capture_has_no_basics_findings():
    assert basics.check(make_capture()) == []


@pytest.mark.parametrize("status,fid,sev", [
    (503, "basics.server-error", "critical"),
    (404, "basics.client-error", "high"),
])
def test_bad_status(status, fid, sev):
    [f] = basics.check(make_capture(status=status))
    assert (f.id, f.severity) == (fid, sev)


def test_js_and_console_errors():
    ids = [f.id for f in basics.check(make_capture(page_errors=["x"], console_errors=["a", "b"]))]
    assert ids == ["basics.js-error", "basics.console-error"]


def test_blank_title():
    [f] = basics.check(make_capture(title="   "))
    assert (f.id, f.severity) == ("basics.missing-title", "medium")


def test_load_failure_severity():
    assert basics.load_failure("u", "boom", is_start=True).severity == "critical"
    assert basics.load_failure("u", "boom", is_start=False).severity == "high"

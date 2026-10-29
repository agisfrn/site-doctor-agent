import anthropic
import httpx2
import pytest

from site_doctor.config import ScanConfig
from site_doctor.session import ToolAgent
from tests.fakes import FakeBrowser, FakeClient

REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def raise_(error):
    raise error


def make():
    return ToolAgent(FakeClient([]), FakeBrowser(), ScanConfig(url="https://a.com/"))


def test_guard_turns_api_errors_and_ctrl_c_into_partial_runs():
    agent = make()
    agent.guard(lambda: raise_(anthropic.APIConnectionError(request=REQUEST)))
    assert agent.partial_reason.startswith("API error")
    agent = make()
    agent.guard(lambda: raise_(KeyboardInterrupt()))
    assert agent.partial_reason == "interrupted"


def test_guard_lets_credential_errors_through():
    error = anthropic.AuthenticationError(
        "bad key", response=httpx2.Response(401, request=REQUEST), body=None)
    with pytest.raises(anthropic.AuthenticationError):
        make().guard(lambda: raise_(error))

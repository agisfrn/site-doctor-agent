"""Stand-ins for the Anthropic client and the browser, so the agent can be tested offline."""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

from site_doctor.browser import PageLoadError

_ids = itertools.count(1)


def tool_use(name, **input):
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(_ids)}", name=name, input=input)


def text(t):
    return SimpleNamespace(type="text", text=t)


def response(*blocks, stop="tool_use"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class FakeClient:
    """Plays back scripted responses. An Exception in the script is raised instead."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=self)

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw.get("messages", []))})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeBrowser:
    def __init__(self, pages=None, statuses=None):
        self.pages = pages or {}
        self.statuses = statuses or {}
        self.captured, self.fetched, self.shots = [], [], []

    def capture(self, url):
        self.captured.append(url)
        page = self.pages.get(url)
        if page is None:
            raise PageLoadError(f"net::ERR_NAME_NOT_RESOLVED at {url}")
        if isinstance(page, Exception):
            raise page
        return page

    def fetch_status(self, url):
        self.fetched.append(url)
        return self.statuses.get(url, 200)

    def screenshot(self, url, path: Path, mobile=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        self.shots.append((url, mobile))
        return path

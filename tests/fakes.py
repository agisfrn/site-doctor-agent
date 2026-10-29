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

    def __init__(self, responses, model_error=None):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=self)
        self.model_error = model_error
        self.models = SimpleNamespace(retrieve=self._retrieve_model)

    def _retrieve_model(self, model):
        if self.model_error:
            raise self.model_error
        return SimpleNamespace(id=model)

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


def browser_raising(message: str):
    from playwright.sync_api import Error

    class _Browser(FakeBrowserCM):
        def __enter__(self):
            raise Error(message)

    return _Browser


def raising_auth_error_client():
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=request), body=None)
    return FakeClient([error])


class FakeCrawlBrowser(FakeBrowser):
    """FakeBrowser plus the batch methods the crawler uses.

    pages: url -> PageCapture | Exception; texts: url -> (status, text) for robots/sitemaps;
    interrupt_on_batch: raise KeyboardInterrupt on that capture_many call (1-based)."""

    def __init__(self, pages=None, statuses=None, texts=None, interrupt_on_batch=None):
        super().__init__(pages, statuses)
        self.texts = texts or {}
        self.interrupt_on_batch = interrupt_on_batch
        self.batches = 0

    def capture_many(self, urls):
        self.batches += 1
        if self.batches == self.interrupt_on_batch:
            raise KeyboardInterrupt
        out = {}
        for url in urls:
            try:
                out[url] = self.capture(url)
            except PageLoadError as e:
                out[url] = e
        return out

    def fetch_many(self, urls):
        return {u: self.fetch_status(u) for u in urls}

    def fetch_text(self, url):
        return self.texts.get(url, (404, ""))


class FakeBrowserCM(FakeCrawlBrowser):
    """A FakeBrowser that can stand in for `with Browser(mobile=...) as b`."""

    def __init__(self, mobile=True, pages=None, statuses=None, **kw):
        super().__init__(pages, statuses)
        self.kw = kw
        self.mobile = mobile

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

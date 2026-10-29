"""Site Doctor: an AI agent that crawls a website and writes report.html.

Usage:  python site_doctor.py https://example.com
"""
import html
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

# Use the browsers installed next to this script (D:\site-doctor\.browsers), not the C: default.
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(Path(__file__).parent / ".browsers"))

import anthropic
from playwright.sync_api import sync_playwright

MODEL = "claude-opus-5-5"
MAX_PAGES = 15
SHOTS = Path("screenshots")

TOOLS = [
    {"name": "open_page",
     "description": "Open a URL in a real browser. Returns HTTP status, title, load time, "
                    "console errors and the links found on the page.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}},
                      "required": ["url"], "additionalProperties": False}},
    {"name": "check_link",
     "description": "Request a URL without rendering it and return its HTTP status code. "
                    "Use for links you don't need to open (images, PDFs, external sites).",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}},
                      "required": ["url"], "additionalProperties": False}},
    {"name": "screenshot",
     "description": "Take a full-page screenshot of a URL as evidence of a problem. Returns the PNG path.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}},
                      "required": ["url"], "additionalProperties": False}},
]

# The agent's final answer must match this schema, so the report never has to parse free text.
REPORT_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "problems"],
    "properties": {
        "summary": {"type": "string"},
        "problems": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "url", "severity", "details", "screenshot"],
            "properties": {
                "title": {"type": "string"},
                "url": {"type": "string"},
                "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                "details": {"type": "string"},
                "screenshot": {"type": "string", "description": "PNG path from the screenshot tool, or empty"},
            }}},
    },
}

SYSTEM = f"""You are Site Doctor, a website QA agent. Explore the site the user gives you and find real problems:
broken links (4xx/5xx or unreachable), JavaScript console errors, slow pages (load over 3s), missing or
empty titles, and anything else that looks broken. You decide which pages to visit. Stay on the same domain
for open_page and visit at most {MAX_PAGES} pages; prefer important pages (nav, footer, main sections).
Use check_link for external links and assets. Take a screenshot of every page that has a problem.
When finished, reply with the report JSON. Severity guide: critical = site or key page down,
high = broken link or JS error on a main page, medium = slow page or minor broken link, low = cosmetic/SEO."""


class Browser:
    def __init__(self, pw):
        self.browser = pw.chromium.launch(channel="chromium")  # full Chromium build, run headless
        self.ctx = self.browser.new_context(viewport={"width": 1280, "height": 800})
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("console", lambda m: m.type == "error" and self.errors.append(m.text))
        self.page.on("pageerror", lambda e: self.errors.append(str(e)))
        self.pages_opened = 0
        self.shots = 0

    def open_page(self, url):
        if self.pages_opened >= MAX_PAGES:
            return {"error": f"Page budget of {MAX_PAGES} used up. Write your report now."}
        self.pages_opened += 1
        self.errors.clear()
        start = time.perf_counter()
        resp = self.page.goto(url, wait_until="load", timeout=30000)
        load = round(time.perf_counter() - start, 2)
        links = self.page.eval_on_selector_all(
            "a[href]", "els => [...new Set(els.map(a => a.href).filter(h => h.startsWith('http')))]")
        return {"url": self.page.url, "status": resp.status if resp else None,
                "title": self.page.title(), "load_time_s": load,
                "console_errors": self.errors[:20], "links": links[:60],
                "pages_left": MAX_PAGES - self.pages_opened}

    def check_link(self, url):
        r = self.ctx.request.get(url, timeout=15000, max_redirects=5)
        return {"url": url, "status": r.status}

    def screenshot(self, url):
        if self.page.url.rstrip("/") != url.rstrip("/"):
            self.page.goto(url, wait_until="load", timeout=30000)
        self.shots += 1
        path = SHOTS / f"shot_{self.shots:02d}_{urlparse(url).netloc}.png"
        self.page.screenshot(path=str(path), full_page=True)
        return {"path": path.as_posix()}

    def run(self, name, args):
        try:
            return getattr(self, name)(**args)
        except Exception as e:  # page errors are findings, not crashes
            return {"error": f"{type(e).__name__}: {str(e).splitlines()[0]}"}


def show_result(name, result):
    if "error" in result:
        return f"⚠  {result['error']}"
    if name == "open_page":
        return (f"status {result['status']} · {result['load_time_s']}s · "
                f"{len(result['console_errors'])} console errors · {len(result['links'])} links · "
                f"title: {result['title']!r}")
    if name == "check_link":
        return f"status {result['status']}" + ("  ✗ BROKEN" if result["status"] >= 400 else "  ✓")
    return f"saved {result['path']}"


def run_agent(start_url, browser):
    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": f"Check this website: {start_url}"}]
    step = 0
    while True:
        response = client.beta.messages.create(
            model=MODEL, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": REPORT_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "refusal":
            sys.exit("The model declined this request.")
        if response.stop_reason != "tool_use":
            text = next(b.text for b in response.content if b.type == "text")
            return json.loads(text)

        results = []
        for call in (b for b in response.content if b.type == "tool_use"):
            step += 1
            print(f"\n[{step:02d}] 🩺 {call.name}  {call.input.get('url', '')}")
            result = browser.run(call.name, call.input)
            print(f"     → {show_result(call.name, result)}")
            results.append({"type": "tool_result", "tool_use_id": call.id,
                            "content": json.dumps(result), "is_error": "error" in result})
        messages.append({"role": "user", "content": results})


def write_report(url, report, pages):
    colors = {"critical": "#b91c1c", "high": "#ea580c", "medium": "#ca8a04", "low": "#2563eb"}
    order = list(colors)
    problems = sorted(report["problems"], key=lambda p: order.index(p["severity"]))
    cards = []
    for p in problems:
        img = (f'<a href="{html.escape(p["screenshot"])}"><img src="{html.escape(p["screenshot"])}"></a>'
               if p["screenshot"] and Path(p["screenshot"]).exists() else "")
        cards.append(f"""<div class="card"><span class="sev" style="background:{colors[p['severity']]}">
{p['severity']}</span><h2>{html.escape(p['title'])}</h2>
<a class="url" href="{html.escape(p['url'])}">{html.escape(p['url'])}</a>
<p>{html.escape(p['details'])}</p>{img}</div>""")
    counts = " · ".join(f"{sum(p['severity'] == s for p in problems)} {s}" for s in order)
    Path("report.html").write_text(f"""<!doctype html><html><head><meta charset="utf-8">
<title>Site Doctor Report</title><style>
body{{font-family:system-ui,sans-serif;max-width:900px;margin:40px auto;padding:0 16px;background:#f8fafc;color:#0f172a}}
.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:16px 20px;margin:16px 0}}
.sev{{color:#fff;font-size:12px;font-weight:700;text-transform:uppercase;padding:3px 8px;border-radius:4px}}
h2{{font-size:18px;margin:10px 0 4px}} .url{{font-size:13px;color:#64748b;word-break:break-all}}
img{{max-width:100%;max-height:400px;object-fit:cover;object-position:top;border:1px solid #e2e8f0;margin-top:8px}}
</style></head><body><h1>🩺 Site Doctor Report</h1>
<p><b>{html.escape(url)}</b> · {pages} pages visited · {len(problems)} problems ({counts})</p>
<p>{html.escape(report['summary'])}</p>{''.join(cards) or '<p>No problems found. 🎉</p>'}
</body></html>""", encoding="utf-8")


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: python site_doctor.py <url>")
    url = sys.argv[1] if "://" in sys.argv[1] else "https://" + sys.argv[1]
    sys.stdout.reconfigure(encoding="utf-8")  # emoji on Windows consoles
    SHOTS.mkdir(exist_ok=True)
    print(f"🩺 Site Doctor examining {url}  (up to {MAX_PAGES} pages)")
    with sync_playwright() as pw:
        browser = Browser(pw)
        report = run_agent(url, browser)
        browser.browser.close()
    write_report(url, report, browser.pages_opened)
    print(f"\n✅ Done: {len(report['problems'])} problems found on {browser.pages_opened} pages → report.html")


if __name__ == "__main__":
    main()

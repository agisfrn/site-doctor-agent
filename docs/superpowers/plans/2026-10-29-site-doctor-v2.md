# Site Doctor v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild Site Doctor as a pip-installable CLI where Claude steers a crawl, deterministic checks find problems, and a scored HTML report explains them.

**Architecture:** `browser.py` captures each page into a plain `PageCapture`; pure check functions turn captures into `Finding`s; `agent.py` runs the Claude tool-use loop (Claude chooses pages, the code runs every check), then merges findings into `IssueGroup`s and makes one structured-output call for explanations; `scoring.py` and `report.py` turn the `ScanResult` into `report.html` + `findings.json`. The agent emits events that `ui.py` renders, so no module except `ui.py`/`cli.py` prints.

**Tech Stack:** Python ≥ 3.11, anthropic (beta messages, server-side fallback), playwright (sync API), typer, rich, jinja2, axe-core 4.10.2 (vendored), pytest, ruff, hatchling.

**Spec:** `docs/superpowers/specs/2026-10-29-site-doctor-v2-design.md`

## Global Constraints

- Python `>=3.11`; package `site-doctor`, import `site_doctor`, src layout, console script `site-doctor = "site_doctor.cli:app"`.
- Default model `claude-opus-5-5`, default effort `medium`; every Claude call uses `client.beta.messages.create(..., betas=["server-side-fallback-2026-07-01"], fallbacks="default")`.
- Only `cli.py` and `ui.py` print. Everything else reports through return values or events.
- Severities and scores come from code, never from Claude.
- Defaults: `max_pages=15`, `max_steps=60`, page timeout 30 000 ms, desktop viewport 1280×800, mobile viewport 390×844.
- Tests never need an API key or internet: they use `demo_site` on localhost and fake clients.
- Paths stored in results and the report are relative to the run dir and use forward slashes (`Path.as_posix()`).
- ruff line length 100. Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

**Spec deviation (deliberate):** `demo_site/` lives at `src/site_doctor/demo_site/` instead of the repo root, so `site-doctor demo` still finds it after `pip install`.

## Review Focus

1. **Start URL redirects to another origin** (`http://x.com` → `https://www.x.com`): the crawl's origin must come from the first successful `open_page`'s `final_url`, or every same-site link is refused. Test in Task 9.
2. **`open_page` on a non-HTML URL** (PDF, image, download) or a dead host: becomes an `is_error` tool result plus a `basics.page-load-failed` finding, and the crawl continues. Test in Task 9.
3. **Pages with hundreds of links**: the summary sent to Claude caps same-site links at 80 and external at 30, so the context doesn't blow up. Test in Task 9.
4. **Triage JSON with unknown or missing issue ids**: unknown ids ignored, missing ones keep empty explanation/fix, no crash. Test in Task 10.
5. **Target typed without a scheme or with spaces** (`  example.com/ `): normalized to `https://example.com/`. Test in Task 1.

---

### Task 1: Package scaffold, models, config, events

**Files:**
- Delete: `site_doctor.py`, `requirements.txt`
- Create: `pyproject.toml`, `src/site_doctor/__init__.py`, `src/site_doctor/models.py`, `src/site_doctor/config.py`, `src/site_doctor/events.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Produces (`models.py`, all `@dataclass`):
  - `CATEGORIES = ("basics", "links", "accessibility", "seo", "performance", "security", "mobile")`, `SEVERITIES = ("critical", "high", "medium", "low")`
  - `Finding(id: str, category: str, severity: str, title: str, url: str, evidence: str = "", screenshots: list[str] = [])`
  - `Resource(url: str, type: str, transfer_size: int)`; `Image(src: str, natural_w: int, natural_h: int, rendered_w: int, rendered_h: int)`
  - `Timings(ttfb_ms: float | None, load_ms: float | None, lcp_ms: float | None, cls: float | None)`
  - `MobileData(doc_width: int, viewport_width: int, small_tap_targets: int)`
  - `PageCapture(url, final_url, status: int | None, headers: dict[str, str] (lower-case keys), title: str, meta: dict, links: list[str], console_errors: list[str], page_errors: list[str], resources: list[Resource], requested_urls: list[str], images: list[Image], timings: Timings, axe_violations: list[dict], mobile: MobileData | None, load_time_s: float)`. `meta` keys: `description`, `canonical`, `og_title`, `og_image`, `viewport` (each `str | None`), `h1_count: int`. Each `axe_violations` item: `{"id", "impact", "help", "nodes": int}`.
  - `PageResult(url: str, status: int | None, title: str, load_time_s: float, lcp_ms: float | None, findings: list[Finding], error: str | None = None)`
  - `IssueGroup(id, category, severity, title, urls: list[str], evidence: list[str], screenshots: list[str], explanation: str = "", fix: str = "")`
  - `Usage(input_tokens: int = 0, output_tokens: int = 0)`
  - `ScanResult(target, model, started_at: str (ISO 8601), duration_s: float, pages: list[PageResult], findings: list[Finding], groups: list[IssueGroup], scores: dict[str, int], overall: int, grade: str, summary: str, usage: Usage, partial: bool = False, partial_reason: str = "")` with `to_dict() -> dict` and `@classmethod from_dict(d) -> ScanResult`.
- Produces (`config.py`): `normalize_target(raw: str) -> str`; `ScanConfig(url: str, max_pages=15, max_steps=60, model="claude-opus-5-5", effort="medium", mobile=True, out_dir: Path = Path("reports"), stamp: str = <now, "%Y%m%d-%H%M%S">)` with property `run_dir -> Path` = `out_dir / f"{host}-{stamp}"` (host with `:` replaced by `-`).
- Produces (`events.py`, frozen dataclasses, `Event` = their union): `ScanStarted(target, model, max_pages)`, `AgentNote(text)`, `StepStarted(step: int, tool: str, url: str)`, `StepFinished(step, tool, url, summary: str, ok: bool, pages_used: int, max_pages: int, tally: dict[str, int])`, `TriageStarted(groups: int)`, `ScanFinished(partial: bool, reason: str)`.

- [ ] **Step 1: Write `pyproject.toml`** — hatchling backend; deps `anthropic>=2.3`, `playwright>=1.50`, `typer>=0.12`, `rich>=13`, `jinja2>=3.1`; optional `dev = ["pytest>=8", "ruff>=0.6"]`; `[tool.ruff] line-length = 100`; `[tool.pytest.ini_options] testpaths = ["tests"]`. `__init__.py` holds `__version__ = "2.0.0"`. Delete the v1 files.

- [ ] **Step 2: Write the failing tests**

```python
def test_scan_result_round_trips_through_json():
    f = Finding("seo.missing-description", "seo", "medium", "No meta description", "https://a.com/")
    r = ScanResult(target="https://a.com/", model="claude-opus-5-5", started_at="2026-10-29T10:00:00",
                   duration_s=1.5, pages=[PageResult("https://a.com/", 200, "A", 0.4, 900.0, [f])],
                   findings=[f], groups=[], scores={c: 100 for c in CATEGORIES}, overall=100,
                   grade="A", summary="ok", usage=Usage(10, 20))
    assert ScanResult.from_dict(json.loads(json.dumps(r.to_dict()))) == r

@pytest.mark.parametrize("raw,expected", [
    ("  example.com/ ", "https://example.com/"),
    ("http://x.com", "http://x.com"),
    ("https://x.com/a?b=1", "https://x.com/a?b=1"),
])
def test_normalize_target(raw, expected):
    assert normalize_target(raw) == expected

def test_run_dir_uses_host_and_stamp():
    cfg = ScanConfig(url="http://127.0.0.1:8123/", stamp="20261029-100000")
    assert cfg.run_dir == Path("reports") / "127.0.0.1-8123-20261029-100000"
```

- [ ] **Step 3: Run** `.venv\Scripts\python -m pip install -e .[dev]` then `pytest tests/test_models.py -v`. Expected: FAIL (ImportError).
- [ ] **Step 4: Implement `models.py`, `config.py`, `events.py`.** `from_dict` rebuilds the nested dataclasses explicitly (no third-party serializers).
- [ ] **Step 5: Run** `pytest tests/test_models.py -v` and `ruff check src tests`. Expected: all pass.
- [ ] **Step 6: Commit** `feat: package scaffold, data models, config and events`.

---

### Task 2: Demo site and local server

**Files:**
- Create: `src/site_doctor/demo_server.py`, `src/site_doctor/demo_site/{index.html, about.html, products.html, contact.html, clean.html, big.jpg}` (no `robots.txt` or `sitemap.xml`, on purpose)
- Create: `tests/conftest.py`
- Test: `tests/test_demo_server.py`

**Interfaces:**
- Produces: `demo_server.DEMO_DIR: Path`; `demo_server.serve(directory: Path = DEMO_DIR) -> ContextManager[str]` yielding `"http://127.0.0.1:<free port>"`, served from a daemon thread; path `/error` answers 500; quiet logging.
- Produces (`conftest.py`): session fixture `demo_url -> str` using `serve()`.

Planted bugs (one per page is enough; spread them): broken internal link `/missing-page`, broken external `https://broken.example.invalid/`, link to `/error` (500), an uncaught JS error (`undefinedFn()`), an `<img>` without `alt`, light-grey-on-white text, no meta description, two `<h1>`, `big.jpg` (≥ 2 MB, ≥ 3000 px wide; generate with Pillow once, or any large photo) shown at `width="200"`, a banner inserted at the top 300 ms after load (layout shift), a `<div style="width:1200px">` (horizontal scroll on mobile), links styled `font-size:8px` (tiny tap targets), `products.html` without a viewport meta. `clean.html`: title, description, canonical, OG tags, viewport meta, one h1, alt texts, links only to `/` and `/about.html`.

- [ ] **Step 1: Write the failing test**

```python
def test_demo_server_serves_pages_and_500(demo_url):
    assert urlopen(demo_url + "/index.html").status == 200
    with pytest.raises(HTTPError) as e:
        urlopen(demo_url + "/error")
    assert e.value.code == 500
```

- [ ] **Step 2: Run** `pytest tests/test_demo_server.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `demo_server.py` (`ThreadingHTTPServer` on port 0, `SimpleHTTPRequestHandler` subclass with `directory=` and `log_message` silenced) and the demo pages.
- [ ] **Step 4: Run** the test. Expected: PASS.
- [ ] **Step 5: Commit** `feat: demo site with planted bugs and local server`.

---

### Task 3: Browser capture

**Files:**
- Create: `src/site_doctor/browser.py`
- Test: `tests/test_browser.py`

**Interfaces:**
- Consumes: `PageCapture`, `Resource`, `Image`, `Timings`, `MobileData` (Task 1); `demo_url` (Task 2).
- Produces: `class PageLoadError(Exception)`; `class Browser(mobile: bool = True, timeout_ms: int = 30_000)` usable as a context manager (`__enter__` starts Playwright + Chromium + desktop context, and a mobile context when `mobile`); `capture(url: str) -> PageCapture`; `fetch_status(url: str) -> int | str` (GET via `context.request`, `max_redirects=5`, 15 s timeout; returns the status, or `"<ExcType>: <first line>"` on failure); `screenshot(url: str, path: Path, mobile: bool = False) -> Path` (full page).
- Produces: `run_axe(page) -> list[dict]`, called by `capture` — a stub returning `[]` until Task 7 vendors axe-core.

How to collect each field (the rest is the implementer's choice):
- Console errors via `page.on("console")` (type `error`), uncaught errors via `page.on("pageerror")`, `requested_urls` via `page.on("request")`; clear them per capture.
- `goto(wait_until="load")`, then `wait_for_load_state("networkidle", timeout=5000)` with the timeout swallowed.
- `meta`, `links` (absolute http(s), deduped, in order), `images` (natural vs. rendered `getBoundingClientRect` size): one `page.evaluate`.
- `resources`: `performance.getEntriesByType("resource")` → `name`, `initiatorType`, `transferSize` (0 when unknown).
- `timings`: Navigation Timing `responseStart` (TTFB) and `loadEventEnd`; LCP and CLS from `PerformanceObserver({type, buffered: true})` read inside one evaluate that resolves after 100 ms.
- `mobile`: load the same URL in the mobile context; `document.documentElement.scrollWidth`, `innerWidth`, and the count of visible `a, button, input, select` whose bounding box is under 24×24.
- Any Playwright `Error`/timeout on the desktop `goto` → `PageLoadError(first line of message)`. A failing mobile pass → `mobile=None`, no raise.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.fixture(scope="module")
def browser():
    with Browser() as b:
        yield b

def test_capture_collects_page_data(browser, demo_url):
    cap = browser.capture(demo_url + "/index.html")
    assert cap.status == 200 and cap.title
    assert any("undefinedFn" in e for e in cap.page_errors)
    assert demo_url + "/missing-page" in cap.links
    assert cap.meta["h1_count"] == 2 and cap.meta["description"] is None
    assert cap.timings.load_ms is not None and cap.load_time_s > 0
    assert cap.mobile is not None and cap.mobile.viewport_width == 390

def test_capture_dead_host_raises(browser):
    with pytest.raises(PageLoadError):
        browser.capture("http://127.0.0.1:9/")

def test_fetch_status(browser, demo_url):
    assert browser.fetch_status(demo_url + "/missing-page") == 404
    assert browser.fetch_status(demo_url + "/error") == 500
    assert isinstance(browser.fetch_status("https://broken.example.invalid/"), str)

def test_screenshot_writes_png(browser, demo_url, tmp_path):
    p = browser.screenshot(demo_url + "/clean.html", tmp_path / "s.png", mobile=True)
    assert p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
```

- [ ] **Step 2: Run** `pytest tests/test_browser.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `browser.py`.**
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Playwright capture of page, network, timings and mobile data`.

---

### Task 4: Check registry, basics and links

**Files:**
- Create: `src/site_doctor/checks/__init__.py`, `checks/basics.py`, `checks/links.py`
- Test: `tests/checks/test_basics.py`, `tests/checks/test_links.py`, `tests/checks/helpers.py` (a `make_capture(**overrides) -> PageCapture` that returns a fully "clean" capture by default)

**Interfaces:**
- Consumes: `PageCapture`, `Finding` (Task 1).
- Produces: `checks.PAGE_CHECKS: list[Callable[[PageCapture], list[Finding]]]` (each later check module appends its `check` here); `checks.run_page_checks(cap: PageCapture) -> list[Finding]`; `basics.check(cap)`; `basics.load_failure(url: str, error: str, is_start: bool) -> Finding` (id `basics.page-load-failed`, critical if `is_start` else high); `links.link_finding(url: str, status: int | str, internal: bool, found_on: str = "") -> Finding | None`.

Finding ids and severities (exact): `basics.server-error` critical (status ≥ 500), `basics.client-error` high (400–499), `basics.js-error` high (one per page error), `basics.console-error` medium (one finding per page, evidence = first 3 messages), `basics.missing-title` medium (empty/whitespace title); `links.broken-internal` high, `links.broken-external` medium (status ≥ 400), `links.unreachable` medium (status is a `str`). Evidence for links: `f"HTTP {status}"` or the error string, plus `f" · linked from {found_on}"` when given.

- [ ] **Step 1: Write the failing tests**

```python
def test_clean_capture_has_no_basics_findings():
    assert basics.check(make_capture()) == []

@pytest.mark.parametrize("status,fid,sev", [(503, "basics.server-error", "critical"),
                                            (404, "basics.client-error", "high")])
def test_bad_status(status, fid, sev):
    [f] = basics.check(make_capture(status=status))
    assert (f.id, f.severity) == (fid, sev)

def test_js_and_console_errors():
    ids = [f.id for f in basics.check(make_capture(page_errors=["x"], console_errors=["a", "b"]))]
    assert ids == ["basics.js-error", "basics.console-error"]

def test_load_failure_severity():
    assert basics.load_failure("u", "boom", is_start=True).severity == "critical"
    assert basics.load_failure("u", "boom", is_start=False).severity == "high"

def test_link_findings():
    assert links.link_finding("https://a.com/x", 200, internal=True) is None
    f = links.link_finding("https://a.com/x", 404, internal=True, found_on="https://a.com/")
    assert (f.id, f.severity) == ("links.broken-internal", "high")
    assert f.evidence == "HTTP 404 · linked from https://a.com/"
    assert links.link_finding("https://b.com", "Error: ENOTFOUND", internal=False).id == "links.unreachable"
```

- [ ] **Step 2: Run** `pytest tests/checks -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the registry and both modules.
- [ ] **Step 4: Run** `pytest tests/checks -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: check registry with basics and link checks`.

---

### Task 5: SEO and security checks

**Files:**
- Create: `src/site_doctor/checks/seo.py`, `checks/security.py` (both registered in `PAGE_CHECKS`)
- Test: `tests/checks/test_seo.py`, `tests/checks/test_security.py`

**Interfaces:**
- Consumes: `make_capture` (Task 4), `PAGE_CHECKS`.
- Produces: `seo.check(cap)`, `seo.check_site(origin: str, fetch: Callable[[str], int | str]) -> list[Finding]`, `security.check(cap)`.

Exact rules:
- seo: `seo.missing-description` medium (`meta["description"]` empty/None); `seo.h1-count` low (`h1_count != 1`, evidence `"{n} h1 elements"`); `seo.missing-canonical` low; `seo.missing-og` low (either `og_title` or `og_image` missing). `check_site`: `seo.missing-robots` low and `seo.missing-sitemap` low when `fetch(origin + "/robots.txt")` / `"/sitemap.xml"` is not an int below 400; the finding `url` is that file's URL.
- security (use `final_url`): `security.not-https` critical when scheme is `http` **unless the host is `localhost` or `127.0.0.1`** (local dev and the demo); `security.mixed-content` high when the page is https and any `requested_urls` item starts with `http://` (evidence: first 3 URLs); on https only: `security.missing-hsts` medium (no `strict-transport-security`); always: `security.missing-csp` medium (no `content-security-policy`), `security.missing-nosniff` low (`x-content-type-options` ≠ `nosniff`), `security.missing-frame-protection` low (no `x-frame-options` and CSP lacks `frame-ancestors`).

- [ ] **Step 1: Write the failing tests**

```python
def test_clean_capture_has_no_seo_findings():
    assert seo.check(make_capture()) == []

def test_seo_page_rules():
    cap = make_capture(meta={**make_capture().meta, "description": None, "h1_count": 2})
    assert {f.id for f in seo.check(cap)} == {"seo.missing-description", "seo.h1-count"}

def test_seo_site_rules():
    fetch = {"https://a.com/robots.txt": 200, "https://a.com/sitemap.xml": 404}.get
    [f] = seo.check_site("https://a.com", fetch)
    assert (f.id, f.url) == ("seo.missing-sitemap", "https://a.com/sitemap.xml")

SAFE = {"strict-transport-security": "max-age=1", "content-security-policy": "frame-ancestors 'none'",
        "x-content-type-options": "nosniff"}

def test_secure_https_page_is_clean():
    assert security.check(make_capture(final_url="https://a.com/", headers=SAFE)) == []

def test_plain_http_is_critical_but_localhost_is_exempt():
    ids = lambda u: {f.id for f in security.check(make_capture(final_url=u, headers=SAFE))}
    assert "security.not-https" in ids("http://a.com/")
    assert "security.not-https" not in ids("http://127.0.0.1:8000/")

def test_mixed_content():
    cap = make_capture(final_url="https://a.com/", headers=SAFE, requested_urls=["http://cdn.com/x.js"])
    assert [f.id for f in security.check(cap)] == ["security.mixed-content"]
```

- [ ] **Step 2: Run** `pytest tests/checks -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** `pytest tests/checks -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: SEO and security header checks`.

---

### Task 6: Performance and mobile checks

**Files:**
- Create: `src/site_doctor/checks/performance.py`, `checks/mobile.py` (registered)
- Test: `tests/checks/test_performance.py`, `tests/checks/test_mobile.py`

**Interfaces:**
- Produces: `performance.check(cap)`, `mobile.check(cap)` (returns `[]` when `cap.mobile is None`, except the viewport rule, which uses `meta`).

Exact rules:
- performance: `performance.lcp-slow` high if `lcp_ms > 4000`, medium if `> 2500`; `performance.cls-high` high if `cls > 0.25`, medium if `> 0.1`; `performance.ttfb-slow` medium if `ttfb_ms > 800`; `performance.heavy-page` medium if the sum of `transfer_size` > 3 000 000; `performance.oversized-image` low per image whose resource `transfer_size > 500_000` or whose `natural_w > 2 * rendered_w` (rendered_w > 0). `None` timings never produce findings.
- mobile: `mobile.missing-viewport` high (`meta["viewport"]` None); `mobile.horizontal-scroll` high (`doc_width > viewport_width + 1`, evidence `"page is {doc}px wide on a {vw}px screen"`); `mobile.small-tap-targets` medium (`small_tap_targets > 0`, evidence `"{n} tap targets under 24×24px"`).

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize("lcp,sev", [(2400, None), (3000, "medium"), (4500, "high")])
def test_lcp_thresholds(lcp, sev):
    cap = make_capture(timings=Timings(ttfb_ms=100, load_ms=500, lcp_ms=lcp, cls=0.0))
    found = [f.severity for f in performance.check(cap) if f.id == "performance.lcp-slow"]
    assert found == ([sev] if sev else [])

def test_oversized_image_and_heavy_page():
    cap = make_capture(images=[Image("https://a.com/big.jpg", 3000, 2000, 200, 133)],
                       resources=[Resource("https://a.com/big.jpg", "img", 3_100_000)])
    ids = [f.id for f in performance.check(cap)]
    assert ids.count("performance.oversized-image") == 1 and "performance.heavy-page" in ids

def test_missing_timings_produce_nothing():
    assert performance.check(make_capture(timings=Timings(None, None, None, None))) == []

def test_mobile_rules():
    cap = make_capture(mobile=MobileData(doc_width=1200, viewport_width=390, small_tap_targets=4))
    assert {f.id for f in mobile.check(cap)} == {"mobile.horizontal-scroll", "mobile.small-tap-targets"}
    assert mobile.check(make_capture(mobile=None)) == []
```

- [ ] **Step 2: Run** `pytest tests/checks -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** `pytest tests/checks -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: performance and mobile checks`.

---

### Task 7: Accessibility (axe-core) and checks against the demo site

**Files:**
- Create: `src/site_doctor/checks/vendor/axe.min.js` (from `https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js`), `checks/vendor/axe-core-LICENSE.txt` (from `.../axe-core@4.10.2/LICENSE`), `checks/accessibility.py` (registered)
- Modify: `src/site_doctor/browser.py` (`run_axe` now injects the file with `page.add_script_tag(path=...)` and returns `await axe.run()` violations mapped to `{"id", "impact", "help", "nodes": len(nodes)}`; any error → `[]`)
- Test: `tests/checks/test_accessibility.py`, `tests/test_checks_on_demo.py`

**Interfaces:**
- Produces: `accessibility.check(cap)`: one finding per violation, id `f"accessibility.{v['id']}"`, severity map `critical→critical, serious→high, moderate→medium, minor→low` (unknown → low), title `v["help"]`, evidence `f"{nodes} element(s)"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_axe_impacts_map_to_severities():
    cap = make_capture(axe_violations=[{"id": "image-alt", "impact": "critical", "help": "Images need alt", "nodes": 2},
                                       {"id": "color-contrast", "impact": "serious", "help": "Contrast", "nodes": 5}])
    assert [(f.id, f.severity) for f in accessibility.check(cap)] == [
        ("accessibility.image-alt", "critical"), ("accessibility.color-contrast", "high")]

# tests/test_checks_on_demo.py — end-to-end over real captures
def test_planted_bugs_are_found(browser, demo_url):
    ids = {f.id for p in ["index", "about", "products", "contact"]
           for f in run_page_checks(browser.capture(f"{demo_url}/{p}.html"))}
    for expected in ["basics.js-error", "accessibility.image-alt", "accessibility.color-contrast",
                     "seo.missing-description", "seo.h1-count", "performance.oversized-image",
                     "mobile.horizontal-scroll", "mobile.small-tap-targets", "mobile.missing-viewport"]:
        assert expected in ids

def test_clean_page_only_has_header_findings(browser, demo_url):
    ids = {f.id for f in run_page_checks(browser.capture(demo_url + "/clean.html"))}
    assert ids <= {"security.missing-csp", "security.missing-nosniff", "security.missing-frame-protection"}
```

(Move the `browser` fixture from Task 3 into `conftest.py` with `scope="session"`.)

- [ ] **Step 2: Run** `pytest tests -v`. Expected: new tests FAIL.
- [ ] **Step 3: Download the vendored files, implement `accessibility.py` and the real `run_axe`.** If the CLS banner doesn't register on the demo, extend the banner delay or size rather than lowering thresholds.
- [ ] **Step 4: Run** `pytest tests -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: axe-core accessibility checks, verified on the demo site`.

---

### Task 8: Triage grouping and scoring

**Files:**
- Create: `src/site_doctor/triage.py`, `src/site_doctor/scoring.py`
- Test: `tests/test_triage.py`, `tests/test_scoring.py`

**Interfaces:**
- Produces: `triage.group_findings(findings: list[Finding]) -> list[IssueGroup]`: group by `id`; severity = most severe in the group; title from the first; `urls`, `evidence` (max 5), `screenshots` unique in first-seen order; sorted by severity order, then `len(urls)` descending, then id.
- Produces: `scoring.PENALTY = {"critical": 25, "high": 10, "medium": 4, "low": 1}`; `scoring.score(groups: list[IssueGroup]) -> tuple[dict[str, int], int, str]` returning per-category scores (every category in `CATEGORIES`), overall and grade. Per group: `PENALTY[sev] * min(1 + 0.1 * (len(urls) - 1), 2)`; category floor 0; round each category with `round()`; overall = `round(mean)`; grade A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, else F.

- [ ] **Step 1: Write the failing tests**

```python
def g(sev, n=1, cat="security", gid="x"):
    return IssueGroup(gid, cat, sev, "t", [f"u{i}" for i in range(n)], [], [])

def test_no_issues_is_perfect():
    scores, overall, grade = score([])
    assert set(scores) == set(CATEGORIES) and overall == 100 and grade == "A"

@pytest.mark.parametrize("groups,expected", [
    ([g("critical")], 75), ([g("high", n=11)], 80), ([g("high", n=21)], 80),
    ([g("critical", gid=str(i)) for i in range(5)], 0)])
def test_category_penalties(groups, expected):
    assert score(groups)[0]["security"] == expected

def test_grade_boundaries():
    assert score([g("low")])[2] == "A"
    # security at 25, six categories at 100 → mean 89.3 → 89 → "B"
    assert score([g("critical", gid=str(i)) for i in range(3)])[2] == "B"

def test_grouping_merges_by_id_and_sorts():
    fs = [Finding("security.missing-csp", "security", "medium", "No CSP", f"u{i}") for i in range(3)]
    fs += [Finding("basics.js-error", "basics", "high", "JS error", "u0", "boom")]
    groups = group_findings(fs)
    assert [x.id for x in groups] == ["basics.js-error", "security.missing-csp"]
    assert groups[1].urls == ["u0", "u1", "u2"]
```

- [ ] **Step 2: Run** `pytest tests/test_triage.py tests/test_scoring.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** the tests. Expected: PASS.
- [ ] **Step 5: Commit** `feat: issue grouping and rule-based scoring`.

---

### Task 9: Agent crawl loop

**Files:**
- Create: `src/site_doctor/agent.py`
- Test: `tests/fakes.py`, `tests/test_agent.py`

**Interfaces:**
- Consumes: `Browser` protocol (`capture`, `fetch_status`, `screenshot`), `PageLoadError`, `run_page_checks`, `seo.check_site`, `basics.load_failure`, `links.link_finding`, events, `ScanConfig`.
- Produces: `normalize_url(url: str) -> str` (strip fragment; empty path → `/`; strip a trailing `/` from non-root paths); `class Agent(client, browser, config: ScanConfig, on_event: Callable[[Event], None] = lambda e: None)` with `run() -> ScanResult` (Task 10 adds triage; in this task `run` returns groups/scores from `group_findings` + `score`, and `summary` = the `finish` summary). Tool names: `open_page(url)`, `check_link(url, found_on?)`, `screenshot(url)`, `finish(summary)`; all schemas `strict`-style (`additionalProperties: false`).
- Produces (`tests/fakes.py`): `tool_use(name, **input)`, `text(t)`, `response(*blocks, stop="tool_use")` building `SimpleNamespace` objects with `.type/.id/.name/.input/.text`, `.stop_reason`, `.usage`; `FakeClient(responses)` whose `beta.messages.create(**kw)` pops the next response and records `kw`; `FakeBrowser(pages: dict[str, PageCapture | Exception], statuses: dict[str, int | str])`.

Behaviour (from the spec, exact):
- Each loop request: `model`, `max_tokens=16000`, `system`, `tools`, `messages`, `output_config={"effort": config.effort}`, plus the fallback beta. Append `response.content` unchanged as the assistant turn. Return all tool results of one response in one user message; failures are `is_error: True`.
- The crawl origin is the origin of the first successful capture's `final_url`; before that, the origin of `config.url`. `open_page` on another origin → error result telling Claude to use `check_link`.
- An already-opened normalized URL returns the cached summary and doesn't count. When `max_pages` pages are used → error result "page budget used up, call finish". Stop after `max_steps` tool calls or on `finish` or on `end_turn`.
- `open_page` summary (JSON): `status`, `title`, `load_time_s`, `counts` (by category and severity), `top_issues` (5 titles, most severe first), `same_site_links` (max 80, each `{"url", "visited": bool}`), `external_links` (max 30), `pages_left`.
- `PageLoadError` → `basics.load_failure(url, msg, is_start=(normalize_url(url) == normalize_url(config.url)))`, recorded as a `PageResult` with `error`, and an `is_error` result.
- After the first successful capture, run `seo.check_site(origin, browser.fetch_status)` once.
- `screenshot` saves `run_dir/screenshots/{n:02d}-{slug}-desktop.png` and `-mobile.png` (when `config.mobile`); finding `screenshots` are filled from these (run-dir-relative, forward slashes) when building the result.
- Text blocks → `AgentNote`; each tool call → `StepStarted` / `StepFinished` (summary for `open_page`: `"{status} · {load}s · {n} issues"`; `check_link`: `"{status} ✓"` or `"{status} ✗ broken"`).
- Accumulate `usage` over every response.

- [ ] **Step 1: Write the failing tests** (FakeBrowser pages built with `make_capture`)

```python
def test_crawl_opens_pages_and_finishes(tmp_path):
    # script: open_page(/), check_link(/missing), finish("done") → 1 page, links.broken-internal finding,
    # result.summary == "done", events contain StepStarted/StepFinished for steps 1..3

def test_page_budget_is_enforced(tmp_path):
    # max_pages=1, script opens / then /about → second tool_result is_error and mentions "finish"

def test_reopening_a_page_is_free(tmp_path):
    # open_page("/") then open_page("/#top") → 1 page used, both results ok

def test_other_origin_is_refused_but_redirected_origin_is_used(tmp_path):
    # config.url="http://a.com/", capture final_url="https://www.a.com/"; then open_page("https://www.a.com/x") ok,
    # open_page("https://evil.com/") is_error                                          (Review Focus 1)

def test_load_failure_becomes_finding_and_crawl_continues(tmp_path):
    # FakeBrowser raises PageLoadError for "/file.pdf" → is_error result, basics.page-load-failed high,
    # next open_page still runs                                                           (Review Focus 2)

def test_link_lists_are_capped(tmp_path):
    # capture with 300 same-site + 100 external links → summary lists 80 and 30          (Review Focus 3)

def test_step_cap_stops_the_crawl(tmp_path):
    # max_steps=2 with 3 scripted check_link calls in separate responses → only 2 fetch_status calls

def test_requests_use_fallback_and_effort(tmp_path):
    kw = client.calls[0]
    assert kw["betas"] == ["server-side-fallback-2026-07-01"] and kw["fallbacks"] == "default"
    assert kw["output_config"] == {"effort": "medium"} and kw["model"] == "claude-opus-5-5"
```

Write these with real assertions as described in the comments.

- [ ] **Step 2: Run** `pytest tests/test_agent.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement `agent.py`.**
- [ ] **Step 4: Run** `pytest tests -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Claude-steered crawl loop with code-enforced limits`.

---

### Task 10: Triage call and partial runs

**Files:**
- Modify: `src/site_doctor/agent.py`
- Test: `tests/test_agent.py`

**Interfaces:**
- Produces: `Agent._explain(groups: list[IssueGroup], crawl_summary: str) -> str` (fills `explanation`/`fix` in place, returns the overall summary); `TRIAGE_SCHEMA` = `{"summary": str, "issues": [{"id": str, "explanation": str, "fix": str}]}` with `additionalProperties: false` and all fields required.

Behaviour:
- After the crawl, emit `TriageStarted(len(groups))`, then one request: no tools, `output_config={"effort": ..., "format": {"type": "json_schema", "schema": TRIAGE_SCHEMA}}`, fallback beta, user message = JSON of groups (`id, category, severity, title, pages: len(urls), urls[:5], evidence`) plus the crawl summary. Parse the first text block with `json.loads`. Unknown ids ignored; missing ids keep `""`. Any exception or a `refusal` → explanations stay empty and the summary falls back to the crawl summary.
- During the crawl: `anthropic.AuthenticationError` and `PermissionDeniedError` propagate. Other `anthropic.APIError`, `KeyboardInterrupt`, or `stop_reason == "refusal"` → stop, `partial=True`, `partial_reason` = `"API error: <msg>"` / `"interrupted"` / `"model declined"`, and **skip** the triage call. `ScanFinished(partial, reason)` is always emitted.

- [ ] **Step 1: Write the failing tests**

```python
def test_triage_fills_explanations_and_ignores_unknown_ids(tmp_path):
    # final response text = {"summary": "S", "issues": [{"id": "links.broken-internal", "explanation": "E", "fix": "F"},
    #                                                     {"id": "nope", "explanation": "x", "fix": "y"}]}
    # → that group has E/F, result.summary == "S", triage request has "format" and no "tools"   (Review Focus 4)

def test_bad_triage_json_keeps_report(tmp_path):
    # final text "not json" → result.summary == crawl summary, explanations empty, partial False

def test_api_error_gives_partial_result_without_triage(tmp_path):
    # FakeClient raises anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")) on call 2
    # → result.partial and "API error" in partial_reason, findings from call 1 kept, no 3rd request

def test_auth_error_propagates(tmp_path):
    # FakeClient raises anthropic.AuthenticationError → pytest.raises(anthropic.AuthenticationError)
```

- [ ] **Step 2: Run** `pytest tests/test_agent.py -v`. Expected: new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pytest tests -v`. Expected: PASS.
- [ ] **Step 5: Commit** `feat: AI triage with structured output and partial-run handling`.

---

### Task 11: HTML/JSON report and render

**Files:**
- Create: `src/site_doctor/report.py`, `src/site_doctor/templates/report.html.j2`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `ScanResult`.
- Produces: `PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50), "claude-fable-5-1": (10.0, 50.0)}` (USD per million input/output tokens); `estimate_cost(model: str, usage: Usage) -> float | None`; `render_html(result: ScanResult) -> str`; `write_report(result: ScanResult, run_dir: Path) -> Path` (writes `report.html` + `findings.json`, UTF-8, returns the HTML path).

Template requirements (spec § report): one self-contained file, inline CSS/JS, no CDN; tokens on `:root` with a `prefers-color-scheme: dark` override; grade + overall in the header; 7 category cards; summary; partial-run banner when `partial`; issue cards as `<details>` with severity badge, title, explanation, fix, affected pages, evidence, screenshot `<img loading="lazy">`; filter chips for severity and category (small inline JS toggling `hidden`); pages table; run details (model, tokens, `$x.xx` estimate, duration). Jinja autoescape on.

- [ ] **Step 1: Write the failing tests**

```python
def test_report_contains_grade_issues_and_escapes(tmp_path, sample_result):
    sample_result.groups[0].title = "<script>alert(1)</script>"
    html = write_report(sample_result, tmp_path).read_text(encoding="utf-8")
    assert sample_result.grade in html and "&lt;script&gt;" in html and "<script>alert(1)" not in html
    assert all(g.id in html for g in sample_result.groups)
    assert json.loads((tmp_path / "findings.json").read_text(encoding="utf-8"))["grade"] == sample_result.grade

def test_partial_banner(tmp_path, sample_result):
    sample_result.partial, sample_result.partial_reason = True, "interrupted"
    assert "interrupted" in render_html(sample_result)

def test_estimate_cost():
    assert estimate_cost("claude-opus-5-5", Usage(1_000_000, 100_000)) == pytest.approx(6.0)
    assert estimate_cost("unknown-model", Usage(1, 1)) is None
```

(`sample_result` fixture in `conftest.py`: 2 pages, 3 groups across categories, scores from `score()`.)

- [ ] **Step 2: Run** `pytest tests/test_report.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `report.py` and the template.
- [ ] **Step 4: Run** the tests; open the rendered file in a browser once and check light and dark mode at 390 px and desktop widths.
- [ ] **Step 5: Commit** `feat: self-contained HTML report with scores, filters and screenshots`.

---

### Task 12: Terminal UI

**Files:**
- Create: `src/site_doctor/ui.py`
- Test: `tests/test_ui.py`

**Interfaces:**
- Consumes: events (Task 1).
- Produces: `class PlainUI(out=sys.stdout)` and `class LiveUI(console: rich.console.Console | None = None)`, both with `handle(event: Event) -> None` and context-manager methods (no-op for `PlainUI`); `make_ui(plain: bool) -> PlainUI | LiveUI` (plain when `plain` or `not sys.stdout.isatty()`).

Formats (exact): `StepFinished` → `"[{step:02d}] {tool} {short} → {summary}"`, where `short` is the URL path when the URL has the scan target's origin (known from `ScanStarted`), else the full URL; `AgentNote` → `"     💬 {text}"` (first line, max 120 chars); `TriageStarted` → `"🧠 Writing explanations for {n} issues…"`. `LiveUI` shows a header (target · model), the last 12 step lines, and a footer `pages {used}/{max} · ● critical n · ● high n · ● medium n · ● low n` (coloured red / orange / yellow / blue).

- [ ] **Step 1: Write the failing test**

```python
def test_plain_ui_formats_steps():
    out = io.StringIO()
    ui = PlainUI(out)
    ui.handle(ScanStarted("https://a.com/", "claude-opus-5-5", 15))
    ui.handle(StepFinished(7, "open_page", "https://a.com/pricing", "200 · 1.2s · 3 issues", True, 3, 15, {}))
    assert "[07] open_page /pricing → 200 · 1.2s · 3 issues" in out.getvalue()
```

- [ ] **Step 2: Run** `pytest tests/test_ui.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `ui.py`.
- [ ] **Step 4: Run** the test. Expected: PASS.
- [ ] **Step 5: Commit** `feat: Rich live terminal view and plain fallback`.

---

### Task 13: CLI

**Files:**
- Create: `src/site_doctor/cli.py`, `src/site_doctor/__main__.py` (`app()`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything above.
- Produces (`tests/fakes.py` additions): `FakeBrowserCM` (a `FakeBrowser` usable as `with Browser(mobile=...) as b`), `browser_raising(msg: str)` (a `Browser` replacement whose `__enter__` raises `playwright.sync_api.Error(msg)`), `raising_auth_error_client()` (raises `anthropic.AuthenticationError` on `beta.messages.create`).
- Produces: Typer `app` with `scan URL [--max-pages 15] [--effort medium] [--model claude-opus-5-5] [--no-mobile] [--out reports] [--plain]`, `demo [same options minus URL]`, `render FINDINGS_JSON [--out DIR]`; `run_scan(config: ScanConfig, plain: bool) -> Path` shared by `scan` and `demo`.

Behaviour: `run_scan` creates `config.run_dir`, opens `Browser(mobile=config.mobile)`, builds `anthropic.Anthropic()`, runs `Agent` inside the UI, writes the report, then prints the report path and `grade · overall/100 · N issues`. Errors → message + exit code 1: `anthropic.AuthenticationError` or a client-construction failure → `"No Anthropic credentials found. Set ANTHROPIC_API_KEY (or run `ant auth login`)."`; a Playwright error containing `"Executable doesn't exist"` → `"Chromium isn't installed. Run: playwright install chromium"`. `demo` wraps `run_scan` in `demo_server.serve()`. A partial result still writes the report and exits 0 with a yellow "partial report" note.

- [ ] **Step 1: Write the failing tests**

```python
def test_render_rebuilds_report(tmp_path, sample_result):
    src = tmp_path / "findings.json"
    src.write_text(json.dumps(sample_result.to_dict()), encoding="utf-8")
    res = CliRunner().invoke(app, ["render", str(src), "--out", str(tmp_path / "out")])
    assert res.exit_code == 0 and (tmp_path / "out" / "report.html").exists()

def test_scan_without_credentials_explains(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "Browser", FakeBrowserCM)     # context-manager wrapper over FakeBrowser
    monkeypatch.setattr(cli.anthropic, "Anthropic", raising_auth_error_client)
    res = CliRunner().invoke(app, ["scan", "example.com", "--out", str(tmp_path), "--plain"])
    assert res.exit_code == 1 and "ANTHROPIC_API_KEY" in res.output

def test_missing_chromium_explains(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "Browser", browser_raising("Executable doesn't exist at C:\\x"))
    res = CliRunner().invoke(app, ["scan", "example.com", "--out", str(tmp_path), "--plain"])
    assert res.exit_code == 1 and "playwright install chromium" in res.output
```

- [ ] **Step 2: Run** `pytest tests/test_cli.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `cli.py` and `__main__.py`.
- [ ] **Step 4: Run** `pytest tests -v` and `site-doctor --help`. Expected: tests pass; help lists `scan`, `demo`, `render`.
- [ ] **Step 5: Commit** `feat: site-doctor CLI with scan, demo and render`.

---

### Task 14: README, CI, license, demo recording

**Files:**
- Create: `README.md`, `LICENSE` (MIT, `Copyright (c) 2026 panmm942-ui`), `.github/workflows/ci.yml`, `demo.tape`, `docs/demo.gif` placeholder note in README (no binary until recorded)
- Modify: `.gitignore` (add `docs/*.tmp`; keep `reports/`)

**Interfaces:** none (docs and CI).

Content:
- `ci.yml`: on push and pull_request; ubuntu-latest; matrix python `3.11`, `3.12`, `3.13`; steps checkout, setup-python with pip cache, `pip install -e .[dev]`, `python -m playwright install --with-deps chromium`, `ruff check src tests`, `pytest -q`.
- `demo.tape` (VHS): `Output docs/demo.gif`, width 1200, height 700, theme "Catppuccin Mocha", `Type "site-doctor demo"`, `Enter`, `Sleep 90s`.
- README sections in order: title + one-line pitch; badges (CI workflow, Python 3.11+, MIT); demo GIF (`docs/demo.gif`); report screenshot (`docs/report.png`); "How it works" mermaid flowchart (CLI → Agent ⇄ Claude; Agent → Browser → Checks → Findings → Triage → Claude explanations → Scoring → Report); features; quickstart (`pip install -e .`, `playwright install chromium`, set `ANTHROPIC_API_KEY`, `site-doctor demo`, `site-doctor scan https://…`); CLI options table; checks table (copy of the spec's table); scoring rules; cost note (typical run tokens, `--model claude-sonnet-5-5` for cheaper runs); development (`pip install -e .[dev]`, `pytest`, `ruff check`); license. Mark the GIF, `docs/report.png` and `examples/sample-report.html` as "generated by `site-doctor demo`" — the user produces them after one real run.

- [ ] **Step 1: Write the files.**
- [ ] **Step 2: Verify** `ruff check src tests`, `pytest -q` (all pass), and that every relative link in README points to an existing file except the three marked as generated.
- [ ] **Step 3: Commit** `docs: README, MIT license, CI workflow and VHS demo script`.

# Site Doctor: Full-Site Scan — Design

Date: 2026-10-29
Status: approved in conversation (piece 1 of 5), pending written-spec review
Builds on: `2026-10-29-site-doctor-v2-design.md`

## Goal

Scan every page of a site, not a sample, and check every link on it, while keeping Claude's cost
roughly flat regardless of site size. Keep today's Claude-led crawl as a second mode.

## Roadmap (one spec → plan → build cycle each)

1. **Full-site scan** (this spec)
2. Exact elements: CSS selector, HTML snippet and a highlighted screenshot per issue
3. More checks: broken images/assets, redirect chains, duplicate titles/descriptions, orphan pages,
   certificates, cookies, spelling via Claude
4. Compare with last scan
5. CI mode + GitHub Action

## Modes

| | `--mode full` (default) | `--mode smart` |
|---|---|---|
| Who picks pages | a crawler in code: sitemap + every same-site link | Claude, one page at a time (v2 behaviour, unchanged) |
| `--max-pages` default | 200 (max 1000) | 15 (max 100) |
| Links checked | every unique link on every crawled page | links Claude chooses to check |
| Claude's role | analyst: reviews an overview, inspects pages, takes screenshots, then explains issues | navigator, then explains issues |
| Claude cost | roughly flat (sees a capped overview, not every page) | grows with pages |

Everything after page capture is shared: checks, grouping, scoring, the triage call, the report.

## Components

### browser.py — concurrent capture behind the same sync interface
The agent, CLI and tests keep calling a synchronous `Browser`. Internally it runs Playwright's
**async** API on a dedicated event-loop thread and forwards calls with
`asyncio.run_coroutine_threadsafe`. New methods:

- `capture_many(urls: list[str]) -> dict[str, PageCapture | PageLoadError]` — up to
  `concurrency` pages at once, each in its own tab (desktop + mobile tab pair).
- `fetch_many(urls: list[str]) -> dict[str, int | str]` — status checks with at most 10 in flight
  overall and 2 per host.
- `fetch_text(url) -> tuple[int | str, str]` — body text for robots.txt and sitemaps.

`capture`, `fetch_status` and `screenshot` keep their signatures. `Browser(mobile, timeout_ms,
concurrency=4)`.

A capture whose main response isn't HTML (`content-type` not `text/html` or
`application/xhtml+xml`), or whose navigation turns into a download, raises `NotAPage(status)`
(a `PageLoadError` subclass carrying the HTTP status) so the crawler can treat it as a link,
not a page.

### crawler.py (new)
`Crawler(browser, config, on_event).run() -> CrawlResult`.

1. Fetch `robots.txt` with `fetch_text`; parse it with `urllib.robotparser`. Collect its `Sitemap:`
   lines. Unless `--ignore-robots`, disallowed URLs are not crawled (they're still status-checked
   if linked).
2. Seed the queue with the start URL plus `<loc>` URLs from `/sitemap.xml` and robots `Sitemap:`
   entries. Sitemap indexes are followed two levels deep; at most 5000 URLs are read. Parse with
   `xml.etree.ElementTree`, namespace-agnostic.
3. Breadth-first: take up to `concurrency` same-origin, not-yet-seen, normalized URLs; skip
   obvious non-pages by extension (`.pdf .zip .jpg .jpeg .png .gif .webp .svg .mp4 .mp3 .doc
   .docx .xls .xlsx .ppt .pptx .csv .xml .json .txt`); `capture_many`; run page checks; enqueue
   new same-origin links. Stop at `max_pages` pages or an empty queue.
4. The first capture's `final_url` sets the origin (redirect handling as in v2).
5. Link check: every unique `a[href]` target (internal and external) that wasn't captured as a
   page gets `fetch_many`; broken ones become `links.*` findings with `found_on` = the first page
   that linked them. Internal URLs left over when the page cap is hit are status-checked too.
6. `seo.check_site` runs once.

`CrawlResult`: `pages: dict[str, PageResult]`, `findings: list[Finding]` (page + link + site),
`link_status: dict[str, int | str]`, `links_from: dict[str, list[str]]` (page → its links),
`origin: str`, `stopped_reason: str` ("" | "page limit" | "interrupted").

Emits `CrawlProgress(pages_done, queued, links_checked, links_total)` after every batch.
Ctrl+C stops the crawl; what was captured is kept and the run is marked partial.

### analyst.py (new) — Claude's role in full mode
`Analyst(client, browser, config, crawl: CrawlResult, on_event).run() -> AnalystResult`
(`summary: str`, `screenshots: dict[str, list[str]]`).

Claude gets a **site overview** capped at roughly 6 000 tokens:
- totals: pages, status-code breakdown, broken links, issues by severity and category
- page groups: URLs grouped by template (path with numeric/hex/long-slug segments replaced by
  `*`, e.g. `/products/*`), with page counts and issue counts
- the 20 most widespread issue groups (id, title, severity, pages affected)
- the 15 worst pages by severity-weighted issue count

Tools (same loop mechanics, fallback beta and limits as v2's agent):
- `page_details(url)` — that page's findings, status, timings, and its links with their statuses
- `screenshot(url)` — desktop + phone, at most 12 per scan
- `finish(summary)`

Caps: 30 tool calls. Then the existing triage call explains each issue group (unchanged).

### agent.py
The v2 `Agent` is the smart mode and keeps its behaviour. The tool loop, `_create`, usage
accounting, triage call and result building move into shared code so both modes use one
implementation (`ClaudeSession` for requests and usage; `build_result(...)` for grouping,
triage, scoring).

### events / ui
New `CrawlProgress` event. `LiveUI` shows a progress bar (pages done / queued, links checked)
during the crawl, then the analyst's step log. `PlainUI` prints a progress line every batch.

### cli.py
`scan URL [--mode full|smart] [--max-pages N] [--concurrency 4] [--ignore-robots] ...` (existing
options kept). `--max-pages` default depends on mode (200 / 15). `demo` uses full mode by default
and accepts `--mode`.

### report
New "Site map" section: the page groups with page counts and issue counts. The pages table
shows the 50 pages with the most issues, with the rest in an expandable `<details>`. The header
shows the mode and links checked.

### config / models
`ScanConfig` gains `mode: str = "full"`, `concurrency: int = 4`, `ignore_robots: bool = False`.
`ScanResult` gains `mode: str = "full"`, `links_checked: int = 0`, `page_groups: list[dict]`
(`pattern`, `pages`, `issues`). Old `findings.json` files without these fields still load.

## Error handling

- A page that fails to load: `basics.page-load-failed` (critical on the start URL), crawl continues.
- A non-HTML URL reached by a link: status-checked, not counted as a page.
- Start URL fails to load: crawl ends with that one finding; the analyst still runs and the report
  says why.
- Robots.txt or sitemap missing/unparseable: ignored (the existing SEO findings still report them).
- Claude API errors during analysis → partial report with all crawl findings (no explanations).
  Auth/credential errors still stop with the clear CLI message.

## Testing (no API key, no internet)

- The demo site gains more pages so a full crawl has something to find: a `/blog/` section with
  3 posts (one template), a page reachable only two hops deep, and a PDF link. It keeps having no
  robots.txt or sitemap (those are planted SEO findings).
- Crawler tests use a second fixture site, `tests/fixtures/crawl_site/`, with a `robots.txt` (one
  Disallow, one Sitemap line), a sitemap index → sitemap, an orphan page listed only in the
  sitemap, a PDF, a redirect, and 30 generated product pages for template grouping.
- `test_browser_concurrency.py`: `capture_many` on 6 pages returns all 6; a dead URL in the batch
  returns `PageLoadError` for that URL only; a PDF raises `NotAPage`.
- `test_crawler.py`: finds every page in the fixture (including the sitemap-only orphan); respects
  Disallow (and `--ignore-robots` overrides); stops at `max_pages` with `stopped_reason="page
  limit"`; checks every link once; reports a broken link with the right `found_on`; PDF is a link,
  not a page; redirected start URL sets the origin.
- `test_analyst.py` (fake client): overview contains groups and worst pages; `page_details` and
  `screenshot` work and are capped; partial on API error keeps crawl findings.
- Smart-mode tests from v2 keep passing unchanged.
- Report: site-map section renders; 60-page result shows 50 rows plus an expandable rest.

## Out of scope for piece 1

Pinpointed elements, new check rules, scan comparison, CI mode (pieces 2–5). Logged-in crawling,
JavaScript-only navigation (links that exist only as click handlers), per-site rate limiting
beyond the 2-per-host cap.

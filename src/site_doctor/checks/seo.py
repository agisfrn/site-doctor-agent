"""Search-engine basics: meta tags, headings, robots.txt and sitemap.xml."""

from collections.abc import Callable

from ..models import Finding, PageCapture


def check(cap: PageCapture) -> list[Finding]:
    url, meta = cap.final_url, cap.meta
    found = []
    if not meta.get("description"):
        found.append(Finding("seo.missing-description", "seo", "medium",
                             "No meta description", url,
                             'search results will show random page text'))
    if meta.get("h1_count", 0) != 1:
        found.append(Finding("seo.h1-count", "seo", "low", "Page should have exactly one h1",
                             url, f"{meta.get('h1_count', 0)} h1 elements"))
    if not meta.get("canonical"):
        found.append(Finding("seo.missing-canonical", "seo", "low", "No canonical link", url,
                             '<link rel="canonical"> is missing'))
    missing_og = [k for k in ("og_title", "og_image") if not meta.get(k)]
    if missing_og:
        names = ", ".join(k.replace("_", ":") for k in missing_og)
        found.append(Finding("seo.missing-og", "seo", "low", "Missing Open Graph tags", url,
                             f"missing {names} (link previews will look bare)"))
    return found


def check_site(origin: str, fetch: Callable[[str], int | str]) -> list[Finding]:
    found = []
    for path, fid, title in (("/robots.txt", "seo.missing-robots", "No robots.txt"),
                             ("/sitemap.xml", "seo.missing-sitemap", "No sitemap.xml")):
        url = origin.rstrip("/") + path
        status = fetch(url)
        if not (isinstance(status, int) and status < 400):
            evidence = f"HTTP {status}" if isinstance(status, int) else str(status)
            found.append(Finding(fid, "seo", "low", title, url, evidence))
    return found

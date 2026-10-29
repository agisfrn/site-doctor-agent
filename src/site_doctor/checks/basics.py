"""Status codes, page titles and JavaScript errors."""

from ..models import Finding, PageCapture


def check(cap: PageCapture) -> list[Finding]:
    url = cap.final_url
    found = []
    if cap.status is not None and cap.status >= 500:
        found.append(Finding("basics.server-error", "basics", "critical",
                             "Page returns a server error", url, f"HTTP {cap.status}"))
    elif cap.status is not None and cap.status >= 400:
        found.append(Finding("basics.client-error", "basics", "high",
                             "Page returns an error status", url, f"HTTP {cap.status}"))
    for err in cap.page_errors:
        found.append(Finding("basics.js-error", "basics", "high",
                             "Uncaught JavaScript error", url, err))
    if cap.console_errors:
        found.append(Finding("basics.console-error", "basics", "medium",
                             "Errors in the browser console", url,
                             " | ".join(cap.console_errors[:3])))
    if not cap.title.strip():
        found.append(Finding("basics.missing-title", "basics", "medium",
                             "Page has no title", url, "<title> is missing or empty"))
    return found


def load_failure(url: str, error: str, is_start: bool) -> Finding:
    return Finding("basics.page-load-failed", "basics", "critical" if is_start else "high",
                   "Page failed to load", url, error)

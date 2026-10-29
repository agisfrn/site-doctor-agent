"""Turn a link's HTTP status into a finding."""

from ..models import Finding


def link_finding(url: str, status: int | str, internal: bool,
                 found_on: str = "") -> Finding | None:
    if isinstance(status, int) and status < 400:
        return None
    evidence = f"HTTP {status}" if isinstance(status, int) else status
    if found_on:
        evidence += f" · linked from {found_on}"
    if isinstance(status, str):
        return Finding("links.unreachable", "links", "medium", "Link can't be reached", url, evidence)
    if internal:
        return Finding("links.broken-internal", "links", "high", "Broken internal link", url, evidence)
    return Finding("links.broken-external", "links", "medium", "Broken external link", url, evidence)

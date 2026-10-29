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
        rule = ("links.unreachable", "medium", "Link can't be reached")
    elif internal:
        rule = ("links.broken-internal", "high", "Broken internal link")
    else:
        rule = ("links.broken-external", "medium", "Broken external link")
    fid, severity, title = rule
    return Finding(fid, "links", severity, title, url, evidence)

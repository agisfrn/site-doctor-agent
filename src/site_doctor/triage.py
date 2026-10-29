"""Merge findings that share a rule id into one issue per problem."""

from .models import SEVERITIES, Finding, IssueGroup

MAX_EVIDENCE = 5


def _add_unique(items: list[str], new: list[str], cap: int | None = None) -> None:
    for x in new:
        if x and x not in items and (cap is None or len(items) < cap):
            items.append(x)


def group_findings(findings: list[Finding]) -> list[IssueGroup]:
    groups: dict[str, IssueGroup] = {}
    for f in findings:
        g = groups.get(f.id)
        if g is None:
            g = groups[f.id] = IssueGroup(f.id, f.category, f.severity, f.title, [], [], [])
        elif SEVERITIES.index(f.severity) < SEVERITIES.index(g.severity):
            g.severity = f.severity
        _add_unique(g.urls, [f.url])
        _add_unique(g.evidence, [f.evidence], MAX_EVIDENCE)
        _add_unique(g.screenshots, f.screenshots)
    return sorted(groups.values(),
                  key=lambda g: (SEVERITIES.index(g.severity), -len(g.urls), g.id))

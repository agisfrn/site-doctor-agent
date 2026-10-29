"""Accessibility problems reported by axe-core (run in the browser by browser.run_axe)."""

from ..models import Finding, PageCapture

SEVERITY = {"critical": "critical", "serious": "high", "moderate": "medium", "minor": "low"}


def check(cap: PageCapture) -> list[Finding]:
    return [
        Finding(f"accessibility.{v['id']}", "accessibility", SEVERITY.get(v["impact"], "low"),
                v["help"], cap.final_url, f"{v['nodes']} element(s)")
        for v in cap.axe_violations
    ]

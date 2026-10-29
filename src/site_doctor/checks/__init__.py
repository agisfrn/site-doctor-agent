"""Deterministic page checks. Each takes a PageCapture and returns Findings."""

from collections.abc import Callable

from ..models import Finding, PageCapture
from . import basics, mobile, performance, security, seo

PAGE_CHECKS: list[Callable[[PageCapture], list[Finding]]] = [
    basics.check,
    seo.check,
    performance.check,
    security.check,
    mobile.check,
]


def run_page_checks(cap: PageCapture) -> list[Finding]:
    return [f for check in PAGE_CHECKS for f in check(cap)]

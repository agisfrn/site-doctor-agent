"""How the page behaves on a phone-size screen."""

from ..models import Finding, PageCapture


def check(cap: PageCapture) -> list[Finding]:
    url, m = cap.final_url, cap.mobile
    found = []
    if not cap.meta.get("viewport"):
        found.append(Finding("mobile.missing-viewport", "mobile", "high",
                             "No viewport meta tag", url,
                             "phones will show a shrunken desktop page"))
    if m is None:
        return found
    if m.doc_width > m.viewport_width + 1:
        found.append(Finding("mobile.horizontal-scroll", "mobile", "high",
                             "Page scrolls sideways on phones", url,
                             f"page is {m.doc_width}px wide on a {m.viewport_width}px screen"))
    if m.small_tap_targets > 0:
        found.append(Finding("mobile.small-tap-targets", "mobile", "medium",
                             "Links or buttons are too small to tap", url,
                             f"{m.small_tap_targets} tap targets under 24×24px"))
    return found

"""Core Web Vitals, server response time, page weight and image sizes."""

from ..models import Finding, PageCapture

HEAVY_PAGE_BYTES = 3_000_000
HEAVY_IMAGE_BYTES = 500_000


def check(cap: PageCapture) -> list[Finding]:
    url, t = cap.final_url, cap.timings
    found = []

    def add(fid, severity, title, evidence, at=url):
        found.append(Finding(fid, "performance", severity, title, at, evidence))

    if t.lcp_ms is not None and t.lcp_ms > 2500:
        add("performance.lcp-slow", "high" if t.lcp_ms > 4000 else "medium",
            "Main content appears slowly (LCP)", f"Largest Contentful Paint {t.lcp_ms / 1000:.1f}s")
    if t.cls is not None and t.cls > 0.1:
        add("performance.cls-high", "high" if t.cls > 0.25 else "medium",
            "Layout jumps while loading (CLS)", f"Cumulative Layout Shift {t.cls:.2f}")
    if t.ttfb_ms is not None and t.ttfb_ms > 800:
        add("performance.ttfb-slow", "medium", "Server responds slowly",
            f"Time to first byte {t.ttfb_ms:.0f}ms")

    total = sum(r.transfer_size for r in cap.resources)
    if total > HEAVY_PAGE_BYTES:
        add("performance.heavy-page", "medium", "Page is heavy to download",
            f"{total / 1_000_000:.1f} MB transferred")

    sizes = {r.url: r.transfer_size for r in cap.resources}
    seen = set()
    for img in cap.images:
        # Vector images scale freely, and the browser invents a 300×150 "natural" size for them.
        if img.src in seen or img.src.split("?")[0].lower().endswith(".svg"):
            continue
        seen.add(img.src)
        size = sizes.get(img.src, 0)
        scaled_down = img.rendered_w > 0 and img.natural_w > 2 * img.rendered_w
        if size > HEAVY_IMAGE_BYTES or scaled_down:
            add("performance.oversized-image", "low", "Image is bigger than it needs to be",
                f"{img.natural_w}×{img.natural_h}px, {size // 1000} KB, "
                f"shown at {img.rendered_w}×{img.rendered_h}px: {img.src}")
    return found

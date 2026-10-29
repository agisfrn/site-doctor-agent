from site_doctor.models import MobileData, PageCapture, Timings


def make_capture(**overrides) -> PageCapture:
    """A capture of a healthy page; override fields to plant one problem at a time."""
    fields = dict(
        url="https://a.com/", final_url="https://a.com/", status=200, headers={},
        title="A healthy page",
        meta={"description": "A page.", "canonical": "https://a.com/", "og_title": "A",
              "og_image": "https://a.com/og.png", "viewport": "width=device-width", "h1_count": 1},
        links=[], console_errors=[], page_errors=[], resources=[], requested_urls=[], images=[],
        timings=Timings(ttfb_ms=100, load_ms=500, lcp_ms=1000, cls=0.0), axe_violations=[],
        mobile=MobileData(doc_width=390, viewport_width=390, small_tap_targets=0), load_time_s=0.5,
    )
    fields.update(overrides)
    return PageCapture(**fields)

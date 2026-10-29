import pytest

from site_doctor.checks import performance
from site_doctor.models import Image, Resource, Timings
from tests.checks.helpers import make_capture


def ids_and_sev(cap):
    return {(f.id, f.severity) for f in performance.check(cap)}


@pytest.mark.parametrize("lcp,sev", [(2400, None), (3000, "medium"), (4500, "high")])
def test_lcp_thresholds(lcp, sev):
    cap = make_capture(timings=Timings(ttfb_ms=100, load_ms=500, lcp_ms=lcp, cls=0.0))
    found = [f.severity for f in performance.check(cap) if f.id == "performance.lcp-slow"]
    assert found == ([sev] if sev else [])


@pytest.mark.parametrize("cls,sev", [(0.05, None), (0.2, "medium"), (0.3, "high")])
def test_cls_thresholds(cls, sev):
    cap = make_capture(timings=Timings(ttfb_ms=100, load_ms=500, lcp_ms=1000, cls=cls))
    found = [f.severity for f in performance.check(cap) if f.id == "performance.cls-high"]
    assert found == ([sev] if sev else [])


def test_slow_ttfb():
    cap = make_capture(timings=Timings(ttfb_ms=900, load_ms=1500, lcp_ms=1000, cls=0.0))
    assert ids_and_sev(cap) == {("performance.ttfb-slow", "medium")}


def test_oversized_image_and_heavy_page():
    cap = make_capture(images=[Image("https://a.com/big.jpg", 3000, 2000, 200, 133)],
                       resources=[Resource("https://a.com/big.jpg", "img", 3_100_000)])
    ids = [f.id for f in performance.check(cap)]
    assert ids.count("performance.oversized-image") == 1 and "performance.heavy-page" in ids


def test_image_scaled_down_more_than_twice_is_oversized():
    cap = make_capture(images=[Image("https://a.com/i.png", 900, 600, 400, 266)],
                       resources=[Resource("https://a.com/i.png", "img", 40_000)])
    assert ids_and_sev(cap) == {("performance.oversized-image", "low")}


def test_missing_timings_produce_nothing():
    assert performance.check(make_capture(timings=Timings(None, None, None, None))) == []


def test_svg_images_are_never_oversized():
    cap = make_capture(images=[Image("https://a.com/leaf.svg", 300, 150, 48, 48)],
                       resources=[Resource("https://a.com/leaf.svg", "img", 300)])
    assert performance.check(cap) == []

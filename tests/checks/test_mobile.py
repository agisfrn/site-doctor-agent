from site_doctor.checks import mobile
from site_doctor.models import MobileData
from tests.checks.helpers import make_capture


def test_clean_capture_has_no_mobile_findings():
    assert mobile.check(make_capture()) == []


def test_mobile_rules():
    cap = make_capture(mobile=MobileData(doc_width=1200, viewport_width=390, small_tap_targets=4))
    found = {f.id: f.evidence for f in mobile.check(cap)}
    assert set(found) == {"mobile.horizontal-scroll", "mobile.small-tap-targets"}
    assert found["mobile.horizontal-scroll"] == "page is 1200px wide on a 390px screen"
    assert found["mobile.small-tap-targets"] == "4 tap targets under 24×24px"


def test_missing_viewport_is_reported_even_without_mobile_data():
    cap = make_capture(meta={**make_capture().meta, "viewport": None}, mobile=None)
    assert [f.id for f in mobile.check(cap)] == ["mobile.missing-viewport"]


def test_no_viewport_page_is_not_also_reported_as_scrolling():
    cap = make_capture(meta={**make_capture().meta, "viewport": None},
                       mobile=MobileData(doc_width=980, viewport_width=390, small_tap_targets=0))
    assert [f.id for f in mobile.check(cap)] == ["mobile.missing-viewport"]

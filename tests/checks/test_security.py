from site_doctor.checks import security
from tests.checks.helpers import make_capture

SAFE = {"strict-transport-security": "max-age=1",
        "content-security-policy": "frame-ancestors 'none'",
        "x-content-type-options": "nosniff"}


def ids(**kw):
    return {f.id for f in security.check(make_capture(**kw))}


def test_secure_https_page_is_clean():
    assert ids(final_url="https://a.com/", headers=SAFE) == set()


def test_plain_http_is_critical_but_localhost_is_exempt():
    assert "security.not-https" in ids(final_url="http://a.com/", headers=SAFE)
    assert "security.not-https" not in ids(final_url="http://127.0.0.1:8000/", headers=SAFE)
    assert "security.not-https" not in ids(final_url="http://localhost/", headers=SAFE)


def test_mixed_content():
    cap = make_capture(final_url="https://a.com/", headers=SAFE,
                       requested_urls=["http://cdn.com/x.js"])
    assert [f.id for f in security.check(cap)] == ["security.mixed-content"]


def test_missing_headers():
    assert ids(final_url="https://a.com/", headers={}) == {
        "security.missing-hsts", "security.missing-csp", "security.missing-nosniff",
        "security.missing-frame-protection"}


def test_hsts_only_matters_on_https():
    assert "security.missing-hsts" not in ids(final_url="http://localhost/", headers={})


def test_x_frame_options_counts_as_frame_protection():
    headers = {**SAFE, "content-security-policy": "default-src 'self'", "x-frame-options": "DENY"}
    assert ids(final_url="https://a.com/", headers=headers) == set()

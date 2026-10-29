"""HTTPS, mixed content and security headers."""

from urllib.parse import urlparse

from ..models import Finding, PageCapture

LOCAL_HOSTS = {"localhost", "127.0.0.1"}


def check(cap: PageCapture) -> list[Finding]:
    url = cap.final_url
    parsed = urlparse(url)
    https = parsed.scheme == "https"
    h = cap.headers
    found = []

    if not https and parsed.hostname not in LOCAL_HOSTS:
        found.append(Finding("security.not-https", "security", "critical",
                             "Page is not served over HTTPS", url,
                             "traffic can be read or changed in transit"))
    if https:
        insecure = [u for u in cap.requested_urls if u.startswith("http://")]
        if insecure:
            found.append(Finding("security.mixed-content", "security", "high",
                                 "HTTPS page loads insecure resources", url,
                                 ", ".join(insecure[:3])))
        if "strict-transport-security" not in h:
            found.append(Finding("security.missing-hsts", "security", "medium",
                                 "No Strict-Transport-Security header", url,
                                 "browsers may still try plain HTTP first"))
    csp = h.get("content-security-policy", "")
    if not csp:
        found.append(Finding("security.missing-csp", "security", "medium",
                             "No Content-Security-Policy header", url,
                             "no protection layer against injected scripts"))
    if h.get("x-content-type-options", "").lower() != "nosniff":
        found.append(Finding("security.missing-nosniff", "security", "low",
                             "No X-Content-Type-Options: nosniff", url,
                             "browsers may guess file types"))
    if "x-frame-options" not in h and "frame-ancestors" not in csp:
        found.append(Finding("security.missing-frame-protection", "security", "low",
                             "Page can be embedded in other sites' frames", url,
                             "no X-Frame-Options or CSP frame-ancestors (clickjacking risk)"))
    return found

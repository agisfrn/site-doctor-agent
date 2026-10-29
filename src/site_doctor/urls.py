"""URL spelling helpers shared by both crawl modes."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def _is_tracking(key: str) -> bool:
    return key.lower().startswith("utm_") or key.lower() in TRACKING_PARAMS


def normalize_url(url: str) -> str:
    """One spelling per page: no #fragment, no tracking parameters, no trailing slash."""
    p = urlsplit(url.strip())
    path = p.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query = p.query
    if query:
        pairs = parse_qsl(query, keep_blank_values=True)
        if any(_is_tracking(k) for k, _ in pairs):
            query = urlencode([(k, v) for k, v in pairs if not _is_tracking(k)])
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, query, ""))


def origin_of(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme.lower()}://{p.netloc.lower()}"


def slug(url: str) -> str:
    path = urlsplit(url).path.strip("/")
    return re.sub(r"[^a-zA-Z0-9]+", "-", path).strip("-")[:40] or "home"

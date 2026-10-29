"""Plain data types shared by every part of Site Doctor."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

CATEGORIES = ("basics", "links", "accessibility", "seo", "performance", "security", "mobile")
SEVERITIES = ("critical", "high", "medium", "low")


@dataclass
class Finding:
    id: str
    category: str
    severity: str
    title: str
    url: str
    evidence: str = ""
    screenshots: list[str] = field(default_factory=list)


@dataclass
class Resource:
    url: str
    type: str
    transfer_size: int


@dataclass
class Image:
    src: str
    natural_w: int
    natural_h: int
    rendered_w: int
    rendered_h: int


@dataclass
class Timings:
    ttfb_ms: float | None
    load_ms: float | None
    lcp_ms: float | None
    cls: float | None


@dataclass
class MobileData:
    doc_width: int
    viewport_width: int
    small_tap_targets: int


@dataclass
class PageCapture:
    """Everything the browser recorded about one page. Checks only read this."""

    url: str
    final_url: str
    status: int | None
    headers: dict[str, str]
    title: str
    meta: dict
    links: list[str]
    console_errors: list[str]
    page_errors: list[str]
    resources: list[Resource]
    requested_urls: list[str]
    images: list[Image]
    timings: Timings
    axe_violations: list[dict]
    mobile: MobileData | None
    load_time_s: float


@dataclass
class PageResult:
    url: str
    status: int | None
    title: str
    load_time_s: float
    lcp_ms: float | None
    findings: list[Finding]
    error: str | None = None


@dataclass
class IssueGroup:
    """One problem, merged across every page it appears on."""

    id: str
    category: str
    severity: str
    title: str
    urls: list[str]
    evidence: list[str]
    screenshots: list[str]
    explanation: str = ""
    fix: str = ""


@dataclass
class Usage:
    input_tokens: int = 0  # uncached input only, as the API reports it
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class ScanResult:
    target: str
    model: str
    started_at: str
    duration_s: float
    pages: list[PageResult]
    findings: list[Finding]
    groups: list[IssueGroup]
    scores: dict[str, int]
    overall: int
    grade: str
    summary: str
    usage: Usage
    partial: bool = False
    partial_reason: str = ""
    mode: str = "full"
    links_checked: int = 0
    page_groups: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ScanResult:
        pages = [
            PageResult(**{**p, "findings": [Finding(**f) for f in p["findings"]]})
            for p in d["pages"]
        ]
        return cls(**{
            **d,
            "pages": pages,
            "findings": [Finding(**f) for f in d["findings"]],
            "groups": [IssueGroup(**g) for g in d["groups"]],
            "usage": Usage(**d["usage"]),
        })

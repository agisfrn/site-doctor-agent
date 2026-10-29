"""Scan settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def normalize_target(raw: str) -> str:
    """Trim the URL a person typed and add https:// when no scheme was given."""
    url = raw.strip()
    return url if "://" in url else "https://" + url


def _now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@dataclass
class ScanConfig:
    url: str
    max_pages: int = 15
    max_steps: int = 60
    model: str = "claude-opus-5-5"
    effort: str = "medium"
    mobile: bool = True
    out_dir: Path = Path("reports")
    stamp: str = field(default_factory=_now_stamp)

    @property
    def run_dir(self) -> Path:
        host = urlparse(self.url).netloc.replace(":", "-")
        return self.out_dir / f"{host}-{self.stamp}"

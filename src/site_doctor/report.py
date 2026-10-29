"""Turn a ScanResult into report.html (self-contained) and findings.json."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, PackageLoader

from .models import CATEGORIES, SEVERITIES, ScanResult, Usage

# USD per million input / output tokens.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.0, 50.0),
}

CATEGORY_LABELS = {
    "basics": "Basics", "links": "Links", "accessibility": "Accessibility", "seo": "SEO",
    "performance": "Performance", "security": "Security", "mobile": "Mobile",
}

_env = Environment(loader=PackageLoader("site_doctor", "templates"), autoescape=True,
                   trim_blocks=True, lstrip_blocks=True)


def estimate_cost(model: str, usage: Usage) -> float | None:
    if model not in PRICES:
        return None
    per_in, per_out = PRICES[model]
    return (usage.input_tokens * per_in + usage.output_tokens * per_out) / 1_000_000


def _score_tone(value: int) -> str:
    return "good" if value >= 90 else "fair" if value >= 70 else "poor"


def render_html(result: ScanResult) -> str:
    severity_counts = Counter(g.severity for g in result.groups)
    cost = estimate_cost(result.model, result.usage)
    return _env.get_template("report.html.j2").render(
        r=result,
        host=urlsplit(result.target).netloc or result.target,
        categories=[(c, CATEGORY_LABELS[c], result.scores.get(c, 100),
                     _score_tone(result.scores.get(c, 100))) for c in CATEGORIES],
        labels=CATEGORY_LABELS,
        severities=SEVERITIES,
        severity_counts={s: severity_counts.get(s, 0) for s in SEVERITIES},
        overall_tone=_score_tone(result.overall),
        cost=f"${cost:.2f}" if cost is not None else "n/a",
        issues_per_page=Counter(f.url for f in result.findings),
    )


def write_report(result: ScanResult, run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "findings.json").write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    html_path = run_dir / "report.html"
    html_path.write_text(render_html(result), encoding="utf-8")
    return html_path

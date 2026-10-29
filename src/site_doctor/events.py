"""Events the agent emits while it works. The UI turns them into output."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScanStarted:
    target: str
    model: str
    max_pages: int


@dataclass(frozen=True)
class AgentNote:
    text: str


@dataclass(frozen=True)
class StepStarted:
    step: int
    tool: str
    url: str


@dataclass(frozen=True)
class StepFinished:
    step: int
    tool: str
    url: str
    summary: str
    ok: bool
    pages_used: int
    max_pages: int
    tally: dict[str, int]


@dataclass(frozen=True)
class TriageStarted:
    groups: int


@dataclass(frozen=True)
class ScanFinished:
    partial: bool
    reason: str


Event = ScanStarted | AgentNote | StepStarted | StepFinished | TriageStarted | ScanFinished

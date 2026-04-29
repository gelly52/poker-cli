"""Shared data models for Poker CLI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class Severity(str, Enum):
    """Finding severity levels."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    """A security finding discovered during a scan."""

    rule_id: str
    title: str
    severity: Severity
    category: str
    path: str
    line: int
    evidence: str
    recommendation: str

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["severity"] = self.severity.value
        return data

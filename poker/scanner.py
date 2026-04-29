"""Scan orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from poker.detectors import AgentToolDetector, PromptDetector, SecretDetector
from poker.models import Finding
from poker.workspace import iter_text_files, read_text


class Detector(Protocol):
    """Minimal detector contract."""

    name: str

    def scan(self, path: Path, relative_path: str, content: str) -> list[Finding]: ...


DEFAULT_DETECTORS: tuple[Detector, ...] = (
    SecretDetector(),
    PromptDetector(),
    AgentToolDetector(),
)

SEVERITY_RANK = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "info": 4,
}


def scan_path(root: Path, detectors: tuple[Detector, ...] = DEFAULT_DETECTORS) -> list[Finding]:
    """Run all detectors against a file or directory."""

    root = root.resolve()
    findings: list[Finding] = []
    paths = [root] if root.is_file() else iter_text_files(root)

    for path in paths:
        relative_path = path.name if root.is_file() else path.relative_to(root).as_posix()
        content = read_text(path)
        for detector in detectors:
            findings.extend(detector.scan(path, relative_path, content))

    return sorted(
        findings,
        key=lambda item: (SEVERITY_RANK[item.severity.value], item.path, item.line),
    )

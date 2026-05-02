"""扫描编排引擎。"""
from pathlib import Path
from typing import Protocol

from poker.capabilities.scan.detectors import AgentToolDetector, PromptDetector, SecretDetector
from poker.models import Finding
from poker.workspace import iter_text_files, read_text


class Detector(Protocol):
    """扫描器协议：所有扫描器必须实现此接口。"""

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
    """对文件或目录执行全部扫描器。

    单个 detector / 文件失败时跳过并继续，不影响其他 detector。
    """

    root = root.resolve()
    findings: list[Finding] = []
    paths = [root] if root.is_file() else iter_text_files(root)

    for path in paths:
        relative_path = path.name if root.is_file() else path.relative_to(root).as_posix()
        try:
            content = read_text(path)
        except Exception:
            continue
        for detector in detectors:
            try:
                findings.extend(detector.scan(path, relative_path, content))
            except Exception:
                # 单个 detector 失败不影响其他
                continue

    return sorted(
        findings,
        key=lambda item: (SEVERITY_RANK[item.severity.value], item.path, item.line),
    )

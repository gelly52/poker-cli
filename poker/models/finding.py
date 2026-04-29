"""安全扫描发现项数据模型。"""
from dataclasses import asdict, dataclass
from enum import Enum


class Severity(str, Enum):
    """风险严重等级。"""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass(frozen=True)
class Finding:
    """一条安全扫描发现。"""

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

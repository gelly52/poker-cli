"""Secret scanning detector."""

from __future__ import annotations

import re
from pathlib import Path

from poker.models import Finding, Severity

SECRET_PATTERNS = {
    "generic-api-key": re.compile(
        r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"]?([a-z0-9_\-]{16,})"
    ),
    "openai-key": re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    "private-key": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
}


class SecretDetector:
    """Detect likely hard-coded credentials."""

    name = "secret-scan"

    def scan(self, path: Path, relative_path: str, content: str) -> list[Finding]:
        findings: list[Finding] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            for rule_id, pattern in SECRET_PATTERNS.items():
                if pattern.search(line):
                    findings.append(
                        Finding(
                            rule_id=rule_id,
                            title="Possible hard-coded secret",
                            severity=Severity.HIGH,
                            category="secret",
                            path=relative_path,
                            line=line_number,
                            evidence=line.strip()[:160],
                            recommendation="Move secrets to a secure secret manager or environment variable.",
                        )
                    )
        return findings

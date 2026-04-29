"""Prompt safety detector."""

from __future__ import annotations

from pathlib import Path

from poker.models import Finding, Severity

PROMPT_HINTS = (
    "system prompt",
    "developer prompt",
    "ignore previous",
    "ignore all previous",
    "tool call",
    "agent_scratchpad",
)

SAFE_BOUNDARY_HINTS = (
    "untrusted",
    "do not follow instructions from",
    "do not reveal",
    "never reveal",
    "trusted",
    "policy",
)


class PromptDetector:
    """Flag prompt-like files that may lack trust-boundary guidance."""

    name = "prompt-audit"

    def scan(self, path: Path, relative_path: str, content: str) -> list[Finding]:
        lowered = content.lower()
        looks_like_prompt = any(hint in lowered for hint in PROMPT_HINTS)
        has_boundary = any(hint in lowered for hint in SAFE_BOUNDARY_HINTS)

        if not looks_like_prompt or has_boundary:
            return []

        return [
            Finding(
                rule_id="prompt-missing-trust-boundary",
                title="Prompt may lack trust-boundary guidance",
                severity=Severity.MEDIUM,
                category="prompt-injection",
                path=relative_path,
                line=1,
                evidence="Prompt-like content found without obvious untrusted-input guidance.",
                recommendation="Add explicit instructions for untrusted content, data leakage, and tool-use boundaries.",
            )
        ]

"""Runtime 实时检测：prompt injection / 敏感信息泄露 / token 异常。

模式自包含；不反向 import poker，保证 poker_observer 可单独 pip 安装。
雷同模式（如 secret patterns）跟 poker.capabilities.scan.detectors 维持各自一份。

每个 detect_* 返回 list[dict]：
  {"rule_id": ..., "severity": "critical|high|medium|low|info", "evidence": "..."}
"""

import re
from typing import Any

# ---------- pattern 表 ----------

PROMPT_INJECTION_PATTERNS: list[tuple[str, str, str]] = [
    (
        r"(?i)ignore\s+(all\s+)?(the\s+)?(previous|prior|above)\s+(instructions|prompts?|rules?)",
        "prompt-injection-ignore-previous",
        "high",
    ),
    (r"(?i)\bDAN\b|\bdo\s+anything\s+now\b", "prompt-injection-dan", "high"),
    (
        r"(?i)(reveal|show|tell\s+me|print|leak|expose)\s+(your\s+|the\s+)?(system\s+)?prompt",
        "prompt-exfil-sysprompt",
        "high",
    ),
    (
        r"(?i)you\s+are\s+now\s+in\s+(developer|admin|jailbreak|root|godmode)\s+mode",
        "prompt-injection-mode",
        "high",
    ),
    (r"<\s*\|.{0,40}\|\s*>", "prompt-injection-special-token", "medium"),
]

SECRET_LEAK_PATTERNS: list[tuple[str, str, str]] = [
    (r"sk-[A-Za-z0-9_\-]{20,}", "secret-leak-openai-key", "critical"),
    (
        r"-----BEGIN (RSA |EC |OPENSSH |DSA |)PRIVATE KEY-----",
        "secret-leak-private-key",
        "critical",
    ),
    (r"AKIA[0-9A-Z]{16}", "secret-leak-aws-access-key", "critical"),
    (r"ghp_[A-Za-z0-9]{30,}", "secret-leak-github-pat", "critical"),
    (r"xox[baprs]-[A-Za-z0-9-]{10,}", "secret-leak-slack-token", "high"),
    (
        r"(?i)(api[_-]?key|secret|token|password)\s*[=:]\s*['\"]?([a-z0-9_\-]{16,})",
        "secret-leak-generic-key",
        "high",
    ),
]

DEFAULT_TOKEN_THRESHOLD = 8000


# ---------- 内部 ----------

def _scan_text(text: Any, patterns: list[tuple[str, str, str]]) -> list[dict]:
    if not isinstance(text, str) or not text:
        return []
    out: list[dict] = []
    seen: set[str] = set()  # 同一 rule_id 一次足矣
    for pat, rule_id, severity in patterns:
        if rule_id in seen:
            continue
        try:
            m = re.search(pat, text)
        except re.error:
            continue
        if m:
            seen.add(rule_id)
            out.append(
                {
                    "rule_id": rule_id,
                    "severity": severity,
                    "evidence": _evidence_snippet(m, text),
                }
            )
    return out


def _evidence_snippet(m: "re.Match[str]", text: str, span: int = 100) -> str:
    start = max(m.start() - span // 2, 0)
    end = min(m.end() + span // 2, len(text))
    snippet = text[start:end].replace("\n", "↵")
    return snippet[:160]


# ---------- 公开 API ----------

def detect_prompt_injection(text: Any) -> list[dict]:
    """检测用户 prompt 中的 injection / jailbreak 信号。"""
    return _scan_text(text, PROMPT_INJECTION_PATTERNS)


def detect_secret_leak(text: Any) -> list[dict]:
    """检测 LLM 响应里疑似泄露的 secrets / 凭据。"""
    return _scan_text(text, SECRET_LEAK_PATTERNS)


def detect_token_anomaly(
    usage: Any,
    threshold: int = DEFAULT_TOKEN_THRESHOLD,
) -> list[dict]:
    """单次调用 token 用量异常（默认 ≥ 8000）。"""
    if not isinstance(usage, dict):
        return []
    raw_total = usage.get("total_tokens")
    if raw_total is None:
        # 部分 provider 只给 prompt+completion
        try:
            raw_total = int(usage.get("prompt_tokens") or 0) + int(
                usage.get("completion_tokens") or 0
            )
        except (TypeError, ValueError):
            return []
    try:
        total = int(raw_total)
    except (TypeError, ValueError):
        return []
    if total >= threshold:
        return [
            {
                "rule_id": "token-usage-anomaly",
                "severity": "medium",
                "evidence": f"total_tokens={total} (>= {threshold})",
            }
        ]
    return []


def detect_in_prompt(text: Any) -> list[dict]:
    """对发出 LLM 的 prompt 跑 prompt-injection 检测。"""
    return detect_prompt_injection(text)


def detect_in_response(
    text: Any,
    usage: Any = None,
    token_threshold: int = DEFAULT_TOKEN_THRESHOLD,
) -> list[dict]:
    """对 LLM 响应文本跑 secret-leak + token-anomaly 联合检测。"""
    out = detect_secret_leak(text)
    out.extend(detect_token_anomaly(usage, token_threshold))
    return out

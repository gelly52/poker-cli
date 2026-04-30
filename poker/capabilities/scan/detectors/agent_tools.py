"""Agent 工具风险检测器。"""
import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from poker.models import Finding, Severity

CONFIRMATION_HINTS = ("confirm", "approval", "approve", "consent", "permission")
SHELL_CALLS = {
    "os.system",
    "os.popen",
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_call",
    "subprocess.check_output",
    "asyncio.create_subprocess_exec",
    "asyncio.create_subprocess_shell",
}
NETWORK_PREFIXES = ("requests.", "httpx.", "urllib.request.")
FILE_ACCESS_CALLS = ("open", "Path.open", "read_text", "read_bytes", "write_text", "write_bytes", "unlink", "rename", "replace")


@dataclass(frozen=True)
class ToolRiskRule:
    rule_id: str
    title: str
    severity: Severity
    matcher: Callable[[ast.Call, str], bool]
    recommendation: str


TOOL_RISK_RULES = (
    ToolRiskRule(
        "arbitrary-command-execution",
        "Agent tool exposes command execution",
        Severity.HIGH,
        lambda call, call_name: call_name in SHELL_CALLS,
        "Require explicit user approval and restrict commands with an allowlist.",
    ),
    ToolRiskRule(
        "unsafe-file-access",
        "Agent tool can access local files",
        Severity.MEDIUM,
        lambda call, call_name: _is_file_access_call(call, call_name),
        "Restrict paths to a workspace allowlist and require confirmation for writes or deletes.",
    ),
    ToolRiskRule(
        "ssrf-risk",
        "Agent tool can fetch user-controlled URLs",
        Severity.MEDIUM,
        lambda call, call_name: call_name.startswith(NETWORK_PREFIXES),
        "Validate URLs, block private networks, and restrict outbound destinations.",
    ),
)


class AgentToolDetector:
    """检测 LangChain 风格工具中暴露的危险能力。"""

    name = "agent-tool-audit"

    def scan(self, path: Path, relative_path: str, content: str) -> list[Finding]:
        if path.suffix != ".py":
            return []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        findings: list[Finding] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_tool(node):
                findings.extend(_scan_tool_function(node, relative_path, content))

        return findings


def _scan_tool_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    relative_path: str,
    content: str,
) -> list[Finding]:
    findings: list[Finding] = []

    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue

        call_name = _call_name(child.func)
        for rule in TOOL_RISK_RULES:
            if rule.matcher(child, call_name):
                findings.append(_finding(rule, node.name, call_name, relative_path, child.lineno))

    source = ast.get_source_segment(content, node) or ""
    if findings and not any(hint in source.lower() for hint in CONFIRMATION_HINTS):
        findings.append(
            Finding(
                rule_id="agent-tool-missing-confirmation",
                title="Risky agent tool may lack confirmation control",
                severity=Severity.MEDIUM,
                category="tool-permission",
                path=relative_path,
                line=node.lineno,
                evidence=f"Tool `{node.name}` exposes risky behavior without obvious confirmation logic.",
                recommendation="Add confirmation, policy checks, or explicit permission gates before executing risky actions.",
            )
        )

    return findings


def _finding(rule: ToolRiskRule, tool_name: str, call_name: str, path: str, line: int) -> Finding:
    return Finding(
        rule_id=rule.rule_id,
        title=rule.title,
        severity=rule.severity,
        category="tool-permission",
        path=path,
        line=line,
        evidence=f"Tool `{tool_name}` calls `{call_name}`.",
        recommendation=rule.recommendation,
    )


def _is_tool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(_decorator_name(decorator) == "tool" for decorator in node.decorator_list)


def _decorator_name(node: ast.AST) -> str:
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_file_access_call(call: ast.Call, call_name: str) -> bool:
    if call_name.endswith(FILE_ACCESS_CALLS):
        return True
    return call_name == "open" or call_name.endswith(".open")

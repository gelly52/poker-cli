"""Agent tool risk detector."""

from __future__ import annotations

import ast
from pathlib import Path

from poker.models import Finding, Severity

CONFIRMATION_HINTS = ("confirm", "approval", "approve", "consent", "permission")
SHELL_CALLS = {
    "os.system",
    "subprocess.run",
    "subprocess.call",
    "subprocess.Popen",
    "subprocess.check_call",
    "subprocess.check_output",
}
NETWORK_PREFIXES = ("requests.", "httpx.", "urllib.request.")
FILE_WRITE_METHODS = ("write_text", "write_bytes", "unlink", "rename", "replace")


class AgentToolDetector:
    """Detect risky capabilities exposed through LangChain-style tools."""

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
    risky = False

    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue

        call_name = _call_name(child.func)
        if call_name in SHELL_CALLS:
            risky = True
            findings.append(
                Finding(
                    rule_id="agent-tool-shell-execution",
                    title="Agent tool exposes shell execution",
                    severity=Severity.HIGH,
                    category="tool-permission",
                    path=relative_path,
                    line=child.lineno,
                    evidence=f"Tool `{node.name}` calls `{call_name}`.",
                    recommendation="Require explicit user approval and restrict commands with an allowlist.",
                )
            )

        if _is_file_write_call(child, call_name):
            risky = True
            findings.append(
                Finding(
                    rule_id="agent-tool-file-write",
                    title="Agent tool can modify files",
                    severity=Severity.MEDIUM,
                    category="tool-permission",
                    path=relative_path,
                    line=child.lineno,
                    evidence=f"Tool `{node.name}` calls `{call_name}`.",
                    recommendation="Show a diff and require confirmation before writing or deleting files.",
                )
            )

        if call_name.startswith(NETWORK_PREFIXES):
            risky = True
            findings.append(
                Finding(
                    rule_id="agent-tool-network-access",
                    title="Agent tool can make network requests",
                    severity=Severity.MEDIUM,
                    category="tool-permission",
                    path=relative_path,
                    line=child.lineno,
                    evidence=f"Tool `{node.name}` calls `{call_name}`.",
                    recommendation="Restrict outbound destinations and require approval for sensitive requests.",
                )
            )

    source = ast.get_source_segment(content, node) or ""
    if risky and not any(hint in source.lower() for hint in CONFIRMATION_HINTS):
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


def _is_file_write_call(call: ast.Call, call_name: str) -> bool:
    if call_name.endswith(FILE_WRITE_METHODS):
        return True

    if call_name not in {"open", "Path.open"} and not call_name.endswith(".open"):
        return False

    mode = _open_mode(call)
    return any(flag in mode for flag in ("w", "a", "x", "+"))


def _open_mode(call: ast.Call) -> str:
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        return str(call.args[1].value)

    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)

    return "r"

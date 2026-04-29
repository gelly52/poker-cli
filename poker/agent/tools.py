"""Agent 工具注册。

Agent 通过此模块暴露操作能力给 LLM，不直接依赖具体能力实现。
"""
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from poker.capabilities.scan.engine import scan_path


@tool
def scan_project(target: str) -> str:
    """对指定文件或目录执行 AI 安全扫描，返回发现的漏洞列表。"""
    path = Path(target)
    if not path.exists():
        return f"目标不存在: {target}"

    findings = scan_path(path)
    if not findings:
        return "未发现安全风险。"

    lines = [f"发现 {len(findings)} 个安全风险:\n"]
    for f in findings:
        lines.append(f"  [{f.severity.value}] {f.title} ({f.rule_id})")
        lines.append(f"    位置: {f.path}:{f.line}")
        lines.append(f"    证据: {f.evidence}")
        lines.append(f"    建议: {f.recommendation}\n")
    return "\n".join(lines)


def get_agent_tools() -> list:
    """返回 Agent 当前可用的工具列表。新增工具只需在此注册。"""
    return [scan_project]

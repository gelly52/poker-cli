"""Audit 能力分发：根据 dimension 调用对应模块。

MVP 只支持 tools 维度；未来加 rag / mcp / prompt 时只需在此注册新分支。
"""
from pathlib import Path
from typing import Any


def run_audit(dimension: str, project_root: Path, llm: Any, console: Any) -> None:
    """根据 dimension 分发到具体审计模块。"""
    if dimension == "tools":
        from poker.capabilities.audit.tools import interactive_audit_tools
        interactive_audit_tools(project_root, llm, console)
        return

    raise NotImplementedError(
        f"维度 '{dimension}' 暂未支持。MVP 仅支持: tools；Phase 2 将加入 rag / mcp / prompt。"
    )

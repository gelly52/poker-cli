"""Audit 能力分发：根据 dimension 调用对应模块。

支持的维度：tools / rag / mcp / prompt / mcp_schema。
新增维度只需在此注册新分支。
"""
from pathlib import Path
from typing import Any, Optional


def run_audit(
    dimension: str,
    project_root: Path,
    llm: Any,
    console: Any,
    schema_path: Optional[Path] = None,
) -> None:
    """根据 dimension 分发到具体审计模块。schema_path 仅 mcp_schema 用。"""
    if dimension == "tools":
        from poker.capabilities.audit.tools import interactive_audit_tools
        interactive_audit_tools(project_root, llm, console)
        return

    if dimension == "rag":
        from poker.capabilities.audit.rag import interactive_audit_rag
        interactive_audit_rag(project_root, llm, console)
        return

    if dimension == "mcp":
        from poker.capabilities.audit.mcp import interactive_audit_mcp
        interactive_audit_mcp(project_root, llm, console)
        return

    if dimension == "prompt":
        from poker.capabilities.audit.prompt import interactive_audit_prompt
        interactive_audit_prompt(project_root, llm, console)
        return

    if dimension == "mcp_schema":
        if schema_path is None:
            console.print("[red]/audit mcp_schema 必须配 --schema <path>[/red]")
            return
        from poker.capabilities.audit.mcp_schema import run_mcp_schema_audit
        run_mcp_schema_audit(schema_path, project_root, console)
        return

    raise NotImplementedError(
        f"维度 '{dimension}' 未支持。支持: tools / rag / mcp / prompt / mcp_schema。"
    )

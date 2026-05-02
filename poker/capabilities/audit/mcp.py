"""审计 MCP server 配置：扫描 .mcp.json / Server() 实例 → 权限 / 暴露面 / 沙箱评估。

支持：
  - JSON 配置：.mcp.json / mcp.json / mcp_config.json / claude_desktop_config.json
  - Python AST：MCPServer(...) / Server(...) / FastMCP(...) 实例化
"""
import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from poker.capabilities.audit._common import (
    Risk,
    overall_severity,
    prompt_selection,
    render_risks_block,
)
from poker.state import save_audit
from poker.workspace import iter_text_files


# ---------- 数据结构 ----------

@dataclass
class McpServerEntry:
    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class McpInfo:
    file: str
    kind: str  # 'json_config' | 'python_server'
    servers: list[McpServerEntry] = field(default_factory=list)
    snippet: str = ""


@dataclass
class McpAuditResult:
    info: McpInfo
    risks: list[Risk] = field(default_factory=list)
    overall_severity: str = "info"
    llm_summary: str = ""


# ---------- 识别常量 ----------

_MCP_JSON_NAMES = {
    ".mcp.json", "mcp.json", "mcp_config.json", "claude_desktop_config.json",
}
_SHELL_BINARIES = {"bash", "sh", "cmd", "cmd.exe", "powershell", "pwsh", "zsh", "ksh"}
_REMOTE_RUNNERS = {"npx", "uvx", "bunx", "pnpx"}
_DANGEROUS_ARG_RE = re.compile(r"--allow[-_]?net|--unsafe|^-c$", re.I)
_PYTHON_SERVER_NAMES = {"Server", "MCPServer", "FastMCP"}


# ---------- 静态识别 ----------

def find_mcp_configs(project_root: Path) -> list[McpInfo]:
    """扫描 MCP 配置：JSON 文件 + Python `Server(...)` 实例。"""
    out: list[McpInfo] = []
    for f in iter_text_files(project_root):
        if f.name in _MCP_JSON_NAMES:
            info = _parse_mcp_json(f, project_root)
            if info is not None:
                out.append(info)
        elif f.suffix == ".py":
            info = _parse_python_mcp(f, project_root)
            if info is not None:
                out.append(info)
    return out


def _parse_mcp_json(file_path: Path, project_root: Path) -> McpInfo | None:
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    rel = _rel(file_path, project_root)

    servers: list[McpServerEntry] = []
    raw_servers = data.get("mcpServers") or data.get("servers") or {}
    if isinstance(raw_servers, dict):
        for name, entry in raw_servers.items():
            if not isinstance(entry, dict):
                continue
            servers.append(McpServerEntry(
                name=name,
                command=str(entry.get("command", "")),
                args=[str(a) for a in entry.get("args", [])],
                env=entry.get("env", {}) if isinstance(entry.get("env"), dict) else {},
                raw=entry,
            ))

    return McpInfo(file=rel, kind="json_config", servers=servers, snippet=str(data)[:300])


def _parse_python_mcp(file_path: Path, project_root: Path) -> McpInfo | None:
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content)
    except (SyntaxError, OSError):
        return None

    servers: list[McpServerEntry] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = _name_of(node.func)
        if func_name not in _PYTHON_SERVER_NAMES:
            continue
        name = "<unnamed>"
        for kw in node.keywords:
            if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                name = str(kw.value.value)
        if node.args and isinstance(node.args[0], ast.Constant):
            name = str(node.args[0].value)
        servers.append(McpServerEntry(name=name, command=func_name))

    if not servers:
        return None
    return McpInfo(
        file=_rel(file_path, project_root), kind="python_server",
        servers=servers, snippet="",
    )


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _rel(file: Path, root: Path) -> str:
    try:
        return file.relative_to(root).as_posix()
    except ValueError:
        return str(file)


# ---------- 审计 ----------

def audit_mcp(info: McpInfo, llm: Any = None) -> McpAuditResult:
    risks: list[Risk] = []

    for s in info.servers:
        cmd_basename = Path(s.command).name.lower() if s.command else ""

        if cmd_basename in _SHELL_BINARIES:
            risks.append(Risk(
                check="shell_wrapper", severity="high",
                evidence=f"server '{s.name}' 命令 '{s.command}' 是 shell 解释器",
                recommendation="改用具体可执行文件；shell wrapper 易被 args 注入",
            ))

        if cmd_basename in _REMOTE_RUNNERS:
            risks.append(Risk(
                check="remote_executable", severity="medium",
                evidence=f"server '{s.name}' 用 {cmd_basename} 拉远端包",
                recommendation="锁定包版本 / 校验 SHA；远端代码每次运行可能被替换",
            ))

        for arg in s.args:
            if _DANGEROUS_ARG_RE.search(arg):
                risks.append(Risk(
                    check="dangerous_arg", severity="high",
                    evidence=f"server '{s.name}' 包含危险参数: {arg}",
                    recommendation="审查参数；移除 -c / --unsafe / --allow-net 等放权选项",
                ))
                break

        for env_key in s.env:
            if any(p in env_key.upper() for p in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                risks.append(Risk(
                    check="env_secret_inline", severity="medium",
                    evidence=f"server '{s.name}' env 中含 {env_key}（可能配置文件明文）",
                    recommendation="敏感凭据用 env_file / OS keychain 而非配置文件明文",
                ))
                break

        # tools / allowed_tools 含 '*' 通配
        for k, v in s.raw.items():
            if k.lower() in ("tools", "allowed_tools", "allowedtools"):
                serialized = v if isinstance(v, str) else " ".join(map(str, v if isinstance(v, list) else []))
                if "*" in serialized:
                    risks.append(Risk(
                        check="wildcard_tools", severity="high",
                        evidence=f"server '{s.name}' tools 含通配符 '*'",
                        recommendation="显式列出允许的工具名，避免 wildcard 暴露",
                    ))
                    break

    if not info.servers:
        risks.append(Risk(
            check="empty_config", severity="info",
            evidence=f"{info.file} 未解析出 server 条目",
            recommendation="确认配置格式（mcpServers / servers 字段）",
        ))

    overall = overall_severity(risks)
    llm_summary = ""
    if llm is not None and info.servers:
        try:
            llm_summary = _llm_assess(info, llm)
        except Exception as e:
            llm_summary = f"（LLM 评估失败：{e}）"

    return McpAuditResult(
        info=info, risks=risks,
        overall_severity=overall, llm_summary=llm_summary,
    )


def _llm_assess(info: McpInfo, llm: Any) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage
    sys_prompt = (
        "你是 MCP server 安全审计员。评估给定配置的权限边界、暴露面、沙箱情况，"
        "给出 3-5 条具体结论，每条一行。"
    )
    servers_repr = "\n".join(
        f"  - {s.name}: cmd={s.command} args={s.args} env_keys={list(s.env.keys())}"
        for s in info.servers
    )
    user_msg = f"文件: {info.file}\n类型: {info.kind}\n服务器:\n{servers_repr}"
    response = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=user_msg)])
    content = response.content if hasattr(response, "content") else str(response)
    return content if isinstance(content, str) else str(content)


# ---------- 交互式入口 ----------

def interactive_audit_mcp(project_root: Path, llm: Any, console: Console) -> None:
    infos = find_mcp_configs(project_root)
    if not infos:
        console.print("[yellow]未发现 MCP 配置（.mcp.json / claude_desktop_config.json / Server(...)）[/yellow]")
        return

    table = Table(title=f"发现 {len(infos)} 份 MCP 配置")
    table.add_column("#", style="bold")
    table.add_column("文件")
    table.add_column("类型")
    table.add_column("server 数")
    for i, info in enumerate(infos, 1):
        table.add_row(str(i), info.file, info.kind, str(len(info.servers)))
    console.print(table)

    selected = prompt_selection(
        infos, label=lambda i: i.file, console=console, kind="配置",
    )
    if selected is None:
        return

    for info in selected:
        console.print(f"\n[bold]审计 {info.file}[/bold]  [dim]{len(info.servers)} 个 server[/dim]")
        result = audit_mcp(info, llm)
        render_risks_block(console, result.risks, result.overall_severity, result.llm_summary)
        target = info.file.replace("/", "_").replace(".", "_")
        path = save_audit(project_root, "mcp", target, _result_to_dict(result))
        console.print(f"[dim]结果已保存：{path}[/dim]")


def _result_to_dict(result: McpAuditResult) -> dict:
    return {
        "info": {
            "file": result.info.file,
            "kind": result.info.kind,
            "servers": [asdict(s) for s in result.info.servers],
        },
        "risks": [asdict(r) for r in result.risks],
        "overall_severity": result.overall_severity,
        "llm_summary": result.llm_summary,
    }

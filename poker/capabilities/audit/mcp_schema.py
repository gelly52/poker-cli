"""MCP schema 静态规则审计：纯解析 tools/list JSON，按硬规则确定性打分。

跟 `audit/mcp.py`（LLM 高层评估配置）互补：本模块只看 tool 的 inputSchema 形态，
不依赖 LLM、不连真实 server。规则数据驱动 —— RULES 列表里每条 (rule_id, severity, check)。
check(tool: dict) → str | None：返回 reason 即命中，None 即不命中。

支持输入：
  - {"tools": [...]} —— 标准 tools/list 响应
  - [...] —— 直接 tools 数组
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from rich.console import Console
from rich.table import Table

from poker.capabilities.audit._common import SEVERITY_RANK, SEVERITY_STYLES
from poker.state import save_audit


# ---------- 数据结构 ----------

@dataclass
class Rule:
    rule_id: str
    severity: str  # high | medium | low
    check: Callable[[dict], Optional[str]]


@dataclass
class ToolReport:
    name: str
    overall: str  # high | medium | low | safe
    risks: list[tuple[str, str, str]] = field(default_factory=list)  # (rule_id, severity, reason)


# ---------- 关键字常量 ----------

_SHELL_KEYWORDS = ("command", "cmd", "shell", "script", "exec")
_PATH_KEYWORDS = ("path", "file", "dir", "folder")
_URL_KEYWORDS = ("url", "endpoint", "uri", "host")
_STRUCTURED_KEYWORDS = ("data", "payload", "config", "options", "settings", "metadata", "params", "body")
_INTERNAL_WORDS = ("internal", "admin", "debug", "private", "backend")
_DESTRUCTIVE_NAMES = ("delete", "remove", "drop", "destroy", "purge", "wipe")


# ---------- helpers ----------

def _input_schema(tool: dict) -> dict:
    schema = tool.get("inputSchema") if isinstance(tool, dict) else None
    return schema if isinstance(schema, dict) else {}


def _props(tool: dict) -> dict:
    """获取 inputSchema.properties；缺失返回 {}。"""
    props = _input_schema(tool).get("properties")
    return props if isinstance(props, dict) else {}


def _has_constraint(prop: dict) -> bool:
    """属性是否带 pattern / enum 约束。"""
    return bool(prop.get("pattern")) or bool(prop.get("enum"))


# ---------- high 规则 ----------

def _check_shell_param(tool: dict) -> Optional[str]:
    hits = [n for n in _props(tool) if any(k in n.lower() for k in _SHELL_KEYWORDS)]
    if hits:
        return f"参数 {hits} 含 shell 关键词，构成命令注入面"
    return None


def _check_path_param_unconstrained(tool: dict) -> Optional[str]:
    hits = []
    for name, prop in _props(tool).items():
        if not isinstance(prop, dict):
            continue
        if any(k in name.lower() for k in _PATH_KEYWORDS) and not _has_constraint(prop):
            hits.append(name)
    if hits:
        return f"路径参数 {hits} 无 pattern/enum 约束，构成任意路径访问面"
    return None


def _check_url_param_unconstrained(tool: dict) -> Optional[str]:
    hits = []
    for name, prop in _props(tool).items():
        if not isinstance(prop, dict):
            continue
        if any(k in name.lower() for k in _URL_KEYWORDS) and not _has_constraint(prop):
            hits.append(name)
    if hits:
        return f"URL 参数 {hits} 无 host 白名单（pattern/enum），构成 SSRF 面"
    return None


def _check_structured_string(tool: dict) -> Optional[str]:
    """type: string 但语义看起来是结构化数据 → 应该用 enum / 子 schema 限定。"""
    hits = []
    for name, prop in _props(tool).items():
        if not isinstance(prop, dict):
            continue
        if prop.get("type") != "string":
            continue
        if _has_constraint(prop):
            continue
        if any(k in name.lower() for k in _STRUCTURED_KEYWORDS):
            hits.append(name)
    if hits:
        return f"参数 {hits} type=string 但名字暗示结构化数据，建议用 enum / 子 schema 限定"
    return None


# ---------- medium 规则 ----------

def _check_additional_properties(tool: dict) -> Optional[str]:
    schema = _input_schema(tool)
    ap = schema.get("additionalProperties")
    # JSON Schema 默认 additionalProperties=true（允许）
    if ap is True or ap is None:
        return "additionalProperties 未限制为 false，允许 LLM 注入未声明字段"
    return None


def _check_too_many_required(tool: dict) -> Optional[str]:
    req = _input_schema(tool).get("required", [])
    if isinstance(req, list) and len(req) > 5:
        return f"required 字段过多（{len(req)}），设计模糊可能传递额外语义"
    return None


def _check_short_description(tool: dict) -> Optional[str]:
    desc = (tool.get("description") or "").strip()
    if len(desc) < 20:
        return f"description 过短（{len(desc)} 字符），LLM 难判断使用边界"
    return None


def _check_no_max_length(tool: dict) -> Optional[str]:
    hits = []
    for name, prop in _props(tool).items():
        if not isinstance(prop, dict):
            continue
        if prop.get("type") != "string":
            continue
        if "maxLength" not in prop:
            hits.append(name)
    if hits:
        return f"字符串参数 {hits} 无 maxLength，构成 DoS / token bombing 面"
    return None


# ---------- low 规则 ----------

def _check_internal_word(tool: dict) -> Optional[str]:
    desc = (tool.get("description") or "").lower()
    hits = [w for w in _INTERNAL_WORDS if w in desc]
    if hits:
        return f"description 含 {hits}，可能暴露内部语义"
    return None


def _check_destructive_name(tool: dict) -> Optional[str]:
    name = (tool.get("name") or "").lower()
    hits = [w for w in _DESTRUCTIVE_NAMES if w in name]
    if hits:
        return f"工具名含 {hits}（破坏性操作），建议加 HITL 确认"
    return None


# ---------- 规则表（数据驱动，新增规则只动这里）----------

RULES: list[Rule] = [
    Rule("shell_param",                "high",   _check_shell_param),
    Rule("path_unconstrained",         "high",   _check_path_param_unconstrained),
    Rule("url_unconstrained",          "high",   _check_url_param_unconstrained),
    Rule("structured_string",          "high",   _check_structured_string),
    Rule("additional_properties_open", "medium", _check_additional_properties),
    Rule("too_many_required",          "medium", _check_too_many_required),
    Rule("short_description",          "medium", _check_short_description),
    Rule("no_max_length",              "medium", _check_no_max_length),
    Rule("internal_word",              "low",    _check_internal_word),
    Rule("destructive_name",           "low",    _check_destructive_name),
]


# ---------- 主入口 ----------

def audit_schema(schema: Any) -> list[ToolReport]:
    """对 MCP tools/list schema 跑全部规则，返回每个 tool 的 ToolReport。"""
    tools = _extract_tools(schema)
    reports: list[ToolReport] = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        risks: list[tuple[str, str, str]] = []
        for rule in RULES:
            try:
                reason = rule.check(t)
            except Exception:
                reason = None  # 单条规则崩溃不影响其他
            if reason:
                risks.append((rule.rule_id, rule.severity, reason))
        reports.append(ToolReport(
            name=str(t.get("name", "<unnamed>")),
            overall=_calc_overall(risks),
            risks=risks,
        ))
    return reports


def _extract_tools(schema: Any) -> list:
    """从 schema 解出 tool 列表：支持 {"tools":[...]} 与裸数组两种形态。"""
    if isinstance(schema, list):
        return schema
    if isinstance(schema, dict):
        tools = schema.get("tools")
        if isinstance(tools, list):
            return tools
    return []


def _calc_overall(risks: list[tuple[str, str, str]]) -> str:
    """按命中规则的最高 severity 取整体等级。"""
    if not risks:
        return "safe"
    seen = {r[1] for r in risks}
    for sev in ("high", "medium", "low"):
        if sev in seen:
            return sev
    return "safe"


def format_report(reports: list[ToolReport]) -> str:
    """纯文本报告：每行一条 + 末尾汇总。便于测试 / 非交互场景比较。"""
    lines = []
    for r in reports:
        if r.risks:
            reasons = "; ".join(reason for _, _, reason in r.risks)
        else:
            reasons = "无命中规则"
        lines.append(f"[{r.overall}] {r.name}: {reasons}")
    counts = {"high": 0, "medium": 0, "low": 0, "safe": 0}
    for r in reports:
        counts[r.overall] = counts.get(r.overall, 0) + 1
    lines.append(
        f"high {counts['high']} | medium {counts['medium']} | "
        f"low {counts['low']} | safe {counts['safe']}"
    )
    return "\n".join(lines)


# ---------- 文件入口 + 渲染 ----------

def run_mcp_schema_audit(schema_path: Path, project_root: Path, console: Console) -> None:
    """读 schema 文件 → audit → 渲染 + 落盘。错误友好提示，不抛栈。"""
    if not schema_path.exists():
        console.print(f"[red]schema 文件不存在：{schema_path}[/red]")
        return
    if not schema_path.is_file():
        console.print(f"[red]不是文件：{schema_path}[/red]")
        return
    try:
        text = schema_path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        console.print(f"[red]读取失败：{e}[/red]")
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        console.print(f"[red]非法 JSON（行 {e.lineno}, 列 {e.colno}）：{e.msg}[/red]")
        return

    reports = audit_schema(data)
    if not reports:
        console.print(
            "[yellow]未在 schema 中发现 tool（期望 {\"tools\": [...]} 或裸数组）[/yellow]"
        )
        return

    _render(console, schema_path, reports)
    saved = _save(project_root, schema_path, reports)
    console.print(f"\n[dim]结果已保存：{saved}[/dim]")


def _render(console: Console, schema_path: Path, reports: list[ToolReport]) -> None:
    table = Table(title=f"MCP schema 审计：{schema_path.name}（{len(reports)} 个 tool）")
    table.add_column("严重", no_wrap=True)
    table.add_column("tool", no_wrap=True)
    table.add_column("理由")
    for r in reports:
        style = SEVERITY_STYLES.get(r.overall, "white")
        sev_cell = f"[{style}]{r.overall.upper()}[/{style}]"
        if not r.risks:
            table.add_row(sev_cell, r.name, "[green]无命中规则[/green]")
        else:
            reasons = "\n".join(
                f"[{SEVERITY_STYLES.get(s, 'white')}]{rid}[/{SEVERITY_STYLES.get(s, 'white')}]: {reason}"
                for rid, s, reason in r.risks
            )
            table.add_row(sev_cell, r.name, reasons)
    console.print(table)

    counts = {"high": 0, "medium": 0, "low": 0, "safe": 0}
    for r in reports:
        counts[r.overall] = counts.get(r.overall, 0) + 1
    console.print(
        f"\n[bold]汇总[/bold]: "
        f"[red]high {counts['high']}[/red] | "
        f"[yellow]medium {counts['medium']}[/yellow] | "
        f"[blue]low {counts['low']}[/blue] | "
        f"[green]safe {counts['safe']}[/green]"
    )


def _save(project_root: Path, schema_path: Path, reports: list[ToolReport]) -> Path:
    payload = {
        "schema_file": str(schema_path),
        "tools": [
            {
                "name": r.name,
                "overall": r.overall,
                "risks": [
                    {"rule_id": rid, "severity": s, "reason": reason}
                    for rid, s, reason in r.risks
                ],
            }
            for r in reports
        ],
    }
    return save_audit(project_root, "mcp_schema", schema_path.stem, payload)

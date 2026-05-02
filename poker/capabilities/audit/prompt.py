"""审计 system prompt：扫文本文件 + AST `SystemMessage` / `ChatPromptTemplate`。

启发式判断 prompt injection 抗性：暴露架构 / 边界不清 / 无脑跟随 user / 反向提示等。
LLM 可选用于深度评估 + 风险评分。
"""
import ast
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
class PromptInfo:
    source: str  # 'file' | 'code'
    file: str
    line: int
    role: str  # 'system' / 'user' / 'assistant' / ''
    content: str


@dataclass
class PromptAuditResult:
    info: PromptInfo
    risks: list[Risk] = field(default_factory=list)
    overall_severity: str = "info"
    llm_summary: str = ""


# ---------- 识别常量 ----------

_PROMPT_TEXT_SUFFIXES = {".md", ".txt", ".prompt", ".system", ".tmpl", ".j2"}
_PROMPT_KEYWORDS = (
    "you are", "system prompt", "你是", "your role", "assistant",
    "do not reveal", "ignore previous", "follow instructions",
)
_MESSAGE_CLASSES = {
    "SystemMessage", "SystemMessagePromptTemplate",
    "HumanMessage", "AIMessage",
}

# 危险措辞 → (regex, check, severity, evidence, recommendation)
_RISK_PATTERNS: tuple[tuple[re.Pattern, str, str, str, str], ...] = (
    (re.compile(r"always follow whatever .*user", re.I),
     "blind_follow_user", "high",
     "system prompt 中要求始终顺从 user，等于显式邀请 jailbreak",
     "明确分级：'当 user 输入与 system 指令冲突时，遵守 system'"),
    (re.compile(r"do not reveal|never disclose|don'?t tell", re.I),
     "secret_admonition", "medium",
     "出现 'do not reveal' 类反向提示，研究表明反而降低注入抗性",
     "用正向表述说明可分享内容；不要列举 'don't' 清单"),
    (re.compile(r"(tool|function|api)s?\s*[:：]\s*\[", re.I),
     "lists_tools_in_prompt", "medium",
     "system prompt 中显式列出工具 / API，被注入后会成攻击地图",
     "工具描述放系统侧 metadata，不进 prompt；最小化披露",),
    (re.compile(r"\b(internal|backend|database|架构)\b", re.I),
     "leaks_architecture", "medium",
     "system prompt 提及内部架构 / 数据库 / 后端字眼",
     "去除内部实现细节；prompt 应只描述行为而非实现"),
    (re.compile(r"\b(sudo|root|admin|bypass)\b", re.I),
     "privileged_mention", "low",
     "system prompt 含 sudo / root / admin / bypass 字样",
     "避免提示模型可越权；用最小权限原则描述任务"),
)
_USER_BOUNDARY_RE = re.compile(
    r"<user[ _]?input>|<\|user\|>|\[user\]|```user|```input", re.I,
)


# ---------- 静态识别 ----------

def find_prompts(project_root: Path) -> list[PromptInfo]:
    """扫文本 prompt 文件 + AST SystemMessage / ChatPromptTemplate。"""
    found: list[PromptInfo] = []

    for f in iter_text_files(project_root):
        if f.suffix.lower() in _PROMPT_TEXT_SUFFIXES:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if _looks_like_prompt(text, f):
                found.append(PromptInfo(
                    source="file", file=_rel(f, project_root),
                    line=1, role="system", content=text,
                ))
        if f.suffix == ".py":
            found.extend(_extract_python_prompts(f, project_root))

    return found


def _looks_like_prompt(text: str, file: Path) -> bool:
    """文件名含 prompt / 内容前 1500 字含 prompt keyword 即视为候选。"""
    if any("prompt" in p.lower() for p in file.parts):
        return True
    head = text[:1500].lower()
    return any(k in head for k in _PROMPT_KEYWORDS)


def _extract_python_prompts(file_path: Path, project_root: Path) -> list[PromptInfo]:
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(content)
    except (SyntaxError, OSError):
        return []
    rel = _rel(file_path, project_root)
    out: list[PromptInfo] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            out.extend(_extract_from_call(node, rel))
    return out


def _extract_from_call(call: ast.Call, rel_file: str) -> list[PromptInfo]:
    """识别 SystemMessage(content=...) / ChatPromptTemplate.from_messages([...])。"""
    out: list[PromptInfo] = []
    func_name = _name_of(call.func)
    short_name = func_name.split(".")[-1]

    if short_name in _MESSAGE_CLASSES:
        text = _string_arg(call, "content")
        if text:
            role = (
                "system" if "System" in short_name
                else "user" if "Human" in short_name
                else "assistant"
            )
            out.append(PromptInfo(
                source="code", file=rel_file, line=call.lineno,
                role=role, content=text,
            ))
        return out

    if short_name == "from_messages":
        if call.args and isinstance(call.args[0], ast.List):
            for elt in call.args[0].elts:
                pi = _extract_from_messages_tuple(elt, rel_file, call.lineno)
                if pi:
                    out.append(pi)
    return out


def _extract_from_messages_tuple(elt: ast.AST, rel_file: str, lineno: int) -> PromptInfo | None:
    """from_messages 里的一项：('system', '...') 或 SystemMessage(...)。"""
    if isinstance(elt, ast.Tuple) and len(elt.elts) == 2:
        role_node, content_node = elt.elts
        if (isinstance(role_node, ast.Constant) and isinstance(role_node.value, str)
                and isinstance(content_node, ast.Constant) and isinstance(content_node.value, str)):
            return PromptInfo(
                source="code", file=rel_file, line=lineno,
                role=role_node.value, content=content_node.value,
            )
    if isinstance(elt, ast.Call):
        results = _extract_from_call(elt, rel_file)
        return results[0] if results else None
    return None


def _string_arg(call: ast.Call, kw_name: str) -> str:
    for kw in call.keywords:
        if kw.arg == kw_name and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    if call.args and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
        return call.args[0].value
    return ""


def _name_of(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _name_of(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _rel(file: Path, root: Path) -> str:
    try:
        return file.relative_to(root).as_posix()
    except ValueError:
        return str(file)


# ---------- 审计 ----------

def audit_prompt(info: PromptInfo, llm: Any = None) -> PromptAuditResult:
    risks: list[Risk] = []
    text = info.content

    for regex, check, severity, evidence, recommendation in _RISK_PATTERNS:
        if regex.search(text):
            risks.append(Risk(
                check=check, severity=severity, evidence=evidence, recommendation=recommendation,
            ))

    if len(text) > 200 and not _USER_BOUNDARY_RE.search(text):
        risks.append(Risk(
            check="no_user_boundary", severity="low",
            evidence="prompt 较长但未见 <user_input> / [user] 等边界标记",
            recommendation="用清晰边界包裹 user 内容（XML / Markdown fence），便于模型区分指令与数据",
        ))

    if len(text) < 30:
        risks.append(Risk(
            check="too_short", severity="low",
            evidence=f"prompt 仅 {len(text)} 字符，约束信息不足",
            recommendation="补充角色定位、任务边界、输出格式约束",
        ))

    overall = overall_severity(risks)
    llm_summary = ""
    if llm is not None and text:
        try:
            llm_summary = _llm_assess(info, llm)
        except Exception as e:
            llm_summary = f"（LLM 评估失败：{e}）"

    return PromptAuditResult(
        info=info, risks=risks,
        overall_severity=overall, llm_summary=llm_summary,
    )


def _llm_assess(info: PromptInfo, llm: Any) -> str:
    from langchain_core.messages import HumanMessage, SystemMessage
    sys_prompt = (
        "你是 prompt 安全审计员。评估给定 system prompt 的 prompt injection 抗性："
        "是否清晰区分指令与数据、是否泄露敏感信息、是否易被 user 输入覆盖。"
        "给出 3-5 条具体结论 + 风险评分（0-10），每条一行。"
    )
    user_msg = (
        f"role: {info.role}\n"
        f"位置: {info.file}:{info.line}\n"
        f"内容（前 2000 字符）:\n{info.content[:2000]}"
    )
    response = llm.invoke([SystemMessage(content=sys_prompt), HumanMessage(content=user_msg)])
    content = response.content if hasattr(response, "content") else str(response)
    return content if isinstance(content, str) else str(content)


# ---------- 交互式入口 ----------

def interactive_audit_prompt(project_root: Path, llm: Any, console: Console) -> None:
    prompts = find_prompts(project_root)
    if not prompts:
        console.print("[yellow]未发现 system prompt（文本文件 / SystemMessage / ChatPromptTemplate）[/yellow]")
        return

    table = Table(title=f"发现 {len(prompts)} 份 prompt")
    table.add_column("#", style="bold")
    table.add_column("来源")
    table.add_column("位置")
    table.add_column("role")
    table.add_column("片段")
    for i, p in enumerate(prompts, 1):
        snippet = p.content[:50].replace("\n", " ")
        table.add_row(str(i), p.source, f"{p.file}:{p.line}", p.role, snippet)
    console.print(table)

    selected = prompt_selection(
        prompts, label=lambda p: p.file, console=console, kind="prompt",
    )
    if selected is None:
        return

    for p in selected:
        console.print(f"\n[bold]审计 {p.file}:{p.line}[/bold]  [dim]role={p.role}[/dim]")
        result = audit_prompt(p, llm)
        render_risks_block(console, result.risks, result.overall_severity, result.llm_summary)
        target = f"{p.file.replace('/', '_')}_{p.line}"
        path = save_audit(project_root, "prompt", target, _result_to_dict(result))
        console.print(f"[dim]结果已保存：{path}[/dim]")


def _result_to_dict(result: PromptAuditResult) -> dict:
    return {
        "info": asdict(result.info),
        "risks": [asdict(r) for r in result.risks],
        "overall_severity": result.overall_severity,
        "llm_summary": result.llm_summary,
    }

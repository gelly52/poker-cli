"""capabilities/triage：协助批量 triage 当前 last_scan 的 finding。

主入口 `interactive_triage`：
1. 加载 last_scan + 已 triage 集合
2. 一次性向 LLM 请求 batch 建议（accepted/ignored/fixed + 一句话理由）
3. 对每条未 triage 的 finding 用 ui.menu.select_one 让用户决定，菜单 title 直接展示 LLM 建议
4. 用户选完调 state.set_triage 落盘；select_one 返回 None（Esc/Ctrl+C）即中断，已选条目保留

LLM 失败 / 解析失败 / 项目类型识别失败均退化为"无建议"，不影响人工 triage 流程。
不重写 stream_agent_long —— suggest 是单轮 batch 调用，无需多轮反思。
"""
import json
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from rich.console import Console

from poker.capabilities.explain import compute_finding_id
from poker.state import load_last_findings, load_triages, set_triage
from poker.ui.menu import select_one

_VALID_ACTIONS = ("accepted", "ignored", "fixed")
_REASON_DISPLAY_LIMIT = 60


# ---------- 项目类型 hint ----------

def detect_project_type(project_root: Path) -> list[str]:
    """轻量识别项目特征作为 LLM 优先级 hint；失败返回空列表，不抛栈。"""
    hints: list[str] = []
    try:
        if (project_root / ".git").exists():
            hints.append("git-repo")
        if (project_root / "pyproject.toml").exists():
            hints.append("python-project")
        if (project_root / "package.json").exists():
            hints.append("nodejs-project")
        if (project_root / "go.mod").exists():
            hints.append("go-project")
        if (project_root / "Cargo.toml").exists():
            hints.append("rust-project")
        if (project_root / "Dockerfile").exists():
            hints.append("dockerized")
        if (project_root / "tests").exists() or (project_root / "test").exists():
            hints.append("has-tests")
    except OSError:
        return hints
    return hints


# ---------- LLM 建议 ----------

def _build_suggest_prompt(findings: list[dict], project_hints: list[str]) -> str:
    items = []
    for f in findings:
        items.append({
            "id": compute_finding_id(f),
            "severity": f.get("severity", ""),
            "rule_id": f.get("rule_id", ""),
            "title": f.get("title", ""),
            "path": f.get("path", ""),
            "line": f.get("line", ""),
            "evidence": str(f.get("evidence", ""))[:200],
        })
    return (
        "你是安全审计助手。根据下面的 findings，给每条推荐 triage：accepted / ignored / fixed。\n\n"
        "判定参考：\n"
        "- accepted：真问题，应进入修复 backlog\n"
        "- ignored：误报、测试 fixture、文档 / 示例代码、刻意为之（如 placeholder 密钥）\n"
        "- fixed：证据显示已被修复（请保守，不确定就不要给 fixed）\n\n"
        f"项目特征 hint：{project_hints}\n\n"
        f"finding 列表（JSON）：\n{json.dumps(items, ensure_ascii=False, indent=2)}\n\n"
        "**严格只输出 JSON**（不要 markdown 代码块，不要前后说明），结构：\n"
        '{"<finding-id>": {"action": "accepted|ignored|fixed", "reason": "一句话理由"}}\n'
    )


def _parse_suggestions(text: str) -> dict[str, dict]:
    """从 LLM 输出抽取 JSON 建议。容错 markdown 代码块包装、前后噪声。"""
    if not text:
        return {}
    body = text

    # 先尝试 ```json ... ``` 包装
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        body = fenced.group(1)
    else:
        # 退而求其次：取首个 { 到末尾 } 之间内容
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            body = text[first : last + 1]

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, dict] = {}
    for fid, val in data.items():
        if not isinstance(val, dict):
            continue
        action = str(val.get("action", "")).strip().lower()
        if action not in _VALID_ACTIONS:
            continue
        reason = str(val.get("reason", "")).strip()
        out[str(fid)] = {"action": action, "reason": reason}
    return out


def suggest_triage(
    findings: list[dict],
    project_root: Path,
    llm: Any,
) -> dict[str, dict]:
    """单轮调 LLM 拿一份 batch triage 建议。失败返回空 dict（调用方退化）。"""
    if not findings or llm is None:
        return {}
    hints = detect_project_type(project_root)
    prompt = _build_suggest_prompt(findings, hints)
    try:
        response = llm.invoke([HumanMessage(content=prompt)])
    except Exception:
        return {}
    content = response.content if hasattr(response, "content") else response
    text = content if isinstance(content, str) else str(content)
    return _parse_suggestions(text)


# ---------- 交互主流程 ----------

def interactive_triage(
    project_root: Path,
    llm: Any,
    console: Console,
) -> None:
    """主入口：列未 triage finding，逐条让用户配合 LLM 建议做决定，自动落盘。

    LLM 不可用 / 解析失败 → 退化为无建议纯人工 triage（功能不阻塞）。
    select_one 返回 None（Esc / Ctrl+C） → 立即结束，已选条目已落盘。
    """
    findings = load_last_findings(project_root)
    if not findings:
        console.print(
            "[yellow]还没跑过扫描；先运行 [cyan]/scan <path>[/cyan] 试试[/yellow]"
        )
        return

    triaged = load_triages(project_root)
    pending = [f for f in findings if compute_finding_id(f) not in triaged]
    if not pending:
        console.print("[green]所有 finding 都已 triage 过；无待处理项[/green]")
        return

    console.print(
        f"[dim]待 triage: {len(pending)} 条 / 总 {len(findings)} 条[/dim]"
    )

    suggestions: dict[str, dict] = {}
    if llm is not None:
        suggestions = suggest_triage(pending, project_root, llm)
        if not suggestions:
            console.print("[yellow]LLM 没给出可用建议；继续无建议 triage[/yellow]")
    else:
        console.print("[yellow]未配置 LLM；纯人工 triage[/yellow]")

    decided = 0
    for f in pending:
        fid = compute_finding_id(f)
        title = _build_menu_title(f, fid, suggestions.get(fid))
        items: list[tuple[str, str]] = [
            ("accepted", "✅  accept   接受为真问题，列入修复 backlog"),
            ("ignored",  "🙈  ignore   误报 / 测试 fixture / 文档示例 / 刻意为之"),
            ("fixed",    "🛠   fixed    已经修复"),
            ("skip",     "⏭   skip     跳过本次不决定"),
        ]
        chosen = select_one(
            title=title,
            items=items,
            hint="↑/↓ 选择  Enter 确认  Esc 中断",
        )
        if chosen is None:
            console.print(
                f"\n[yellow][已中断]  本次 triage {decided} 条已落盘"
                f"（剩余 {len(pending) - decided - 0} 条未处理）[/yellow]"
            )
            return
        if chosen == "skip":
            continue
        try:
            set_triage(project_root, fid, str(chosen))
            decided += 1
        except ValueError as e:
            console.print(f"[red]triage 落盘失败: {e}[/red]")

    console.print(
        f"\n[green]完成。本次 triage {decided} 条 / "
        f"待处理 {len(pending)} 条[/green]"
    )


def _build_menu_title(
    finding: dict,
    finding_uid: str,
    suggestion: dict | None,
) -> str:
    """组装 select_one 顶部 title：finding 摘要 + LLM 建议（若有）。"""
    sev = finding.get("severity", "")
    head = (
        f"[{sev}] {finding.get('title', '')}  "
        f"({finding_uid})  ·  "
        f"{finding.get('path', '')}:{finding.get('line', '')}"
    )
    if not suggestion or not suggestion.get("action"):
        return head
    reason = suggestion.get("reason", "").strip()
    if len(reason) > _REASON_DISPLAY_LIMIT:
        reason = reason[:_REASON_DISPLAY_LIMIT] + "…"
    return f"{head}\n  ↳ LLM 建议：{suggestion['action']}（{reason}）" if reason else (
        f"{head}\n  ↳ LLM 建议：{suggestion['action']}"
    )

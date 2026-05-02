"""红队模拟：对 prompt 文件分析 → 生成针对性攻击载荷。

MVP 只生成不执行；执行 endpoint 是 Phase 2 的事。
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from poker.capabilities.redteam.payloads import PAYLOAD_LIBRARY


@dataclass
class PayloadResult:
    category: str
    payload: str
    intent: str
    references: list[str] = field(default_factory=list)
    relevance: str = ""  # 为什么这条 payload 对该 prompt 有针对性


def analyze_prompt(prompt_text: str) -> dict[str, bool]:
    """启发式提取 prompt 关键特征，用于挑选相关 payload。

    返回 dict 含：
      - mentions_role: 是否定义角色 / 人设
      - mentions_tools: 是否提及工具 / function calling
      - mentions_secrets: 是否提及敏感数据 / 不能泄露的内容
      - mentions_constraints: 是否有明确约束（不要做某事）
      - mentions_user_input: 是否处理用户输入 / 外部内容
    """
    text = prompt_text.lower()
    return {
        "mentions_role": any(k in text for k in ("you are", "你是", "角色", "role:", "act as", "assistant")),
        "mentions_tools": any(k in text for k in ("tool", "function", "工具", "调用", "call ")),
        "mentions_secrets": any(k in text for k in ("secret", "private", "internal", "do not reveal", "不要泄露", "保密", "confidential")),
        "mentions_constraints": any(k in text for k in ("do not", "must not", "never", "不要", "禁止", "cannot")),
        "mentions_user_input": any(k in text for k in ("user input", "user content", "document", "rag", "context", "用户输入", "retriev")),
    }


def generate_payloads(prompt_text: str) -> list[PayloadResult]:
    """根据 analyze_prompt 的结果挑选相关类别 payload，并附 relevance 说明。"""
    features = analyze_prompt(prompt_text)
    results: list[PayloadResult] = []

    # 几乎所有 prompt 都适用
    _add_category(results, "jailbreak", "适用所有 LLM 应用：测试基础越狱抵抗力", limit=4)

    if features["mentions_role"]:
        _add_category(results, "role_override", "prompt 定义了明确角色，验证角色保持力", limit=3)

    if features["mentions_user_input"]:
        _add_category(results, "context_smuggling", "prompt 处理外部内容（文档 / 用户输入），存在间接注入面", limit=3)

    if features["mentions_secrets"] or features["mentions_constraints"]:
        _add_category(results, "data_exfil", "prompt 提到敏感约束，验证泄露抵抗力", limit=3)

    if features["mentions_tools"] or features["mentions_constraints"]:
        _add_category(results, "instruction_hierarchy", "prompt 有指令规则，验证优先级稳定性", limit=2)

    return results


def _add_category(results: list[PayloadResult], category: str, relevance: str, limit: int) -> None:
    for p in PAYLOAD_LIBRARY.get(category, [])[:limit]:
        results.append(PayloadResult(
            category=category,
            payload=p.payload,
            intent=p.intent,
            references=list(p.references) if p.references else [],
            relevance=relevance,
        ))


def run_redteam(prompt_file: Path, project_root: Path, console: Any) -> None:
    """读 prompt 文件 → 分析 → 生成 payload → 输出表格。路径越界 / 文件错误时打印并返回，不抛。"""
    target = prompt_file.expanduser()
    abs_target = target.resolve() if target.is_absolute() else (project_root / target).resolve()
    try:
        abs_target.relative_to(project_root)
    except ValueError:
        console.print(f"[red]路径越界：{prompt_file} 不在 project root ({project_root}) 内[/red]")
        return
    if not abs_target.exists():
        console.print(f"[red]文件不存在：{abs_target}[/red]")
        return
    if not abs_target.is_file():
        console.print(f"[red]不是文件：{abs_target}[/red]")
        return

    try:
        prompt_text = abs_target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        console.print(f"[red]读取失败：{e}[/red]")
        return

    results = generate_payloads(prompt_text)
    if not results:
        console.print("[yellow]未生成 payload[/yellow]")
        return

    _render_payloads(abs_target.relative_to(project_root), results, console)


def _render_payloads(rel_path: Path, results: list[PayloadResult], console: Any) -> None:
    """按 category 分组打印 payload 列表。"""
    console.print(f"\n[bold]对 {rel_path} 生成了 {len(results)} 条攻击载荷[/bold]")
    console.print("[dim]MVP 只生成不执行；用户负责评估并自行对接 endpoint[/dim]")

    by_cat: dict[str, list[PayloadResult]] = {}
    for r in results:
        by_cat.setdefault(r.category, []).append(r)

    for cat, items in by_cat.items():
        console.print(f"\n[bold yellow]== {cat.upper()} ({len(items)} 条) ==[/bold yellow]")
        if items[0].relevance:
            console.print(f"[dim]适用原因: {items[0].relevance}[/dim]")
        for i, r in enumerate(items, 1):
            console.print(f"\n  [bold]{i}.[/bold] {r.intent}")
            console.print(f"     payload: [cyan]{r.payload}[/cyan]")
            if r.references:
                console.print(f"     refs: {', '.join(r.references)}")

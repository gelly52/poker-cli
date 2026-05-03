"""capabilities/multi_agent：planner / investigators / critic / synthesizer 协作。

主入口 `run_multi_agent_investigation`：
1. **Planner**：单轮 LLM 拆 ≤5 个独立子任务
2. **Investigators**：`ThreadPoolExecutor` 并发跑（每个独立 stream_agent_long + thread-local
   budget=15），任意失败不影响其他；最终报告标 `[Investigator <id>: 失败 - <err>]`
3. **Critic**：单轮反馈，对每个 Investigator 报告提 2-3 个关键问题（**只一轮，不无限往复**）
4. **Synthesizer**：单轮合并所有产出 + critique 输出最终 markdown

落盘 `.poker/state/<hash>/multi_agent_runs/<topic>_<ts>.md`。
任何阶段 KeyboardInterrupt / 异常都会按已完成阶段拼装报告落盘。

**不引入 langgraph / autogen** —— 仅用 stdlib threading.ThreadPoolExecutor。
"""
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from rich.console import Console

from poker.capabilities.multi_agent.roles import (
    run_critic,
    run_investigator,
    run_planner,
    run_synthesizer,
)
from poker.state import save_multi_agent_run

MAX_AGENTS = 5


def run_multi_agent_investigation(
    topic: str,
    project_root: Path,
    llm: Any,
    console: Console,
) -> Path | None:
    """主入口：多 Agent 协作调查 → 落盘。"""
    topic = (topic or "").strip()
    if not topic:
        console.print(
            "[yellow]/investigate --multi 需要主题；例：/investigate \"全面安全分析\" --multi[/yellow]"
        )
        return None
    if llm is None:
        console.print("[red]未配置 LLM；多 Agent 协作需要 API key[/red]")
        return None

    sections: dict[str, str] = {}
    interrupted = False
    error: Exception | None = None

    try:
        # ---------- Phase 1: Planner ----------
        console.print("[cyan][Planner][/cyan] 拆分子任务 ...")
        sub_tasks = run_planner(topic, llm, max_subtasks=MAX_AGENTS)
        if len(sub_tasks) > MAX_AGENTS:
            console.print(
                f"[yellow]子任务 {len(sub_tasks)} 条超上限，截到 {MAX_AGENTS}[/yellow]"
            )
            sub_tasks = sub_tasks[:MAX_AGENTS]
        sections["plan"] = _render_plan_section(sub_tasks)
        console.print(
            f"[green]✓[/green] Planner 拆出 {len(sub_tasks)} 个子任务"
        )

        # ---------- Phase 2: Investigators 并发 ----------
        console.print(
            f"[cyan][Investigators][/cyan] 并发 {len(sub_tasks)} 个 ..."
        )
        reports = _run_investigators_concurrent(
            topic, sub_tasks, project_root, llm, console
        )
        sections["investigators"] = _render_investigators_section(sub_tasks, reports)

        # ---------- Phase 3: Critic ----------
        successful_reports = {
            sid: r["markdown"] for sid, r in reports.items() if not r["error"]
        }
        if successful_reports:
            console.print("[cyan][Critic][/cyan] 一轮反馈 ...")
            critique = run_critic(topic, successful_reports, llm)
            sections["critic"] = "## Critic 反馈\n\n" + (critique or "（空）")
            console.print("[green]✓[/green] Critic 完成")
        else:
            sections["critic"] = (
                "## Critic 反馈\n\n（所有 Investigator 失败，无可反馈内容）"
            )

        # ---------- Phase 4: Synthesizer ----------
        console.print("[cyan][Synthesizer][/cyan] 合并报告 ...")
        final = run_synthesizer(
            topic,
            successful_reports,
            sections.get("critic", ""),
            llm,
        )
        sections["final"] = final
        console.print("[green]✓[/green] Synthesizer 完成")

    except KeyboardInterrupt:
        interrupted = True
    except Exception as e:
        error = e

    report_md = _assemble_markdown(topic, sections)

    if interrupted:
        console.print("\n[yellow][已中断]，已生成部分将落盘[/yellow]")
    elif error is not None:
        console.print(
            f"\n[red]协作异常 ({type(error).__name__}: {error})；已生成部分将落盘[/red]"
        )

    if not report_md.strip():
        console.print("[yellow]未生成任何报告内容；不落盘[/yellow]")
        return None

    try:
        path = save_multi_agent_run(project_root, topic, report_md)
    except Exception as e:
        console.print(f"[red]报告落盘失败: {e}[/red]")
        return None

    console.print(f"[green]报告已落盘:[/green] [cyan]{path}[/cyan]")
    return path


# ---------- Phase 2 helpers ----------

def _run_investigators_concurrent(
    topic: str,
    sub_tasks: list[dict],
    project_root: Path,
    llm: Any,
    console: Console,
) -> dict[str, dict]:
    """并发跑所有 Investigator；返回 {sub_id: {markdown, error}}。

    Ctrl+C 时 cancel 未启动的 future，已启动的让其自然结束（线程不可强杀）；
    KeyboardInterrupt 继续向上传播给主 try。
    """
    reports: dict[str, dict] = {}

    def _worker(sub: dict) -> tuple[str, str, str | None]:
        md, err = run_investigator(topic, sub, project_root, llm)
        return sub["id"], md, err

    with ThreadPoolExecutor(
        max_workers=min(MAX_AGENTS, max(1, len(sub_tasks))),
        thread_name_prefix="poker-investigator",
    ) as executor:
        futures = {executor.submit(_worker, sub): sub for sub in sub_tasks}
        try:
            for f in as_completed(futures):
                sub = futures[f]
                try:
                    sub_id, md, err = f.result()
                except Exception as e:
                    sub_id = sub["id"]
                    md = ""
                    err = f"{type(e).__name__}: {e}"
                reports[sub_id] = {"markdown": md, "error": err}
                if err:
                    console.print(f"[red]✗ {sub_id} 失败: {err}[/red]")
                else:
                    console.print(f"[green]✓ {sub_id} 完成[/green]")
        except KeyboardInterrupt:
            for f in futures:
                if not f.done():
                    f.cancel()
            # 用占位标记未完成的子任务
            for f, sub in futures.items():
                sub_id = sub["id"]
                if sub_id not in reports:
                    reports[sub_id] = {"markdown": "", "error": "中断（未完成）"}
            raise
    return reports


def _render_plan_section(sub_tasks: list[dict]) -> str:
    lines = ["## 子任务规划", ""]
    for t in sub_tasks:
        lines.append(
            f"- **{t['id']}**: {t.get('goal', '')} "
            f"（范围：{t.get('scope', '') or '—'}）"
        )
    return "\n".join(lines)


def _render_investigators_section(
    sub_tasks: list[dict], reports: dict[str, dict]
) -> str:
    parts = ["## Investigator 产出", ""]
    for sub in sub_tasks:
        sid = sub["id"]
        r = reports.get(sid)
        header = f"### {sid}: {sub.get('goal', '')}"
        if r and not r["error"] and r["markdown"]:
            parts.append(header)
            parts.append("")
            parts.append(r["markdown"])
        else:
            err = r["error"] if r and r["error"] else "未完成"
            parts.append(header)
            parts.append("")
            parts.append(f"[Investigator {sid}: 失败 - {err}]")
        parts.append("")
    return "\n".join(parts)


def _assemble_markdown(topic: str, sections: dict[str, str]) -> str:
    """按已完成阶段拼装最终 markdown：有 final → 用 final + 附录；没 final → 拼前面阶段。"""
    if "final" in sections and sections["final"].strip():
        # 完整路径：final + 附录
        appendix_parts = []
        if "plan" in sections:
            appendix_parts.append(sections["plan"])
        if "investigators" in sections:
            appendix_parts.append(sections["investigators"])
        if "critic" in sections:
            appendix_parts.append(sections["critic"])
        appendix = "\n\n".join(p for p in appendix_parts if p.strip())
        if appendix:
            return (
                sections["final"].strip()
                + "\n\n---\n\n# 附录：原始过程数据\n\n"
                + appendix
            )
        return sections["final"]

    # 中断 / 早失败：拼装已有阶段做兜底报告
    head = f"# 多 Agent 调查（未完成）：{topic}\n"
    parts = [head]
    for key in ("plan", "investigators", "critic"):
        if key in sections and sections[key].strip():
            parts.append(sections[key])
    return "\n\n".join(parts)

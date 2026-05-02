"""/redteam 命令入口。

不带 --execute：仅生成 payload 列表（旧行为）。
带 --execute：对白名单 endpoint 实际发请求；强制二次确认 + 限速 + 上限 + 落盘。
"""
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

console = Console()


def register_redteam(app: typer.Typer) -> None:
    """将 redteam 命令注册到 Typer app。"""

    @app.command()
    def redteam(
        prompt_file: Path = typer.Argument(..., help="prompt 文件路径"),
        execute: bool = typer.Option(False, "--execute", help="对 endpoint 实际发请求（高危）"),
        endpoint: Optional[str] = typer.Option(None, "--endpoint", help="endpoint 白名单 name"),
    ) -> None:
        """对 prompt 文件生成攻击载荷；--execute 时对 endpoint 实际发请求。"""
        from poker.agent.tools import set_project_root

        project_root = Path.cwd().resolve()
        set_project_root(project_root)

        if execute:
            run_execute(prompt_file, project_root, endpoint, console)
        else:
            from poker.capabilities.redteam import run_redteam
            run_redteam(prompt_file, project_root, console)


def run_execute(
    prompt_file: Path,
    project_root: Path,
    endpoint_name: Optional[str],
    console: Console,
) -> None:
    """执行流：白名单 → API key → 截断 → 强确认 → audit → 限速发请求 → 判定 → 落盘 → 总结。"""
    from poker.capabilities.redteam import generate_payloads
    from poker.capabilities.redteam.endpoints import (
        env_var_name,
        load_endpoints,
        resolve_api_key,
    )
    from poker.capabilities.redteam.executor import (
        DEFAULT_MAX_COUNT,
        execute_payloads,
    )
    from poker.capabilities.redteam.judge import judge_response
    from poker.state import append_audit_log, save_redteam_run
    from poker.ui.confirm import confirm_phrase

    if not endpoint_name:
        console.print("[red]--execute 必须配 --endpoint <name>[/red]")
        return

    # 1. 解析 prompt 文件 + 路径校验
    target = prompt_file.expanduser()
    abs_target = target.resolve() if target.is_absolute() else (project_root / target).resolve()
    try:
        abs_target.relative_to(project_root)
    except ValueError:
        console.print(f"[red]路径越界：{prompt_file}[/red]")
        return
    if not abs_target.is_file():
        console.print(f"[red]prompt 文件不存在：{abs_target}[/red]")
        return
    try:
        prompt_text = abs_target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        console.print(f"[red]读取失败：{e}[/red]")
        return

    # 2. 查白名单（任意 URL 拒绝；只接受登记过的 name）
    endpoints = load_endpoints()
    if endpoint_name not in endpoints:
        console.print(f"[red]endpoint '{endpoint_name}' 不在白名单 ~/.poker/redteam_endpoints.toml[/red]")
        if endpoints:
            console.print(f"[dim]已注册：{', '.join(sorted(endpoints))}[/dim]")
        else:
            console.print("[dim]白名单为空，请先创建 ~/.poker/redteam_endpoints.toml[/dim]")
        return
    endpoint = endpoints[endpoint_name]

    # 3. API key 必须从环境变量
    api_key = resolve_api_key(endpoint_name)
    if not api_key:
        console.print(f"[red]未找到 API key（环境变量 {env_var_name(endpoint_name)}）[/red]")
        return

    # 4. 生成 payload + 上限截断
    payloads = generate_payloads(prompt_text)
    if not payloads:
        console.print("[yellow]未生成 payload，跳过执行[/yellow]")
        return
    truncated = len(payloads) > DEFAULT_MAX_COUNT
    if truncated:
        payloads = payloads[:DEFAULT_MAX_COUNT]

    # 5. 强二次确认（输入完整 phrase）
    rel = abs_target.relative_to(project_root)
    summary = (
        f"\n[bold yellow]⚠️  即将对 endpoint 发起 {len(payloads)} 次攻击请求（含外部网络副作用）[/bold yellow]\n"
        f"  endpoint:   [cyan]{endpoint.name}[/cyan]\n"
        f"  url:        {endpoint.url}\n"
        f"  model:      {endpoint.model or '<default>'}\n"
        f"  rate_limit: {endpoint.rate_limit} req/s\n"
        f"  prompt:     {rel}\n"
    )
    if truncated:
        summary += f"  [dim]（payload 上限 {DEFAULT_MAX_COUNT}，已截断）[/dim]\n"
    if not confirm_phrase("yes execute attacks", summary):
        return

    # 6. 审计日志（必发，便于事后追溯）
    append_audit_log(project_root, {
        "type": "redteam_execute",
        "endpoint": endpoint_name,
        "url": endpoint.url,
        "count": len(payloads),
        "prompt": str(rel),
    })

    # 7. 同步发请求 + 实时进度
    console.print(f"\n[bold]开始执行 {len(payloads)} 个 payload...[/bold]\n[dim](Ctrl+C 可随时中止)[/dim]")

    def _on_progress(i: int, total: int, result) -> None:
        if result.error:
            console.print(
                f"  [red]✗[/red] {i}/{total}  [{result.category}] "
                f"{result.payload[:50]!r}  [red]({result.error})[/red]"
            )
        else:
            console.print(
                f"  [green]·[/green] {i}/{total}  [{result.category}] "
                f"{result.payload[:50]!r}  [dim]{result.latency_ms:.0f}ms[/dim]"
            )

    results, interrupted = execute_payloads(
        payloads, endpoint, api_key, prompt_text,
        on_progress=_on_progress,
    )

    if interrupted:
        console.print("\n[yellow]⏸  已中止；已发出的部分仍会落盘[/yellow]")

    # 8. 判定
    judged = []
    summary_counts = {"safe": 0, "partial": 0, "bypass": 0}
    for r in results:
        verdict = judge_response(r.payload, r.response_text, prompt_text)
        summary_counts[verdict.label] = summary_counts.get(verdict.label, 0) + 1
        judged.append({
            "payload": r.payload,
            "category": r.category,
            "intent": r.intent,
            "status_code": r.status_code,
            "response_text": r.response_text,
            "latency_ms": r.latency_ms,
            "error": r.error,
            "ts": r.ts,
            "verdict": verdict.label,
            "reason": verdict.reason,
            "score": verdict.score,
        })

    # 9. 落盘
    record = {
        "endpoint": endpoint_name,
        "url": endpoint.url,
        "model": endpoint.model,
        "interrupted": interrupted,
        "total": len(judged),
        "summary": summary_counts,
        "results": judged,
    }
    saved_path = save_redteam_run(project_root, abs_target.stem, record)
    console.print(f"\n[dim]结果已保存：{saved_path}[/dim]")

    # 10. 总结
    console.print(
        f"\n[bold]总结[/bold]: "
        f"[green]safe {summary_counts.get('safe', 0)}[/green] · "
        f"[yellow]partial {summary_counts.get('partial', 0)}[/yellow] · "
        f"[red]bypass {summary_counts.get('bypass', 0)}[/red]"
    )

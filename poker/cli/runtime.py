"""`poker runtime` 子命令：渲染 poker_observer 写的 runtime jsonl。

读 ~/.poker/runtime/<project_hash>/*.jsonl，按时间倒序展示最近事件。
不依赖 poker_observer（避免循环），自己计算 project_hash。
"""
import hashlib
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

console = Console()


def _project_hash(project: str) -> str:
    return hashlib.sha256(project.encode("utf-8")).hexdigest()[:12]


def _runtime_root() -> Path:
    return Path.home() / ".poker" / "runtime"


def _runtime_dir(project: str) -> Path:
    return _runtime_root() / _project_hash(project)


def register_runtime(app: typer.Typer) -> None:
    """注册 `poker runtime` sub-app。"""
    runtime_app = typer.Typer(help="LLM runtime 观测：show / list")

    @runtime_app.command("show")
    def show(
        project: str = typer.Option(..., "--project", "-p", help="项目 name（与 callback 里同名）"),
        limit: int = typer.Option(50, "--limit", "-n", min=1, help="最多渲染事件条数"),
        only_detections: bool = typer.Option(
            False, "--only-detections", help="只显示有检测命中的事件"
        ),
    ) -> None:
        """渲染最近 runtime 事件。"""
        d = _runtime_dir(project)
        if not d.exists():
            console.print(f"[yellow]还没有 [cyan]{project}[/cyan] 的 runtime 记录:[/yellow] {d}")
            raise typer.Exit(code=0)

        events = _load_events(d, limit=limit, only_detections=only_detections)
        if not events:
            tag = "（含检测命中）" if only_detections else ""
            console.print(f"[yellow]无事件{tag}[/yellow]")
            return

        _render_events(events, project=project, runtime_dir=d)

    @runtime_app.command("list")
    def list_projects() -> None:
        """列出有 runtime 记录的所有 project_hash。"""
        root = _runtime_root()
        if not root.exists():
            console.print("[yellow]还没有任何 runtime 记录[/yellow]")
            return
        rows = sorted(
            (p for p in root.iterdir() if p.is_dir()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not rows:
            console.print("[yellow]还没有任何 runtime 记录[/yellow]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("Project hash", style="cyan", no_wrap=True)
        table.add_column("Files")
        table.add_column("Last update", style="dim")
        for d in rows:
            files = list(d.glob("*.jsonl"))
            ts = max((f.stat().st_mtime for f in files), default=d.stat().st_mtime)
            from datetime import datetime
            table.add_row(
                d.name,
                str(len(files)),
                datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S"),
            )
        console.print(table)

    app.add_typer(runtime_app, name="runtime")


def _load_events(
    runtime_dir: Path,
    *,
    limit: int = 50,
    only_detections: bool = False,
) -> list[dict]:
    """读 runtime_dir 下所有 jsonl，按 mtime 倒序汇总最近 limit 条事件。"""
    if not runtime_dir.exists():
        return []
    files = sorted(
        runtime_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[dict] = []
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if only_detections and not (ev.get("detections") or []):
                continue
            out.append(ev)
            if len(out) >= limit:
                return out
    return out


def _render_events(events: list[dict], *, project: str, runtime_dir: Path) -> None:
    table = Table(
        show_header=True,
        header_style="bold",
        title=f"Runtime · {project} · {runtime_dir}",
        title_style="bold gold3",
    )
    table.add_column("Time", style="dim", no_wrap=True)
    table.add_column("Kind", style="cyan", no_wrap=True)
    table.add_column("Run", style="dim", no_wrap=True)
    table.add_column("Detections", no_wrap=True)
    table.add_column("Summary")

    for ev in events:
        ts = (ev.get("ts") or "")[:19].replace("T", " ")
        kind = ev.get("kind", "?")
        run_id = (ev.get("run_id") or "")[:8] or "-"
        dets = ev.get("detections") or []
        det_str = _format_detections(dets)
        summary = _summarize_payload(kind, ev.get("payload") or {})
        table.add_row(ts, kind, run_id, det_str, summary)

    console.print(table)


def _format_detections(dets: list[dict]) -> str:
    if not dets:
        return "[dim]-[/dim]"
    parts: list[str] = []
    for d in dets[:3]:
        sev = d.get("severity") or ""
        rule = d.get("rule_id") or "?"
        if sev in ("critical", "high"):
            parts.append(f"[red]{rule}[/red]")
        elif sev == "medium":
            parts.append(f"[yellow]{rule}[/yellow]")
        else:
            parts.append(rule)
    extra = "" if len(dets) <= 3 else f" +{len(dets) - 3}"
    return ", ".join(parts) + extra


def _summarize_payload(kind: str, payload: dict) -> str:
    if kind == "llm_start":
        prompts = payload.get("prompts") or []
        first = prompts[0] if prompts else ""
        return _short(str(first), 80)
    if kind == "chat_model_start":
        msgs = payload.get("messages") or []
        first = msgs[0] if msgs else ""
        return _short(str(first), 80)
    if kind == "llm_end":
        usage = payload.get("usage") or {}
        return f"resp: {_short(str(payload.get('response', '')), 60)}  usage={usage}"
    if kind == "tool_start":
        tool = payload.get("tool", "?")
        return f"{tool}({_short(str(payload.get('input', '')), 60)})"
    if kind == "tool_end":
        return f"out: {_short(str(payload.get('output', '')), 80)}"
    if kind in ("llm_error", "tool_error"):
        return f"[red]{_short(str(payload.get('error', '')), 80)}[/red]"
    return _short(str(payload), 80)


def _short(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"

"""交互式 REPL：/cmd / !cmd / chat 三类输入分发。"""
import shlex
import subprocess
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from poker.agent.llm import create_chat_model
from poker.agent.runtime import restore_session, stream_agent
from poker.agent.tools import set_project_root
from poker.capabilities.scan.engine import scan_path
from poker.capabilities.scan.report import (
    filter_by_mode,
    print_summary,
    print_table_grouped,
)
from poker.config import load_config
from poker.config.models import PROVIDERS
from poker.shell import run_shell
from poker.state import (
    append_audit_log,
    append_chat,
    get_state_dir,
    load_chat_sessions,
    save_findings,
)
from poker.ui.help import render_help
from poker.ui.menu import select_one
from poker.ui.panels import accent_panel
from poker.ui.prompt import create_session, read_line
from poker.ui.splash import render_splash

console = Console()


class _ReplState:
    """REPL 会话状态：tracked cwd + session id。"""

    def __init__(self) -> None:
        self.cwd: Path = Path.cwd().resolve()
        self.session_id: str = "repl"


def start_repl() -> None:
    config = load_config()
    llm = create_chat_model(config.provider) if config.has_api_key else None
    state = _ReplState()
    set_project_root(state.cwd)

    render_splash(console, state.cwd)
    console.print()

    session = create_session(get_state_dir(state.cwd) / "repl_history")

    while True:
        try:
            user_input = read_line(session).strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n再见！")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            append_audit_log(state.cwd, {"type": "command", "input": user_input})
            if not _handle_command(user_input, config, state, llm):
                break
            continue

        if user_input.startswith("!"):
            append_audit_log(state.cwd, {"type": "shell", "input": user_input})
            _handle_shell(user_input[1:], state)
            continue

        if not llm:
            console.print("[red]未配置 API key，请先运行 poker init 或设置环境变量[/red]")
            continue

        append_chat(state.cwd, "user", user_input)
        try:
            text = Text()
            with Live(Panel(text, title="Poker", border_style="green"), console=console, refresh_per_second=8) as live:
                for token, _ in stream_agent(llm, user_input, state.session_id):
                    text.append(token)
                    live.update(Panel(text, title="Poker", border_style="green"))
            append_chat(state.cwd, "assistant", str(text))
        except Exception as e:
            console.print(f"[red]Agent 错误: {e}[/red]")


def _handle_command(input_str: str, config, state: _ReplState, llm) -> bool:
    """处理 / 前缀命令；返回 False 表示退出 REPL。"""
    parts = input_str[1:].split(maxsplit=1)
    cmd = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    if cmd in ("exit", "quit"):
        console.print("再见！")
        return False

    if cmd == "help":
        render_help(console)
        return True

    if cmd == "scan":
        _cmd_scan(args, state)
        return True

    if cmd == "audit":
        _cmd_audit(args, state, llm)
        return True

    if cmd == "redteam":
        _cmd_redteam(args, state)
        return True

    if cmd == "trace":
        _cmd_trace(args, state)
        return True

    if cmd == "resume":
        _cmd_resume(args, state)
        return True

    if cmd == "config":
        _cmd_config(args, config)
        return True

    console.print(f"[red]未知命令: /{cmd}[/red]  输入 /help 查看可用命令")
    return True


def _handle_shell(cmd_str: str, state: _ReplState) -> None:
    """! 前缀：整行透传给 bash；cd 跨调用自动持久化（见 poker.shell.run_shell）。"""
    cmd_str = cmd_str.strip()
    if not cmd_str:
        return

    try:
        result = run_shell(cmd_str, state.cwd)
    except subprocess.TimeoutExpired:
        console.print("[red]Shell 命令超时（60s）[/red]")
        return
    except Exception as e:
        console.print(f"[red]Shell 错误: {e}[/red]")
        return

    if result.new_cwd is not None and result.new_cwd != state.cwd:
        state.cwd = result.new_cwd
        set_project_root(result.new_cwd)

    if result.stdout:
        console.print(result.stdout, end="")
    if result.stderr:
        console.print(f"[red]{result.stderr}[/red]", end="")
    if result.returncode != 0:
        console.print(f"[dim](退出码 {result.returncode})[/dim]")


def _cmd_scan(args_str: str, state: _ReplState) -> None:
    """REPL /scan：支持 --quiet / --verbose；保存全量 findings 到 state。"""
    try:
        tokens = shlex.split(args_str) if args_str else []
    except ValueError as e:
        console.print(f"[red]/scan 参数解析错误: {e}[/red]")
        return

    quiet = "--quiet" in tokens or "-q" in tokens
    verbose = "--verbose" in tokens or "-v" in tokens
    targets = [t for t in tokens if not t.startswith("-")]

    if targets:
        p = Path(targets[0]).expanduser()
        path = p.resolve() if p.is_absolute() else (state.cwd / p).resolve()
    else:
        path = state.cwd

    if not path.exists():
        console.print(f"[red]目标不存在: {path}[/red]")
        return

    findings = scan_path(path)
    save_findings(state.cwd, findings)

    visible = filter_by_mode(findings, quiet, verbose)
    print_table_grouped(console, visible)
    print_summary(console, findings)


def _cmd_audit(args_str: str, state: _ReplState, llm) -> None:
    """REPL /audit <dim> [--schema <path>]：调能力层 run_audit。"""
    from poker.capabilities.audit import run_audit

    parts = shlex.split(args_str) if args_str else []
    if not parts:
        console.print(
            "[yellow]用法：/audit <dimension> [--schema <path>]"
            "  支持: tools / rag / mcp / prompt / mcp_schema[/yellow]"
        )
        return

    dimension: str | None = None
    schema_path: Path | None = None
    i = 0
    while i < len(parts):
        a = parts[i]
        if a == "--schema":
            i += 1
            if i >= len(parts):
                console.print("[red]--schema 缺少 path[/red]")
                return
            schema_path = Path(parts[i])
        elif a.startswith("--schema="):
            schema_path = Path(a.split("=", 1)[1])
        elif not a.startswith("--"):
            if dimension is None:
                dimension = a
            else:
                console.print(f"[yellow]意外参数: {a}[/yellow]")
                return
        else:
            console.print(f"[yellow]未知 flag: {a}[/yellow]")
            return
        i += 1

    if dimension is None:
        console.print("[yellow]用法：/audit <dimension>[/yellow]")
        return

    # 相对路径相对 state.cwd 解析（REPL 的 tracked cwd 与 os.getcwd 可能不同）
    if schema_path is not None and not schema_path.is_absolute():
        schema_path = (state.cwd / schema_path).resolve()

    try:
        run_audit(dimension, state.cwd, llm, console, schema_path=schema_path)
    except NotImplementedError as e:
        console.print(f"[yellow]{e}[/yellow]")
    except Exception as e:
        console.print(f"[red]/audit 错误: {e}[/red]")


def _cmd_redteam(args_str: str, state: _ReplState) -> None:
    """REPL /redteam <prompt-file> [--execute --endpoint <name>]：生成 / 执行攻击载荷。"""
    parts = shlex.split(args_str) if args_str else []
    if not parts:
        console.print("[yellow]用法：/redteam <prompt-file> [--execute --endpoint <name>][/yellow]")
        return

    # 轻量 flag 解析（不引 typer，避免 REPL 进入参数解析模式）
    prompt_file: Path | None = None
    execute = False
    endpoint_name: str | None = None
    i = 0
    while i < len(parts):
        a = parts[i]
        if a == "--execute":
            execute = True
        elif a == "--endpoint":
            i += 1
            if i >= len(parts):
                console.print("[red]--endpoint 缺少 name[/red]")
                return
            endpoint_name = parts[i]
        elif a.startswith("--endpoint="):
            endpoint_name = a.split("=", 1)[1]
        elif not a.startswith("--"):
            prompt_file = Path(a)
        else:
            console.print(f"[yellow]未知 flag: {a}[/yellow]")
            return
        i += 1

    if prompt_file is None:
        console.print("[yellow]用法：/redteam <prompt-file> [--execute --endpoint <name>][/yellow]")
        return

    try:
        if execute:
            from poker.cli.redteam import run_execute
            run_execute(prompt_file, state.cwd, endpoint_name, console)
        else:
            from poker.capabilities.redteam import run_redteam
            run_redteam(prompt_file, state.cwd, console)
    except Exception as e:
        console.print(f"[red]/redteam 错误: {e}[/red]")


def _cmd_trace(args_str: str, state: _ReplState) -> None:
    """REPL /trace <文件:行:变量>：数据流追踪。"""
    from poker.capabilities.trace import run_trace

    parts = shlex.split(args_str) if args_str else []
    if not parts:
        console.print("[yellow]用法：/trace <文件:行:变量>，例：agent.py:21:user_input[/yellow]")
        return
    try:
        run_trace(parts[0], state.cwd, console)
    except Exception as e:
        console.print(f"[red]/trace 错误: {e}[/red]")


def _cmd_resume(args_str: str, state: _ReplState) -> None:
    """REPL /resume：列出按时间切分的上下文窗口；选中后恢复并回放之前的对话。"""
    sessions = load_chat_sessions(state.cwd)
    if not sessions:
        console.print("[yellow]还没有历史上下文[/yellow]")
        return

    items = [(s, _format_session(s)) for s in sessions]
    chosen = select_one(title="选择上下文窗口", items=items)
    if chosen is None:
        console.print("[dim]已取消[/dim]")
        return

    state.session_id = chosen["id"]
    restore_session(state.session_id, chosen["messages"])
    _replay_session(chosen)


def _format_session(s: dict) -> str:
    ts = s["start_ts"][:16].replace("T", " ")
    return f"{ts}  ·  {len(s['messages'])} 条  ·  {s['preview']}"


def _replay_session(s: dict) -> None:
    """把恢复的 session 历史按 user/assistant 顺序打印出来，让用户看到上下文。"""
    ts = s["start_ts"][:16].replace("T", " ")
    console.print(f"\n[dim]── 上下文 {ts} 已恢复 ──[/dim]\n")
    for msg in s["messages"]:
        role = msg.get("role")
        content = (msg.get("content") or "").rstrip()
        if role == "user":
            console.print(f"[bold cyan]user ❯[/] {content}")
        elif role == "assistant":
            console.print(f"[bold green]assistant ❯[/] {content}")
        console.print()
    console.print(f"[dim]── 共 {len(s['messages'])} 条 · 继续输入即可接着聊 ──[/dim]\n")


def _cmd_config(sub: str, config) -> None:
    if sub in ("", "show"):
        table = Table(box=None, padding=(0, 2), show_header=False)
        table.add_column(style="bold gold3", no_wrap=True)
        table.add_column()
        table.add_row("Profile", config.profile)
        table.add_row("Provider", config.provider.name)
        table.add_row("Model", config.provider.model or "<未设置>")
        table.add_row("Base URL", config.provider.base_url or "<默认>")
        table.add_row("API Key", config.provider.redacted_key())
        table.add_row("API Key 就绪", "[green]是[/green]" if config.has_api_key else "[red]否[/red]")
        console.print(accent_panel(table, "Config · 当前配置"))
    elif sub == "doctor":
        checks = [
            ("API Key", bool(config.provider.api_key),
             "已设置" if config.provider.api_key else f"未设置，请设置 POKER_{config.provider.name.upper()}_API_KEY"),
            ("Provider", config.provider.name in PROVIDERS,
             config.provider.name if config.provider.name in PROVIDERS else f"无效: {config.provider.name}"),
            ("Model", bool(config.provider.model),
             config.provider.model if config.provider.model else "未设置"),
        ]
        table = Table(box=None, padding=(0, 2), show_header=True, header_style="bold gold3")
        table.add_column("检查项", no_wrap=True)
        table.add_column("状态")
        table.add_column("说明")
        all_ok = True
        for name, ok, detail in checks:
            table.add_row(name, "[green]OK[/green]" if ok else "[red]FAIL[/red]", detail)
            if not ok:
                all_ok = False
        console.print(accent_panel(table, "Config · 检查结果"))
        console.print("[green]所有检查通过[/green]" if all_ok else "[red]部分检查未通过[/red]")
    else:
        console.print(f"[red]未知子命令: config {sub}[/red]  可用: show, doctor")

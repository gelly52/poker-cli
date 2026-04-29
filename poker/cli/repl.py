"""交互式 REPL：纯文本=聊天，/xxx=命令，/exit=退出。"""
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from poker.agent.llm import create_chat_model
from poker.agent.runtime import stream_agent
from poker.capabilities.scan.engine import scan_path
from poker.capabilities.scan.report import print_table
from poker.config import load_config
from poker.config.models import PROVIDERS

console = Console()

REPL_HELP = """\
可用命令:
  /scan [target]       扫描目标文件或目录
  /config show         显示当前配置
  /config doctor       检查配置有效性
  /help                显示帮助
  /exit                退出

其他输入将与安全 Agent 对话。"""


def start_repl() -> None:
    config = load_config()
    llm = create_chat_model(config.provider) if config.has_api_key else None
    session_id = "repl"

    console.print(Panel("Poker CLI - AI security agent CLI for LLM and agent projects.", title="[bold]Poker CLI[/bold]", border_style="blue"))

    while True:
        try:
            user_input = input("> ").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n再见！")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            if not _handle_command(user_input, config):
                break
            continue

        if not llm:
            console.print("[red]未配置 API key，请先运行 poker init 或设置环境变量[/red]")
            continue

        try:
            text = Text()
            with Live(Panel(text, title="Poker", border_style="green"), console=console, refresh_per_second=8) as live:
                for token, _ in stream_agent(llm, user_input, session_id):
                    text.append(token)
                    live.update(Panel(text, title="Poker", border_style="green"))
        except Exception as e:
            console.print(f"[red]Agent 错误: {e}[/red]")


def _handle_command(input_str: str, config) -> bool:
    """处理 / 前缀命令，返回 False 表示退出 REPL。"""
    parts = input_str[1:].split(maxsplit=1)
    cmd = parts[0] if parts else ""
    args = parts[1] if len(parts) > 1 else ""

    if cmd in ("exit", "quit"):
        console.print("再见！")
        return False

    if cmd == "help":
        console.print(REPL_HELP)
        return True

    if cmd == "scan":
        _cmd_scan(args or ".")
        return True

    if cmd == "config":
        _cmd_config(args, config)
        return True

    console.print(f"[red]未知命令: /{cmd}[/red]  输入 /help 查看可用命令")
    return True


def _cmd_scan(target: str) -> None:
    path = Path(target)
    if not path.exists():
        console.print(f"[red]目标不存在: {target}[/red]")
        return
    findings = scan_path(path)
    print_table(console, findings)


def _cmd_config(sub: str, config) -> None:
    if sub in ("", "show"):
        table = Table(title="Poker CLI 配置")
        table.add_column("键", style="bold")
        table.add_column("值")
        table.add_row("Profile", config.profile)
        table.add_row("Provider", config.provider.name)
        table.add_row("Model", config.provider.model or "<未设置>")
        table.add_row("Base URL", config.provider.base_url or "<默认>")
        table.add_row("API Key", config.provider.redacted_key())
        table.add_row("API Key 就绪", "[green]是[/green]" if config.has_api_key else "[red]否[/red]")
        console.print(table)
    elif sub == "doctor":
        checks = [
            ("API Key", bool(config.provider.api_key),
             "已设置" if config.provider.api_key else f"未设置，请设置 POKER_{config.provider.name.upper()}_API_KEY"),
            ("Provider", config.provider.name in PROVIDERS,
             config.provider.name if config.provider.name in PROVIDERS else f"无效: {config.provider.name}"),
            ("Model", bool(config.provider.model),
             config.provider.model if config.provider.model else "未设置"),
        ]
        table = Table(title="配置检查")
        table.add_column("检查项")
        table.add_column("状态")
        table.add_column("说明")
        all_ok = True
        for name, ok, detail in checks:
            table.add_row(name, "[green]OK[/green]" if ok else "[red]FAIL[/red]", detail)
            if not ok:
                all_ok = False
        console.print(table)
        console.print("[green]所有检查通过[/green]" if all_ok else "[red]部分检查未通过[/red]")
    else:
        console.print(f"[red]未知子命令: config {sub}[/red]  可用: show, doctor")

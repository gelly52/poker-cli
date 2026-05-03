"""Poker CLI 命令入口。"""
import typer

from poker.cli.audit import register_audit
from poker.cli.config import register_config
from poker.cli.init import register_init
from poker.cli.redteam import register_redteam
from poker.cli.runtime import register_runtime
from poker.cli.scan import register_scan
from poker.cli.trace import register_trace

app = typer.Typer(
    name="poker",
    help="Poker CLI - AI security agent CLI for LLM and agent projects.",
    add_completion=False,
)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Poker CLI - AI security agent CLI."""
    if ctx.invoked_subcommand is None:
        from poker.cli.repl import start_repl
        start_repl()


# 注册所有一次性命令
register_scan(app)
register_audit(app)
register_redteam(app)
register_trace(app)
register_init(app)
register_config(app)
register_runtime(app)

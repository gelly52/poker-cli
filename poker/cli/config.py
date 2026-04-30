"""config 命令实现——查看配置、检查配置有效性。"""
import typer
from rich.console import Console
from rich.table import Table

from poker.config import load_config

console = Console()


def register_config(app: typer.Typer) -> None:
    """将 config 命令和 doctor 子命令注册到 Typer app。"""

    config_app = typer.Typer(help="查看和修改配置")

    @config_app.command("show")
    def config_show() -> None:
        """显示当前配置（敏感信息脱敏）。"""
        config = load_config()

        table = Table(title="Poker CLI 配置")
        table.add_column("键", style="bold")
        table.add_column("值")

        table.add_row("Profile", config.profile)
        table.add_row("Provider", config.provider.name)
        table.add_row("Model", config.provider.model or "<未设置>")
        table.add_row("Base URL", config.provider.base_url or "<默认>")
        table.add_row("API Key", config.provider.redacted_key())
        table.add_row("LLM 就绪", "[green]是[/green]" if config.has_api_key else "[red]否[/red]")

        console.print(table)

    @config_app.command("doctor")
    def config_doctor() -> None:
        """检查配置是否有效。"""
        config = load_config()
        checks = _run_checks(config)

        table = Table(title="配置检查")
        table.add_column("检查项")
        table.add_column("状态")
        table.add_column("说明")

        all_ok = True
        for name, ok, detail in checks:
            status = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
            if not ok:
                all_ok = False
            table.add_row(name, status, detail)

        console.print(table)

        if all_ok:
            console.print("\n[green]所有检查通过[/green]")
        else:
            console.print("\n[red]部分检查未通过，请按提示修复[/red]")
            raise typer.Exit(code=1)

    app.add_typer(config_app, name="config")


def _run_checks(config) -> list[tuple[str, bool, str]]:
    """执行配置检查，返回 (检查名, 是否通过, 说明) 列表。"""
    checks: list[tuple[str, bool, str]] = []

    # 检查1：API key 是否设置
    has_key = bool(config.provider.api_key)
    checks.append((
        "API Key",
        has_key,
        "已设置" if has_key else f"未设置，请在 .aisec/config.toml 写入 provider.api_key，或设置环境变量 POKER_{config.provider.name.upper()}_API_KEY",
    ))

    # 检查2：provider 是否有效
    from poker.config.models import PROVIDERS
    valid_provider = config.provider.name in PROVIDERS
    checks.append((
        "Provider",
        valid_provider,
        config.provider.name if valid_provider else f"无效 provider: {config.provider.name}",
    ))

    # 检查3：model 是否设置
    has_model = bool(config.provider.model)
    checks.append((
        "Model",
        has_model,
        config.provider.model if has_model else "未设置模型名称",
    ))

    return checks

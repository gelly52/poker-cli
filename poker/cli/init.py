"""init 命令实现——交互式初始化项目配置。"""
from pathlib import Path

import typer
from rich.console import Console

from poker.config import PROVIDERS, PokerConfig, ProviderConfig, save_project_config

console = Console()


def register_init(app: typer.Typer) -> None:
    """将 init 命令注册到 Typer app。"""

    @app.command()
    def init() -> None:
        """交互式初始化项目安全配置。"""

        project_root = Path.cwd()

        # 选择 provider
        console.print("\n[bold]可用 LLM Provider:[/bold]")
        for i, name in enumerate(PROVIDERS, 1):
            console.print(f"  {i}. {name}")

        choice = typer.prompt("选择 provider（输入编号或名称）", default="1")
        provider_name = _resolve_provider(choice)
        if provider_name is None:
            console.print(f"[red]无效选择:[/red] {choice}")
            raise typer.Exit(code=1)

        # 输入 API key（可选，支持后续通过环境变量设置）
        api_key = typer.prompt(
            f"输入 {provider_name} API key（留空则稍后通过环境变量设置）",
            default="",
            show_default=False,
        )

        # 输入 model
        default_model = _default_model(provider_name)
        model = typer.prompt("模型名称", default=default_model)

        # 输入 base_url（可选）
        base_url = typer.prompt(
            "API base URL（留空使用默认）",
            default="",
            show_default=False,
        )

        # 构建配置并保存
        provider = ProviderConfig(
            name=provider_name,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )
        config = PokerConfig(provider=provider)

        save_project_config(config, project_root)

        console.print(f"\n[green]配置已保存到 {project_root}/.aisec/config.toml[/green]")
        console.print(f"  Provider: {provider_name}")
        console.print(f"  Model:    {model}")
        if api_key:
            console.print(f"  API Key:  {provider.redacted_key()}")
        else:
            console.print("  API Key:  [yellow]未设置，请通过环境变量提供[/yellow]")
            console.print(f"  环境变量: POKER_{provider_name.upper()}_API_KEY=your-key")


def _resolve_provider(choice: str) -> str | None:
    """解析用户输入的 provider 选择。"""
    choice = choice.strip().lower()
    if choice in PROVIDERS:
        return choice
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(PROVIDERS):
            return PROVIDERS[idx]
    except ValueError:
        pass
    return None


def _default_model(provider: str) -> str:
    """每个 provider 的默认模型。"""
    defaults = {
        "openai": "gpt-4o-mini",
        "anthropic": "claude-sonnet-4-20250514",
        "deepseek": "deepseek-chat",
        "qwen": "qwen-plus",
        "local": "local-model",
    }
    return defaults.get(provider, "gpt-4o-mini")

"""/help 命令渲染：跟 splash 同款金色 Panel + ROUNDED 边框，统一视觉风格。

命令列表直接复用 poker.ui.prompt 的 COMMANDS（补全菜单的同一份数据），
新加 / 命令只需改 prompt.COMMANDS 一处，自动同步到补全和 /help。
"""
from rich.console import Console, Group
from rich.table import Table
from rich.text import Text

from poker.ui.panels import ACCENT, accent_panel
from poker.ui.prompt import COMMANDS

# 不属于 / 命令的额外说明（Shell / Chat / 快捷键）
_EXTRA_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Shell 命令 (!cmd)", [
        ("!<command>", "透传给 bash；cd 跨调用自动持久化"),
    ]),
    ("Chat", [
        ("(其他输入)", "与安全 Agent 对话"),
        ("↑ / ↓",     "浏览输入历史"),
        ("/ 后弹菜单", "↑/↓ 选择命令补全，回车填入"),
    ]),
]


def _build_syntax_legend() -> Text:
    """命令清单底部的参数语法说明。"""
    accent = f"bold {ACCENT}"
    legend = Text()
    legend.append("参数语法  ", style="bold")
    legend.append("<x>",    style=accent); legend.append(" 必填    ", style="dim")
    legend.append("[x]",    style=accent); legend.append(" 可选    ", style="dim")
    legend.append("a|b|c",  style=accent); legend.append(" 枚举    ", style="dim")
    legend.append("--flag", style=accent); legend.append(" 开关",     style="dim")
    return legend


def render_help(console: Console) -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style=f"bold {ACCENT}", no_wrap=True)
    table.add_column(style="dim white")

    table.add_row(Text("内置命令 (/cmd)", style="bold"), "")
    for cmd, hint in COMMANDS:
        table.add_row(f"  {cmd}", hint)

    for group_name, items in _EXTRA_GROUPS:
        table.add_row("", "")
        table.add_row(Text(group_name, style="bold"), "")
        for cmd, desc in items:
            table.add_row(f"  {cmd}", desc)

    body = Group(table, Text(""), _build_syntax_legend())
    console.print(accent_panel(body, "Help · 命令清单"))

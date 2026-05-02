"""启动 splash banner：外层固定宽度 Panel，左侧扑克牌图标 + 右侧 POKER 大字 + 4 张能力卡。"""

from pathlib import Path

from rich.align import Align
from rich.box import ROUNDED
from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from poker import __version__

# 外层框宽度——超出此宽度的终端，splash 居左不再撑满
_OUTER_WIDTH = 100

# 填充式扑克牌图标（A♠），用 █ 堆出花色形状；纯 ASCII + box-drawing
_CARD_ICON = """\
╭─────────────╮
│A            │
│      █      │
│     ███     │
│    █████    │
│   ███████   │
│   ███████   │
│     ███     │
│      █      │
│    █████    │
│            A│
╰─────────────╯"""

# POKER 大字（figlet "standard" 风格，5 行高）
_POKER_LOGO = r"""
 ____   ___  _  _______ ____
|  _ \ / _ \| |/ / ____|  _ \
| |_) | | | | ' /|  _| | |_) |
|  __/| |_| | . \| |___|  _ <
|_|    \___/|_|\_\_____|_| \_\
"""

_ICON_COL_WIDTH = 22

# 每个能力一行：(名称, 花色, 颜色, 单行描述)
_CARDS = [
    ("SCAN", "♣", "bright_green", "Discover assets and attack surface"),
    ("AUDIT", "♥", "bright_red", "Identify risks and security issues"),
    ("REDTEAM", "♠", "bright_white", "Simulate real attacks and validate defenses"),
    ("TRACE", "♦", "bright_yellow", "Trace paths and attack provenance"),
]


def _build_top() -> Table:
    """左：扑克牌图标；右：POKER logo + 副标题 + 花色行（全部居中对齐）。"""
    icon = Text(_CARD_ICON, style="gold3", no_wrap=True)

    logo = Text(_POKER_LOGO, style="bold gold3", no_wrap=True)
    subtitle = Text(
        "Poker CLI - AI security agent CLI for LLM and agent projects.", style="dim white"
    )
    suits_line = Text.assemble(
        ("♣  ", "bright_green"),
        ("♥  ", "bright_red"),
        ("♠  ", "bright_white"),
        ("♦", "bright_yellow"),
    )
    right_block = Group(
        Align.center(logo),
        Align.center(subtitle),
        Text(""),
        Align.center(suits_line),
    )

    # 左列宽度大于卡牌图标本身，再用 justify="center" 让卡牌在列内左右居中
    table = Table.grid(expand=True)
    table.add_column(width=_ICON_COL_WIDTH, no_wrap=True, vertical="middle", justify="center")
    table.add_column(ratio=1, vertical="middle")
    table.add_row(icon, right_block)
    return table


def _build_cards_row() -> Table:
    """4 行紧凑列表：花色 + 名称 + 单行描述。比 2×2 网格省 80% 垂直空间。"""
    table = Table.grid(padding=(0, 2))
    table.add_column(justify="center", no_wrap=True)
    table.add_column(no_wrap=True)
    table.add_column(ratio=1)

    for name, suit, color, desc in _CARDS:
        table.add_row(
            Text(suit, style=f"bold {color}"),
            Text(name, style=f"bold {color}"),
            Text(desc, style="dim white"),
        )
    return table


def _build_tagline() -> Text:
    return Text.assemble(
        ("Four suits. Four capabilities. One goal: ", "dim white"),
        ("take control", "bold gold3"),
        (".", "dim white"),
    )


def render_splash(console: Console, project_root: Path) -> None:
    """打印外层框 + 完整 banner。版本号显示在左上 title，项目路径显示在左下 subtitle。"""
    width = min(console.width - 2, _OUTER_WIDTH)

    body = Group(
        _build_top(),
        Text(""),
        Padding(_build_cards_row(), (0, 0, 0, 5)),  # 左缩进与上方卡牌图标的起始位置对齐
        Text(""),
        Rule(_build_tagline(), style="dim white"),
    )

    outer = Panel(
        body,
        title=Text(f"POKER v{__version__}", style="bold gold3"),
        title_align="left",
        subtitle=Text(f"  {project_root}  ", style="dim white"),
        subtitle_align="left",
        border_style="gold3",
        box=ROUNDED,
        width=width,
        padding=(1, 2),
    )
    console.print(outer)

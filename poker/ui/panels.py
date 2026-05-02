"""统一的 Panel 工厂 + 主色常量。

所有需要金色圆角框的 rich 渲染（/help、/config、未来的 /scan 报告等）都通过
`accent_panel()` 创建，保证视觉一致。换主色只改这里的 ACCENT 一处。
"""
from rich.box import ROUNDED
from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text

ACCENT = "gold3"  # rich 主色（命名色）


def accent_panel(content: RenderableType, title: str) -> Panel:
    """跟 splash / help 同款的金色圆角 Panel。"""
    return Panel(
        content,
        title=Text(title, style=f"bold {ACCENT}"),
        title_align="left",
        border_style=ACCENT,
        box=ROUNDED,
        padding=(1, 2),
    )

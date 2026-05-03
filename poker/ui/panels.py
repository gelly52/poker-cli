"""统一的 Panel 工厂 + 主色常量。

所有需要金色圆角框的 rich 渲染（/help、/config、未来的 /scan 报告等）都通过
`accent_panel()` 创建，保证视觉一致。换主色只改这里的 ACCENT / ACCENT_HEX 一处。
"""
from rich.box import ROUNDED
from rich.console import RenderableType
from rich.panel import Panel
from rich.text import Text

ACCENT = "gold3"          # rich 命名色（给 rich.Style 用）
ACCENT_HEX = "#d7af00"    # 等价 hex（给 prompt_toolkit.Style 用，rich 不识别命名色到 hex 自动转）


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

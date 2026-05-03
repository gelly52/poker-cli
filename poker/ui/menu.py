"""输入框下方的 inline 选择菜单（不接管全屏）。

基于 prompt_toolkit Application(full_screen=False)：菜单临时占位，
按键选完后渲染区域自动擦除，光标回到调用前位置，splash 与历史输出全部保留。

通用工具：调用方传 (value, label) 列表，返回选中的 value 或 None（取消）。
"""

from typing import TypeVar

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.styles import Style

from poker.ui.panels import ACCENT_HEX

T = TypeVar("T")

_LOAD_MORE = object()
# title 用金色加粗；hint 不设任何颜色避免 Windows 终端把 ansibrightblack 误解为底色
_STYLE = Style.from_dict(
    {
        "menu.title": f"bold {ACCENT_HEX}",
        "menu.hint": "",
        "menu.selected": f"reverse bold {ACCENT_HEX}",
    }
)


def select_one(
    title: str,
    items: list[tuple[T, str]],
    page_size: int = 10,
    hint: str = "↑/↓ 浏览  Enter 确认  Esc 取消",
) -> T | None:
    """从 items 选一个。超过 page_size 自动加分页"加载更多"。返回 None 表示取消。"""
    if not items:
        return None

    page = 0
    while True:
        start = page * page_size
        page_items = items[start : start + page_size]
        if not page_items:
            return None

        values: list[tuple[object, str]] = list(page_items)
        if start + page_size < len(items):
            values.append((_LOAD_MORE, "[加载更多 …]"))

        chosen = _run_inline_menu(title, hint, values)
        if chosen is None:
            return None
        if chosen is _LOAD_MORE:
            page += 1
            continue
        return chosen  # type: ignore[return-value]


def _run_inline_menu(title: str, hint: str, values: list[tuple[object, str]]) -> object | None:
    selected = [0]

    def get_text() -> FormattedText:
        lines: list[tuple[str, str]] = [
            ("class:menu.title", f"{title}\n"),
            ("class:menu.hint", f"{hint}\n\n"),
        ]
        for i, (_, label) in enumerate(values):
            style = "class:menu.selected" if i == selected[0] else ""
            prefix = "❯ " if i == selected[0] else "  "
            lines.append((style, f"{prefix}{label}\n"))
        return FormattedText(lines)

    kb = KeyBindings()

    @kb.add("up")
    def _(event):
        selected[0] = (selected[0] - 1) % len(values)

    @kb.add("down")
    def _(event):
        selected[0] = (selected[0] + 1) % len(values)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=values[selected[0]][0])

    @kb.add("escape")
    @kb.add("c-c")
    def _(event):
        event.app.exit(result=None)

    body = Window(
        FormattedTextControl(get_text, focusable=True),
        height=Dimension(min=len(values) + 3, max=len(values) + 3),
        style="bg:default",  # 清掉 widget 默认底色，让终端原生背景透出来
    )
    app: Application = Application(
        layout=Layout(HSplit([body])),
        key_bindings=kb,
        style=_STYLE,
        full_screen=False,
        mouse_support=False,
    )
    return app.run()

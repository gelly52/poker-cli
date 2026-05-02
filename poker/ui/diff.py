"""diff 预览 + y/n 确认（写文件 / 改代码工具的 HITL 闸门）。

写盘前先用 difflib 生成 unified diff，rich 渲染 syntax 高亮，
prompt_toolkit 读 y/n。回车默认接受（Y），输入 n / no 拒绝。
KeyboardInterrupt / EOFError 也按拒绝处理。
"""
import difflib

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import HTML
from rich.console import Console
from rich.syntax import Syntax

from poker.ui.panels import accent_panel

_console = Console()


def show_diff_and_confirm(old_text: str, new_text: str, path: str) -> bool:
    """显示 diff + 等用户确认；返回 True 接受、False 拒绝。

    - 内容未变化：直接拒绝（无需写盘）
    - 默认动作（回车 / 空输入）：接受
    - 输入 n / no（大小写不敏感）：拒绝
    - Ctrl+C / EOF：拒绝
    """
    if old_text == new_text:
        _console.print(f"[yellow]内容未变化：{path}（跳过）[/yellow]")
        return False

    diff_text = _build_unified_diff(old_text, new_text, path)
    syntax = Syntax(diff_text, "diff", theme="ansi_dark", word_wrap=False)
    _console.print(accent_panel(syntax, f"Diff · {path}"))

    try:
        answer = pt_prompt(HTML("<ansicyan><b>应用以上修改？[Y/n] </b></ansicyan>"))
    except (KeyboardInterrupt, EOFError):
        _console.print("[dim]已取消[/dim]")
        return False

    return answer.strip().lower() not in {"n", "no"}


def _build_unified_diff(old_text: str, new_text: str, path: str) -> str:
    """生成 unified diff 文本；空 diff 也返回提示头。"""
    diff_lines = list(
        difflib.unified_diff(
            old_text.splitlines(keepends=False),
            new_text.splitlines(keepends=False),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="",
        )
    )
    return "\n".join(diff_lines) if diff_lines else f"--- a/{path}\n+++ b/{path}\n(空 diff)"

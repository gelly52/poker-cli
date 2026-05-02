"""高风险操作的强确认对话：要求用户输入完整 phrase 才放行。

不接受 y/n 简写；任何与目标 phrase 不完全相符的输入（含 Ctrl+C / EOF）都视为拒绝。
用于 /redteam --execute 这类有外部副作用的动作。
"""
from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.formatted_text import HTML
from rich.console import Console

_console = Console()


def confirm_phrase(phrase: str, prompt_text: str = "") -> bool:
    """显示 prompt_text，等用户输入完整 phrase；不匹配返回 False。

    匹配判定：strip() 后字符串相等（区分大小写）。
    """
    if prompt_text:
        _console.print(prompt_text)
    try:
        answer = pt_prompt(HTML(f"<ansicyan><b>请输入 '{phrase}' 继续：</b></ansicyan>"))
    except (KeyboardInterrupt, EOFError):
        _console.print("[dim]已取消[/dim]")
        return False
    if answer.strip() != phrase:
        _console.print("[yellow]输入与确认词不匹配，已中止[/yellow]")
        return False
    return True

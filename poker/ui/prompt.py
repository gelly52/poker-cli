"""prompt_toolkit 输入封装：cyan prompt + 上下键浏览历史 + 持久化历史 + 斜杠命令补全。

斜杠命令清单见 COMMANDS。新增命令只需追加一行，无需改其他逻辑。
"""
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import completion_is_selected
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings


_PROMPT_HTML = HTML("<ansicyan><b>poker ❯ </b></ansicyan>")

# (命令, 提示)；加新命令只动这里
COMMANDS: list[tuple[str, str]] = [
    ("/scan",    "扫描目标文件或目录"),
    ("/audit",   "深度审计某维度（MVP: tools）"),
    ("/redteam", "对 prompt 文件生成攻击载荷"),
    ("/trace",   "数据流追踪到危险 sink"),
    ("/resume",  "显示最近 N 条会话历史"),
    ("/config",  "显示/检查配置"),
    ("/help",    "显示帮助"),
    ("/exit",    "退出 REPL"),
    ("/quit",    "退出 REPL"),
]


class _SlashCompleter(Completer):
    """仅在以 / 开头且未输入空格时返回候选；其他输入不打扰。"""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for cmd, hint in COMMANDS:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display=cmd, display_meta=hint)


def _build_key_bindings() -> KeyBindings:
    """选中候选时 Enter 只关菜单、不提交，让用户继续敲参数后再回车执行。"""
    kb = KeyBindings()

    @kb.add("enter", filter=completion_is_selected)
    def _(event):
        event.current_buffer.complete_state = None

    return kb


def create_session(history_path: Path) -> PromptSession:
    """按项目持久化历史；上下方向键浏览历史，输入 / 后浏览命令候选。"""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    return PromptSession(
        history=FileHistory(str(history_path)),
        completer=_SlashCompleter(),
        complete_while_typing=True,
        key_bindings=_build_key_bindings(),
    )


def read_line(session: PromptSession) -> str:
    """读一行输入；KeyboardInterrupt / EOFError 由调用方处理。"""
    return session.prompt(_PROMPT_HTML)

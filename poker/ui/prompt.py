"""prompt_toolkit 输入封装：cyan prompt + 上下键浏览历史 + 持久化历史 + 斜杠命令补全。

斜杠命令清单见 COMMANDS。新增命令只需追加一行，无需改其他逻辑。

设计要点：
- 补全菜单样式跟 select_one 同款金色（共享 panels.ACCENT_HEX，单点换主色）
- 候选弹出时**自动预选第一个**：用户输 `/sc` 直接 Enter 即可 apply 到 `/scan`，少敲两个字
- 选中候选 + Enter 只 apply 不提交，让用户继续敲参数后再回车执行
"""
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from poker.ui.panels import ACCENT_HEX

_PROMPT_HTML = HTML("<ansicyan><b>poker ❯ </b></ansicyan>")

# (命令, 提示)；加新命令只动这里。提示尾部用 CLI 标准约定标记参数：
#   <x>      = 必填位置参数
#   [x]      = 可选位置参数
#   a|b|c    = 枚举值
#   --flag   = flag
# 不带参数的命令不加后缀。
COMMANDS: list[tuple[str, str]] = [
    ("/scan",         "扫描目标文件或目录  [path] [--quiet|--verbose]"),
    ("/audit",        "深度审计某维度  <tools|rag|mcp|prompt|mcp_schema>"),
    ("/redteam",      "对 prompt 生成攻击载荷  <prompt-file> [--execute --endpoint <name>]"),
    ("/trace",        "数据流追踪  <file:line:var>"),
    ("/explain",      "用项目上下文解释 finding  [finding-id 前缀]"),
    ("/triage",       "对未 triage 的 finding 逐条决策（LLM 协助）"),
    ("/investigate",  "Agent 综合调查  <topic> [--single|--multi]"),
    ("/threat-model", "基于已有产出输出 STRIDE 风格威胁模型"),
    ("/resume",       "显示最近 N 条会话历史"),
    ("/config",       "显示/检查配置  [show|doctor]"),
    ("/help",         "显示帮助"),
    ("/exit",         "退出 REPL"),
    ("/quit",         "退出 REPL"),
]


# 跟 menu.py 同款金色；bg:default 让终端原生底色透出来，避免突兀色块
_COMPLETION_STYLE = Style.from_dict(
    {
        "completion-menu":                          "bg:default",
        "completion-menu.completion":               f"bg:default fg:{ACCENT_HEX}",
        "completion-menu.completion.current":       f"reverse bold fg:{ACCENT_HEX}",
        "completion-menu.meta.completion":          "bg:default fg:#808080",
        "completion-menu.meta.completion.current":  f"reverse fg:{ACCENT_HEX}",
        "completion-menu.multi-column-meta":        "bg:default fg:#808080",
    }
)


class _SlashCompleter(Completer):
    """仅在以 / 开头且未输入空格时返回候选；其他输入不打扰。"""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or " " in text:
            return
        for cmd, hint in COMMANDS:
            if cmd.startswith(text):
                yield Completion(cmd, start_position=-len(text), display=cmd, display_meta=hint)


def _select_first_completion(buffer: Buffer) -> None:
    """补全候选刷新时把第一个标为**预选**（仅高亮、不 apply 到 buffer）。

    直接改 `complete_state.complete_index` 而不是调 `buffer.go_to_completion`，
    后者会同步 apply 候选到 buffer.text —— 那会导致"输 /h 自动变成 /help，
    再敲 e 变成 /helpe"的反直觉行为。

    防御逻辑：`set_completions` 跟渲染之间存在 race 窗口，可能留下越界的旧
    `complete_index`（导致官方 `completion_is_selected` filter 抛 IndexError 让事件
    循环卡死）。本 hook 在设新 index 前先校正越界，全程 try/except 兜底，
    遵循 observer 边界："hook 出错绝不传播"。

    交互效果：
    - 用户继续输入 → buffer 自动 cancel_completion → 重新弹菜单 → 再次预选第一项
    - 用户按 Enter → enter handler 真正 apply 候选 + 关菜单（不提交）
    - 用户按 ↑/↓  → 走 prompt_toolkit 默认（移动并 preview apply，符合直觉）
    """
    try:
        state = buffer.complete_state
        if state is None:
            return
        completions = state.completions or []
        idx = state.complete_index
        # 修复 race 留下的越界 index：要么落到合法首项，要么清掉
        if idx is not None and idx >= len(completions):
            state.complete_index = 0 if completions else None
            return
        # 没人选过 + 有候选 → 预选第一项
        if completions and idx is None:
            state.complete_index = 0
    except Exception:
        pass


@Condition
def _has_selected_completion() -> bool:
    """安全版 `completion_is_selected`：越界 / 异常一律 False，不让 filter 抛栈。

    替代 prompt_toolkit 官方的同名 filter，避免内部 race 导致整条事件循环挂掉。
    """
    try:
        state = get_app().current_buffer.complete_state
        if state is None or state.complete_index is None:
            return False
        completions = state.completions or []
        return 0 <= state.complete_index < len(completions)
    except Exception:
        return False


def _build_key_bindings() -> KeyBindings:
    """Enter 在选中候选时：apply 到 buffer + 关菜单，让用户继续敲参数后再回车执行。"""
    kb = KeyBindings()

    @kb.add("enter", filter=_has_selected_completion)
    def _(event):
        buffer = event.current_buffer
        state = buffer.complete_state
        try:
            if (
                state is not None
                and state.complete_index is not None
                and state.completions
                and 0 <= state.complete_index < len(state.completions)
            ):
                # 预选路径下 buffer.text 还没变，需要在这里真正 apply
                buffer.apply_completion(state.completions[state.complete_index])
        except Exception:
            pass
        buffer.complete_state = None

    return kb


def create_session(history_path: Path) -> PromptSession:
    """按项目持久化历史；上下方向键浏览历史，输入 / 后浏览命令候选。"""
    history_path.parent.mkdir(parents=True, exist_ok=True)
    session: PromptSession = PromptSession(
        history=FileHistory(str(history_path)),
        completer=_SlashCompleter(),
        complete_while_typing=True,
        key_bindings=_build_key_bindings(),
        style=_COMPLETION_STYLE,
    )
    # complete_while_typing 触发的候选默认无选中；挂钩子让第一项自动高亮
    session.default_buffer.on_completions_changed += _select_first_completion
    return session


def read_line(session: PromptSession) -> str:
    """读一行输入；KeyboardInterrupt / EOFError 由调用方处理。"""
    return session.prompt(_PROMPT_HTML)

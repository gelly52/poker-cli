"""LangChain BaseCallbackHandler 子类：捕获 LLM / tool 事件 → 检测 → 异步落盘。

设计原则：
- **opt-in**：用户在自家项目显式 `callbacks=[PokerCallbackHandler(project="...")]` 才生效
- **不影响目标项目稳定性**：所有钩子最外层 try/except 兜底，永不抛栈到目标项目
- **零阻塞**：写盘走 AsyncJsonlWriter 后台线程，钩子调用永远立即返回
- **不发外网**：检测 + 落盘全在本地

事件结构（jsonl 一行一条）：
  {ts, project, kind, run_id, parent_run_id, payload, detections: list}
"""
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

from poker_observer.detectors import (
    DEFAULT_TOKEN_THRESHOLD,
    detect_in_prompt,
    detect_in_response,
)
from poker_observer.writer import AsyncJsonlWriter

_TRUNCATE_LIMIT = 2000


def _project_hash(project: str) -> str:
    return hashlib.sha256(project.encode("utf-8")).hexdigest()[:12]


def default_runtime_dir(project: str) -> Path:
    """默认日志目录：~/.poker/runtime/<project_hash>/。"""
    base = Path.home() / ".poker" / "runtime" / _project_hash(project)
    base.mkdir(parents=True, exist_ok=True)
    return base


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_str_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _truncate(text: Any, limit: int = _TRUNCATE_LIMIT) -> str:
    if not isinstance(text, str):
        text = str(text) if text is not None else ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"... [truncated, original {len(text)} chars]"


def _extract_response_text(response: Any) -> str:
    """从 LangChain LLMResult / ChatResult 抽出文本。多 generation 拼接。"""
    try:
        gens = getattr(response, "generations", None)
        if not gens:
            return ""
        parts: list[str] = []
        for batch in gens:
            for g in batch:
                t = getattr(g, "text", None)
                if t:
                    parts.append(t)
                    continue
                msg = getattr(g, "message", None)
                if msg is not None:
                    content = getattr(msg, "content", None)
                    if content:
                        parts.append(content if isinstance(content, str) else str(content))
        return "\n".join(p for p in parts if p)
    except Exception:
        return ""


def _extract_token_usage(response: Any) -> dict:
    """LangChain 通常把 usage 放到 LLMResult.llm_output['token_usage']。"""
    try:
        out = getattr(response, "llm_output", None) or {}
        usage = out.get("token_usage") or out.get("usage") or {}
        if not isinstance(usage, dict):
            return {}
        return {k: v for k, v in usage.items() if isinstance(v, (int, float))}
    except Exception:
        return {}


class PokerCallbackHandler(BaseCallbackHandler):
    """LangChain callback handler：捕获 LLM / tool 事件，本地写 JSONL + 实时检测。

    用法（在用户的项目里）：

        from poker_observer import PokerCallbackHandler
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(callbacks=[PokerCallbackHandler(project="my-rag")])
        # 跑 invoke 即可；事件落到 ~/.poker/runtime/<hash>/<ts>.jsonl

        # poker runtime show --project my-rag 离线分析
    """

    raise_error: bool = False  # LangChain 看到 False 会吞掉钩子异常

    def __init__(
        self,
        project: str,
        *,
        runtime_dir: Path | None = None,
        token_anomaly_threshold: int = DEFAULT_TOKEN_THRESHOLD,
        writer: AsyncJsonlWriter | None = None,
    ) -> None:
        super().__init__()
        self._project = project
        self._token_threshold = token_anomaly_threshold

        if writer is not None:
            self._writer = writer
            self._log_path = getattr(writer, "file_path", None) or Path(".")
        else:
            target_dir = runtime_dir if runtime_dir is not None else default_runtime_dir(project)
            target_dir.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            self._log_path = target_dir / f"{ts}.jsonl"
            self._writer = AsyncJsonlWriter(self._log_path)

    # ---------- 公开属性 ----------

    @property
    def project(self) -> str:
        return self._project

    @property
    def log_path(self) -> Path:
        return self._log_path

    def close(self) -> None:
        """显式关闭后台 writer 线程；测试 / 程序退出前调一次。"""
        try:
            self._writer.close()
        except Exception:
            pass

    # ---------- LLM 钩子 ----------

    def on_llm_start(
        self,
        serialized: dict[str, Any] | None,
        prompts: list[str] | None,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            prompts = prompts or []
            detections: list[dict] = []
            for p in prompts:
                detections.extend(detect_in_prompt(p))
            self._emit(
                kind="llm_start",
                run_id=run_id,
                parent_run_id=parent_run_id,
                payload={
                    "model": _model_name(serialized),
                    "prompts": [_truncate(p) for p in prompts],
                    "n_prompts": len(prompts),
                },
                detections=detections,
            )
        except Exception:
            pass

    def on_chat_model_start(
        self,
        serialized: dict[str, Any] | None,
        messages: list[list[Any]] | None,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            messages = messages or []
            flat: list[str] = []
            for batch in messages:
                for m in batch:
                    content = getattr(m, "content", None) or str(m)
                    flat.append(content if isinstance(content, str) else str(content))
            detections: list[dict] = []
            for t in flat:
                detections.extend(detect_in_prompt(t))
            self._emit(
                kind="chat_model_start",
                run_id=run_id,
                parent_run_id=parent_run_id,
                payload={
                    "model": _model_name(serialized),
                    "messages": [_truncate(t) for t in flat],
                    "n_messages": len(flat),
                },
                detections=detections,
            )
        except Exception:
            pass

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            text = _extract_response_text(response)
            usage = _extract_token_usage(response)
            detections = detect_in_response(text, usage=usage, token_threshold=self._token_threshold)
            self._emit(
                kind="llm_end",
                run_id=run_id,
                parent_run_id=parent_run_id,
                payload={"response": _truncate(text), "usage": usage},
                detections=detections,
            )
        except Exception:
            pass

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            self._emit(
                kind="llm_error",
                run_id=run_id,
                parent_run_id=parent_run_id,
                payload={"error": _truncate(str(error), 500)},
                detections=[],
            )
        except Exception:
            pass

    # ---------- 工具钩子 ----------

    def on_tool_start(
        self,
        serialized: dict[str, Any] | None,
        input_str: str,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            self._emit(
                kind="tool_start",
                run_id=run_id,
                parent_run_id=parent_run_id,
                payload={
                    "tool": (serialized or {}).get("name") if isinstance(serialized, dict) else None,
                    "input": _truncate(input_str),
                },
                detections=[],
            )
        except Exception:
            pass

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            text = output if isinstance(output, str) else str(output)
            self._emit(
                kind="tool_end",
                run_id=run_id,
                parent_run_id=parent_run_id,
                payload={"output": _truncate(text)},
                detections=[],
            )
        except Exception:
            pass

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID | None = None,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> Any:
        try:
            self._emit(
                kind="tool_error",
                run_id=run_id,
                parent_run_id=parent_run_id,
                payload={"error": _truncate(str(error), 500)},
                detections=[],
            )
        except Exception:
            pass

    # ---------- 写盘 ----------

    def _emit(
        self,
        *,
        kind: str,
        run_id: UUID | None,
        parent_run_id: UUID | None,
        payload: dict,
        detections: list[dict],
    ) -> None:
        record = {
            "ts": _now_iso(),
            "project": self._project,
            "kind": kind,
            "run_id": _to_str_id(run_id),
            "parent_run_id": _to_str_id(parent_run_id),
            "payload": payload,
            "detections": detections,
        }
        try:
            self._writer.write(record)
        except Exception:
            pass


def _model_name(serialized: Any) -> str | None:
    if not isinstance(serialized, dict):
        return None
    # LangChain 不同版本字段名不一
    for k in ("name", "id", "_type"):
        v = serialized.get(k)
        if v:
            return str(v) if not isinstance(v, list) else ".".join(map(str, v))
    return None

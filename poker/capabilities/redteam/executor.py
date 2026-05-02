"""红队 payload 执行器：按 endpoint 白名单 + 限速 + 超时同步发请求。

外部网络副作用！调用前必须由 CLI 完成二次确认 + audit 日志。

设计约束（hard requirements）：
  - 单次超时 30s（httpx.Client timeout）
  - 限速间隔 = 1 / endpoint.rate_limit；rate_limit ≤ 0 视为无限速
  - 单次 run 上限 50 条 payload；超过截断（不发请求）
  - Ctrl+C 立即返回已收集结果 + interrupted=True
  - 任何单条请求异常都被吞掉为 ExecResult.error，不影响后续 payload
"""
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from poker.capabilities.redteam import PayloadResult
from poker.capabilities.redteam.endpoints import Endpoint

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_COUNT = 50


@dataclass
class ExecResult:
    payload: str
    category: str
    intent: str
    status_code: int = 0
    response_text: str = ""
    latency_ms: float = 0.0
    error: str = ""
    ts: str = ""


def execute_payloads(
    payloads: list[PayloadResult],
    endpoint: Endpoint,
    api_key: str,
    system_prompt: str,
    *,
    max_count: int = DEFAULT_MAX_COUNT,
    timeout: float = DEFAULT_TIMEOUT,
    client: Any = None,
    on_progress: Optional[Callable[[int, int, ExecResult], None]] = None,
) -> tuple[list[ExecResult], bool]:
    """同步迭代执行 payload。

    返回 (results, interrupted)：
      - results：成功执行（含错误响应）的 ExecResult 列表
      - interrupted：True 表示用户 Ctrl+C 中止；调用方决定如何展示

    超过 max_count 的 payload 被截断不发；调用方应在 truncate 前提示用户。
    """
    truncated = list(payloads[:max_count])
    results: list[ExecResult] = []
    interrupted = False

    own_client = client is None
    if own_client:
        try:
            import httpx
        except ImportError as e:
            raise RuntimeError("需要 httpx：pip install httpx") from e
        client = httpx.Client(timeout=timeout)

    interval = 1.0 / endpoint.rate_limit if endpoint.rate_limit > 0 else 0.0

    try:
        for i, p in enumerate(truncated):
            if i > 0 and interval > 0:
                try:
                    time.sleep(interval)
                except KeyboardInterrupt:
                    interrupted = True
                    break
            try:
                result = _send_one(client, endpoint, api_key, system_prompt, p)
            except KeyboardInterrupt:
                interrupted = True
                break
            results.append(result)
            if on_progress is not None:
                try:
                    on_progress(i + 1, len(truncated), result)
                except Exception:
                    pass
    finally:
        if own_client:
            try:
                client.close()
            except Exception:
                pass

    return results, interrupted


def _send_one(
    client: Any, endpoint: Endpoint, api_key: str, system_prompt: str, p: PayloadResult,
) -> ExecResult:
    """发一条请求；任何异常（除 KeyboardInterrupt）都吞为 ExecResult.error。"""
    started = time.perf_counter()
    ts = datetime.now(timezone.utc).isoformat()

    body = {
        "model": endpoint.model or "default",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": p.payload},
        ],
        "max_tokens": 500,
        "temperature": 0.0,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = client.post(endpoint.url, json=body, headers=headers)
    except KeyboardInterrupt:
        raise
    except Exception as e:
        latency = (time.perf_counter() - started) * 1000
        return ExecResult(
            payload=p.payload, category=p.category, intent=p.intent,
            status_code=0, response_text="",
            latency_ms=latency, error=_classify_error(e), ts=ts,
        )

    latency = (time.perf_counter() - started) * 1000
    status = getattr(resp, "status_code", 0)
    if status >= 400:
        return ExecResult(
            payload=p.payload, category=p.category, intent=p.intent,
            status_code=status,
            response_text=getattr(resp, "text", "")[:500],
            latency_ms=latency, error=f"HTTP {status}", ts=ts,
        )

    try:
        data = resp.json()
    except Exception as e:
        return ExecResult(
            payload=p.payload, category=p.category, intent=p.intent,
            status_code=status, response_text="",
            latency_ms=latency, error=f"json decode: {e}", ts=ts,
        )

    text = ""
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        text = str(data)[:500]
    return ExecResult(
        payload=p.payload, category=p.category, intent=p.intent,
        status_code=status, response_text=text,
        latency_ms=latency, error="", ts=ts,
    )


def _classify_error(e: Exception) -> str:
    """超时统一标 'timeout'，便于 UI 区分；其余保留类型 + 消息。"""
    err_name = type(e).__name__
    msg = str(e) or err_name
    if "timeout" in err_name.lower() or "timeout" in msg.lower():
        return "timeout"
    return f"{err_name}: {msg}"[:200]

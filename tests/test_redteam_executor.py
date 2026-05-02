"""Tests for poker.capabilities.redteam.executor (mocked httpx)."""
import time

import httpx
import pytest

from poker.capabilities.redteam import PayloadResult
from poker.capabilities.redteam.endpoints import Endpoint
from poker.capabilities.redteam.executor import DEFAULT_MAX_COUNT, execute_payloads


# ---------- mocks ----------

class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {
            "choices": [{"message": {"content": "OK"}}]
        }
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    """记录调用 + 可配置每次行为。"""

    def __init__(self, responder=None):
        self.responder = responder or (lambda body: _FakeResponse())
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.responder(json)

    def close(self):
        pass


def _make_payloads(n: int):
    return [
        PayloadResult(category="jailbreak", payload=f"attack {i}", intent="test")
        for i in range(n)
    ]


# ---------- 上限截断 ----------

def test_executor_truncates_to_max_count():
    payloads = _make_payloads(60)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=10000.0)
    client = _FakeClient()
    results, _ = execute_payloads(payloads, ep, "k", "sys", client=client)
    assert len(results) == DEFAULT_MAX_COUNT
    assert len(client.calls) == DEFAULT_MAX_COUNT


def test_executor_accepts_smaller_counts():
    payloads = _make_payloads(3)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=10000.0)
    client = _FakeClient()
    results, _ = execute_payloads(payloads, ep, "k", "sys", client=client)
    assert len(results) == 3


# ---------- 限速 ----------

def test_executor_respects_rate_limit_interval():
    """3 个 payload，rate=20 req/s（间隔 50ms），至少 2 * 0.05 = 0.10s。"""
    payloads = _make_payloads(3)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=20.0)
    client = _FakeClient()
    started = time.perf_counter()
    results, _ = execute_payloads(payloads, ep, "k", "sys", client=client)
    elapsed = time.perf_counter() - started
    assert len(results) == 3
    assert elapsed >= 0.08, f"expected >= 0.08s elapsed, got {elapsed:.3f}s"


# ---------- 超时 / 错误 ----------

def test_executor_handles_timeout():
    payloads = _make_payloads(2)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)

    def responder(body):
        raise httpx.TimeoutException("read timeout")
    client = _FakeClient(responder=responder)

    results, _ = execute_payloads(payloads, ep, "k", "sys", client=client)
    assert len(results) == 2
    assert all(r.error == "timeout" for r in results)
    assert all(r.status_code == 0 for r in results)


def test_executor_continues_after_single_failure():
    """一条失败不影响其他 payload。"""
    payloads = _make_payloads(3)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)
    counter = {"n": 0}

    def responder(body):
        counter["n"] += 1
        if counter["n"] == 2:
            raise httpx.ConnectError("conn refused")
        return _FakeResponse()
    client = _FakeClient(responder=responder)

    results, _ = execute_payloads(payloads, ep, "k", "sys", client=client)
    assert len(results) == 3
    assert results[0].error == ""
    assert "Connect" in results[1].error
    assert results[2].error == ""


def test_executor_handles_http_4xx_5xx():
    payloads = _make_payloads(1)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)
    client = _FakeClient(responder=lambda b: _FakeResponse(status_code=503, text="overloaded"))
    results, _ = execute_payloads(payloads, ep, "k", "sys", client=client)
    assert results[0].status_code == 503
    assert "HTTP 503" in results[0].error
    assert "overloaded" in results[0].response_text


# ---------- 成功路径 ----------

def test_executor_extracts_message_content():
    payloads = _make_payloads(1)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)
    client = _FakeClient(responder=lambda b: _FakeResponse(payload={
        "choices": [{"message": {"content": "Hello attacker"}}]
    }))
    results, _ = execute_payloads(payloads, ep, "k", "sys", client=client)
    assert results[0].response_text == "Hello attacker"
    assert results[0].error == ""
    assert results[0].latency_ms >= 0


def test_executor_includes_system_and_user_messages():
    payloads = _make_payloads(1)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)
    client = _FakeClient()
    execute_payloads(payloads, ep, "k", "MY-SYSTEM", client=client)
    body = client.calls[0]["json"]
    assert body["messages"][0] == {"role": "system", "content": "MY-SYSTEM"}
    assert body["messages"][1]["role"] == "user"
    assert body["messages"][1]["content"] == "attack 0"


def test_executor_authorization_header():
    payloads = _make_payloads(1)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)
    client = _FakeClient()
    execute_payloads(payloads, ep, "sk-test-key", "sys", client=client)
    headers = client.calls[0]["headers"]
    assert headers["Authorization"] == "Bearer sk-test-key"
    assert headers["Content-Type"] == "application/json"


def test_executor_uses_endpoint_model():
    payloads = _make_payloads(1)
    ep = Endpoint(name="x", url="http://localhost", model="my-model", rate_limit=1000.0)
    client = _FakeClient()
    execute_payloads(payloads, ep, "k", "sys", client=client)
    assert client.calls[0]["json"]["model"] == "my-model"


# ---------- Ctrl+C ----------

def test_executor_keyboardinterrupt_returns_partial():
    """Ctrl+C 在第 3 次调用时触发；前 2 次结果应保留。"""
    payloads = _make_payloads(5)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)
    counter = {"n": 0}

    def responder(body):
        counter["n"] += 1
        if counter["n"] == 3:
            raise KeyboardInterrupt
        return _FakeResponse()
    client = _FakeClient(responder=responder)

    results, interrupted = execute_payloads(payloads, ep, "k", "sys", client=client)
    assert interrupted is True
    assert len(results) == 2  # 第 3 次未 append


# ---------- progress callback ----------

def test_executor_progress_callback():
    payloads = _make_payloads(3)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)
    client = _FakeClient()
    progress_calls: list[tuple] = []

    def cb(i, total, result):
        progress_calls.append((i, total, result.error))

    execute_payloads(
        payloads, ep, "k", "sys", client=client, on_progress=cb,
    )
    assert progress_calls == [(1, 3, ""), (2, 3, ""), (3, 3, "")]


def test_executor_progress_callback_exception_swallowed():
    """progress callback 抛异常不应影响主流程。"""
    payloads = _make_payloads(2)
    ep = Endpoint(name="x", url="http://localhost", rate_limit=1000.0)
    client = _FakeClient()

    def bad_cb(i, total, result):
        raise RuntimeError("oops")

    results, _ = execute_payloads(
        payloads, ep, "k", "sys", client=client, on_progress=bad_cb,
    )
    assert len(results) == 2

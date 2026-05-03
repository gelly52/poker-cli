"""OpenTelemetry 兼容层（可选）。

本模块只做 dict → dict 的"OTel span 风格"形式转换，**不依赖 opentelemetry-sdk** —— 用户
可以拿返回值喂给真正的 OTel exporter，也可以只用结构去做调试。

OTel 风格字段：
- name             span name
- trace_id         由 run_id 充当（不是真正的 OTel trace_id 编码，但唯一即可）
- parent_span_id   由 parent_run_id 充当
- start_time       事件 ts
- attributes       扁平 string -> primitive 的 kv，符合 OTel attribute 约定
"""
from typing import Any


def to_otel_span(event: dict) -> dict:
    """把一条 PokerCallbackHandler 写出的事件 dict 转 OTel span 风格 dict。

    输入 event 字段：ts / project / kind / run_id / parent_run_id / payload / detections。
    返回的 dict 可直接 push 到自家 OTel collector / Jaeger / Tempo。
    """
    if not isinstance(event, dict):
        return {"name": "poker.runtime.invalid", "attributes": {}}

    detections = event.get("detections") or []
    payload = event.get("payload") or {}

    attrs: dict[str, Any] = {
        "poker.project": str(event.get("project") or ""),
        "poker.kind": str(event.get("kind") or ""),
        "poker.detections.count": len(detections),
    }

    # detections 的前 5 条扁平化进 attributes
    for i, d in enumerate(detections[:5]):
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            if isinstance(v, (str, int, float, bool)):
                attrs[f"poker.detection.{i}.{k}"] = v

    # payload 里 primitive 字段进 attributes（list/dict 跳过避免 OTel 拒收）
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(v, (str, int, float, bool)):
                attrs[f"poker.payload.{k}"] = v
            elif isinstance(v, dict):
                for sk, sv in v.items():
                    if isinstance(sv, (str, int, float, bool)):
                        attrs[f"poker.payload.{k}.{sk}"] = sv

    return {
        "name": str(event.get("kind") or "poker.runtime"),
        "trace_id": str(event.get("run_id") or ""),
        "parent_span_id": str(event.get("parent_run_id") or ""),
        "start_time": str(event.get("ts") or ""),
        "attributes": attrs,
    }

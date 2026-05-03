"""自动记忆：跨会话持久化 chat 历史、findings、audits、triages、audit log。

存储位置：~/.poker/state/<project_hash>/
project_hash 由 project_root 的 abspath sha256 取前 12 位，确保同一项目稳定映射。

模块只负责"读写"这一职责，不掺杂业务逻辑；调用方（cli / capabilities）负责何时触发。
"""
import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ---------- 路径常量 ----------

_STATE_ROOT_NAME = ".poker"
_STATE_SUBDIR = "state"

_CHAT_FILE = "chat_history.jsonl"
_FINDINGS_LATEST = "last_scan.json"
_FINDINGS_HISTORY = "findings_history.jsonl"
_AUDITS_DIR = "audits"
_TRIAGES_FILE = "triages.json"
_AUDIT_LOG = "audit.jsonl"
_BACKUPS_DIR = "backups"
_REDTEAM_DIR = "redteam"
_INVESTIGATIONS_DIR = "investigations"
_THREAT_MODEL_DIR = "threat_models"
_MULTI_AGENT_DIR = "multi_agent_runs"

_VALID_TRIAGE_STATES = frozenset({"accepted", "ignored", "fixed"})


# ---------- 路径工具 ----------

def project_hash(project_root: Path) -> str:
    """对 project_root 的 abspath 取 sha256 前 12 位。"""
    abspath = str(project_root.resolve())
    return hashlib.sha256(abspath.encode("utf-8")).hexdigest()[:12]


def get_state_dir(project_root: Path) -> Path:
    """返回 ~/.poker/state/<hash>/，确保存在。"""
    state_dir = Path.home() / _STATE_ROOT_NAME / _STATE_SUBDIR / project_hash(project_root)
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------- chat 历史 ----------

def append_chat(
    project_root: Path,
    role: str,
    content: str,
    session_id: str | None = None,
) -> None:
    """追加一条 chat 历史。role ∈ {user, assistant, system}。

    session_id 持久化到 jsonl，使 load_chat_sessions 能按"会话归属"而非纯时间 gap 切窗口。
    `/resume` 切到旧 session 后追加的对话仍归到原 session_id，下次打开能正确接续。
    """
    path = get_state_dir(project_root) / _CHAT_FILE
    record: dict = {"ts": _now_iso(), "role": role, "content": content}
    if session_id:
        record["session_id"] = session_id
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_chat(project_root: Path, limit: int = 50) -> list[dict]:
    """加载历史聊天，返回最近 limit 条（按时间正序）。"""
    path = get_state_dir(project_root) / _CHAT_FILE
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    records: list[dict] = []
    for line in lines[-limit:]:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def load_chat_sessions(project_root: Path, gap_minutes: int = 30) -> list[dict]:
    """切分 chat_history.jsonl 成多个上下文窗口；最新在前。

    优先按 record.session_id 分组（新格式）；无 session_id 字段的旧 record
    回落到时间 gap 切（向后兼容）。如果 legacy group 的首条 ts 跟某个 session_id
    相同，自动合并到一起 —— 这种情况发生在用户先用旧格式聊过、之后某次 `/resume`
    选中那段历史并继续追加的场景。

    每个 session: {id, start_ts, preview, messages: list[record]}。
    """
    records = load_chat(project_root, limit=10_000)
    if not records:
        return []

    by_session: dict[str, list[dict]] = {}
    legacy: list[dict] = []
    for r in records:
        sid = r.get("session_id")
        if sid:
            by_session.setdefault(sid, []).append(r)
        else:
            legacy.append(r)

    for group in _split_legacy_by_gap(legacy, gap_minutes):
        first_ts = group[0].get("ts", "") if group else ""
        if not first_ts:
            continue
        # 同 ts 已存在 by_session（即 /resume 后追加的延续）→ legacy 在前合并
        by_session[first_ts] = group + by_session.get(first_ts, [])

    sessions = [_build_session(g) for g in by_session.values() if g]
    sessions.sort(key=lambda s: s["start_ts"], reverse=True)
    return sessions


def _split_legacy_by_gap(records: list[dict], gap_minutes: int) -> list[list[dict]]:
    """按时间 gap 切 legacy（无 session_id）records；旧逻辑保留。"""
    if not records:
        return []
    gap = timedelta(minutes=gap_minutes)
    groups: list[list[dict]] = [[records[0]]]
    for prev, curr in zip(records, records[1:]):
        try:
            prev_ts = datetime.fromisoformat(prev["ts"])
            curr_ts = datetime.fromisoformat(curr["ts"])
        except (KeyError, ValueError):
            groups[-1].append(curr)
            continue
        if curr_ts - prev_ts > gap:
            groups.append([curr])
        else:
            groups[-1].append(curr)
    return groups


def _build_session(records: list[dict]) -> dict:
    first = records[0]
    # session_id 字段优先（新格式）；缺失回落到首条 ts（旧格式 / 兼容路径）
    sid = first.get("session_id") or first.get("ts", "")
    first_user = next((r for r in records if r.get("role") == "user"), first)
    preview = (first_user.get("content") or "").strip().splitlines()[0] if first_user else ""
    return {
        "id": sid,
        "start_ts": first.get("ts", ""),
        "preview": preview[:60] or "(空)",
        "messages": records,
    }


# ---------- findings ----------

def save_findings(project_root: Path, findings: list[Any]) -> None:
    """覆盖写 last_scan.json + 追加 findings_history.jsonl。

    findings 元素期望有 to_dict() 方法（如 Finding），或本身是 dict。
    """
    state_dir = get_state_dir(project_root)
    payload = {
        "ts": _now_iso(),
        "count": len(findings),
        "findings": [_to_dict(f) for f in findings],
    }
    (state_dir / _FINDINGS_LATEST).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (state_dir / _FINDINGS_HISTORY).open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def load_last_findings(project_root: Path) -> list[dict]:
    """读取最近一次扫描结果。"""
    path = get_state_dir(project_root) / _FINDINGS_LATEST
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("findings", [])
    except json.JSONDecodeError:
        return []


# ---------- audits ----------

def save_audit(project_root: Path, dimension: str, target: str, result: dict) -> Path:
    """保存一次 audit 结果到 audits/<dim>_<target>_<ts>.json，返回写入的文件路径。"""
    audits_dir = get_state_dir(project_root) / _AUDITS_DIR
    audits_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    safe_target = _safe_filename(target)
    path = audits_dir / f"{dimension}_{safe_target}_{ts}.json"
    payload = {
        "ts": _now_iso(),
        "dimension": dimension,
        "target": target,
        "result": result,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# ---------- triages ----------

def set_triage(project_root: Path, finding_id: str, triage_state: str) -> None:
    """设置 finding 处置：accepted | ignored | fixed。"""
    if triage_state not in _VALID_TRIAGE_STATES:
        raise ValueError(f"invalid triage state: {triage_state}")
    path = get_state_dir(project_root) / _TRIAGES_FILE
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    data[finding_id] = {"state": triage_state, "ts": _now_iso()}
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_triages(project_root: Path) -> dict:
    """读取所有 triage 记录。"""
    path = get_state_dir(project_root) / _TRIAGES_FILE
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# ---------- audit log ----------

def append_audit_log(project_root: Path, event: dict) -> None:
    """追加一条审计日志。event 是任意可 JSON 化的 dict。"""
    path = get_state_dir(project_root) / _AUDIT_LOG
    record = {"ts": _now_iso(), **event}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


# ---------- backups ----------

def save_backup(project_root: Path, file_path: Path) -> Path:
    """备份原文件到 backups/<ISO_ts>_<filename>。

    file_path 不存在时（新建文件场景）写一个 0 字节占位备份并标记。
    时间戳用 ISO 紧凑格式（YYYYMMDDTHHMMSSZ）以兼容 Windows 文件名。
    扩展名保留（如 README.md → 备份名含 .md），便于人工辨识。
    返回备份文件路径。
    """
    backups_dir = get_state_dir(project_root) / _BACKUPS_DIR
    backups_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = _safe_filename(file_path.stem) if file_path.stem else "_"
    suffix = "".join(c for c in file_path.suffix if c.isalnum() or c == ".")
    backup_path = backups_dir / f"{ts}_{stem}{suffix}"
    if file_path.exists() and file_path.is_file():
        backup_path.write_bytes(file_path.read_bytes())
    else:
        backup_path.write_bytes(b"")  # 占位：标记原文件不存在
    return backup_path


# ---------- redteam ----------

def save_redteam_run(project_root: Path, prompt_name: str, payload: dict) -> Path:
    """把 redteam 执行结果保存到 redteam/<prompt>_<ts>.json。"""
    rt_dir = get_state_dir(project_root) / _REDTEAM_DIR
    rt_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    safe = _safe_filename(prompt_name)
    path = rt_dir / f"{safe}_{ts}.json"
    full = {"ts": _now_iso(), "prompt_name": prompt_name, **payload}
    path.write_text(
        json.dumps(full, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


# ---------- investigations ----------

def save_investigation(project_root: Path, topic: str, markdown: str) -> Path:
    """落盘一次 /investigate 报告到 investigations/<topic>_<ts>.md。

    topic 经文件名清洗（保留字母数字下划线短横，截断到 64）；ts 是 unix 秒。
    返回写入的文件路径。
    """
    inv_dir = get_state_dir(project_root) / _INVESTIGATIONS_DIR
    inv_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    safe_topic = _safe_filename(topic)
    path = inv_dir / f"{safe_topic}_{ts}.md"
    header = f"<!-- topic: {topic}\n     ts: {_now_iso()} -->\n\n"
    path.write_text(header + markdown, encoding="utf-8")
    return path


def load_investigation_records(project_root: Path, limit: int = 5) -> list[dict]:
    """读 investigations/ 最近 limit 条报告（按 mtime 倒序），仅取 topic + 首段摘要。

    返回每条 {path, topic, snippet}；解析失败的条目跳过。
    """
    inv_dir = get_state_dir(project_root) / _INVESTIGATIONS_DIR
    if not inv_dir.exists():
        return []
    files = sorted(
        inv_dir.glob("*.md"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[dict] = []
    for p in files[:limit]:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        topic = _parse_investigation_topic(text)
        snippet = _extract_md_snippet(text)
        out.append({"path": str(p), "topic": topic, "snippet": snippet})
    return out


def _parse_investigation_topic(text: str) -> str:
    """从 save_investigation 写出的 `<!-- topic: ... -->` 注释里提取 topic。"""
    first = text.split("\n", 1)[0]
    marker = "<!-- topic:"
    if first.startswith(marker):
        rest = first[len(marker):]
        if "-->" in rest:
            rest = rest.split("-->")[0]
        return rest.strip()
    return ""


def _extract_md_snippet(text: str, length: int = 400) -> str:
    """跳过头部 HTML 注释，取首个 markdown 章节前 length 字。"""
    if "-->" in text[:512]:
        text = text.split("-->", 1)[1]
    text = text.lstrip()
    return text[:length].rstrip()


# ---------- audit records ----------

def load_audit_records(project_root: Path, limit: int = 20) -> list[dict]:
    """读 audits/ 下所有 audit JSON，返回最近 limit 条（按 mtime 倒序）。"""
    audits_dir = get_state_dir(project_root) / _AUDITS_DIR
    if not audits_dir.exists():
        return []
    files = sorted(
        audits_dir.glob("*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    out: list[dict] = []
    for p in files[:limit]:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append(data if isinstance(data, dict) else {"raw": data})
    return out


# ---------- 聚合 + threat model ----------

def load_all_artifacts(project_root: Path) -> dict:
    """聚合 scan / audit / triage / investigation 产出，供 /threat-model 综合分析。

    返回 {findings, triages, audits, investigations}。任何子项失败都返回空。
    """
    return {
        "findings": load_last_findings(project_root),
        "triages": load_triages(project_root),
        "audits": load_audit_records(project_root, limit=20),
        "investigations": load_investigation_records(project_root, limit=5),
    }


def save_threat_model(project_root: Path, markdown: str) -> Path:
    """落盘一次 /threat-model 报告到 threat_models/<ts>.md。"""
    tm_dir = get_state_dir(project_root) / _THREAT_MODEL_DIR
    tm_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    path = tm_dir / f"{ts}.md"
    header = f"<!-- threat-model\n     ts: {_now_iso()} -->\n\n"
    path.write_text(header + markdown, encoding="utf-8")
    return path


def save_multi_agent_run(project_root: Path, topic: str, markdown: str) -> Path:
    """落盘一次多 Agent 协作调查到 multi_agent_runs/<topic>_<ts>.md。"""
    ma_dir = get_state_dir(project_root) / _MULTI_AGENT_DIR
    ma_dir.mkdir(exist_ok=True)
    ts = int(time.time())
    safe_topic = _safe_filename(topic)
    path = ma_dir / f"{safe_topic}_{ts}.md"
    header = f"<!-- multi-agent\n     topic: {topic}\n     ts: {_now_iso()} -->\n\n"
    path.write_text(header + markdown, encoding="utf-8")
    return path


# ---------- 内部工具 ----------

def _to_dict(obj: Any) -> dict:
    """把 Finding / dict / 其他对象统一转为 dict。"""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return {"value": str(obj)}


def _safe_filename(s: str) -> str:
    """文件名清洗：只保留字母数字下划线短横，最长 64。"""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)[:64] or "_"

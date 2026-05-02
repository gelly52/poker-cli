"""Red team endpoint 白名单 + API key 解析。

设计约束（hard requirements）：
  - URL 不允许直接传给 CLI；只接受 `~/.poker/redteam_endpoints.toml` 中登记的 name
  - URL 必须 http:// 或 https://，其他协议（ftp / file / ws）一律拒绝
  - API key 永远从环境变量 `POKER_REDTEAM_<NAME>_KEY` 读取，配置文件不存 secret
  - 配置文件解析失败、name 不合法、URL 不合法都安静忽略该条，不向调用方泄漏异常细节
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

# 默认配置路径；测试可显式覆盖
DEFAULT_PATH = Path.home() / ".poker" / "redteam_endpoints.toml"

# 名字校验：字母开头 + 字母数字下划线（避免文件名 / env var 注入）
_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")


@dataclass
class Endpoint:
    name: str
    url: str
    model: str = ""
    rate_limit: float = 1.0  # req/s


def load_endpoints(path: Optional[Path] = None) -> dict[str, Endpoint]:
    """读取 TOML，返回 {name: Endpoint}。文件不存在 / 解析失败返回 {}。"""
    p = path if path is not None else DEFAULT_PATH
    if not p.exists() or not p.is_file():
        return {}
    try:
        data = tomllib.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    raw = data.get("endpoints", {})
    if not isinstance(raw, dict):
        return {}

    out: dict[str, Endpoint] = {}
    for name, entry in raw.items():
        if not _NAME_RE.match(name):
            continue
        if not isinstance(entry, dict):
            continue
        url = entry.get("url", "")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        try:
            rate = float(entry.get("rate_limit", 1.0))
        except (TypeError, ValueError):
            rate = 1.0
        if rate <= 0:
            rate = 1.0
        out[name] = Endpoint(
            name=name,
            url=url,
            model=str(entry.get("model", "")),
            rate_limit=rate,
        )
    return out


def resolve_api_key(endpoint_name: str) -> Optional[str]:
    """从环境变量 POKER_REDTEAM_<NAME>_KEY 读取 API key；缺失返回 None。"""
    if not _NAME_RE.match(endpoint_name):
        return None
    var = f"POKER_REDTEAM_{endpoint_name.upper()}_KEY"
    val = os.environ.get(var, "").strip()
    return val or None


def env_var_name(endpoint_name: str) -> str:
    """返回该 endpoint 对应的 env var 名，便于 UI 提示。"""
    return f"POKER_REDTEAM_{endpoint_name.upper()}_KEY"

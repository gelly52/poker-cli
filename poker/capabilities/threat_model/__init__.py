"""capabilities/threat_model：基于 scan / audit / triage / investigation 已有产出，
让 LLM 输出 STRIDE 风格威胁模型 markdown 报告。

主入口 `run_threat_model`：
1. `state.load_all_artifacts` 聚合所有产出
2. 没素材 → 友好提示先做基础调查
3. `_summarize_artifacts` 把素材压成 token 友好的摘要（findings 按 severity 取 top-N
   并标"已截取"，audit / investigation 摘要保留最近 N 条）
4. 用 `stream_agent_long` 跑（注入 STRIDE_SYSTEM_PROMPT，max_rounds=3，无 capability
   工具——素材已聚合，避免 LLM 跑偏；保留默认 read 类工具供必要时验证）
5. 落盘 `.poker/state/<hash>/threat_models/<ts>.md`，KeyboardInterrupt / 异常都把
   已生成内容落盘
"""
import time
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from poker.agent.runtime import stream_agent_long
from poker.capabilities.explain import compute_finding_id
from poker.state import load_all_artifacts, save_threat_model

_FINDINGS_TOP_N = 30
_MAX_ROUNDS = 3
_SEVERITY_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}


THREAT_MODEL_SYSTEM_PROMPT = """\
你是 Poker CLI 的安全威胁建模助手。

工作模式：
1. 用户已经跑过 scan / audit / investigation；你拿到所有产出的精简摘要
2. 基于这些素材，输出 STRIDE 6 大类威胁模型 markdown 报告
3. **6 类必须全覆盖**：即使某类没找到风险也明确写出"未发现明显风险"
4. 引用 finding 必须用 8 位短 hash ID（来自素材摘要里的反引号 ID）
5. 不要编造素材里不存在的 finding ID

风险按严重等级排列：critical > high > medium > low > info；不确定的明确标注。\
"""


def has_artifacts(artifacts: dict) -> bool:
    """判断是否有任意产出可供建模。"""
    return bool(
        (artifacts.get("findings") or [])
        or (artifacts.get("audits") or [])
        or (artifacts.get("investigations") or [])
    )


def _summarize_artifacts(artifacts: dict) -> tuple[str, list[str]]:
    """构造给 LLM 的素材摘要文本 + 截断说明列表。"""
    notes: list[str] = []
    parts: list[str] = []

    # ---- findings: 按 severity 取 top-N ----
    findings = list(artifacts.get("findings") or [])
    triages = artifacts.get("triages") or {}

    if findings:
        sorted_findings = sorted(
            findings,
            key=lambda f: _SEVERITY_RANK.get(str(f.get("severity", "")).lower(), 0),
            reverse=True,
        )
        original_count = len(sorted_findings)
        if original_count > _FINDINGS_TOP_N:
            notes.append(
                f"finding 共 {original_count} 条，已按 severity 取 top {_FINDINGS_TOP_N}"
            )
            sorted_findings = sorted_findings[:_FINDINGS_TOP_N]

        f_lines = [f"## Findings（{len(sorted_findings)}/{original_count}）"]
        for f in sorted_findings:
            fid = compute_finding_id(f)
            triage_state = (triages.get(fid) or {}).get("state", "")
            triage_tag = f" [triage={triage_state}]" if triage_state else ""
            f_lines.append(
                f"- `{fid}` [{f.get('severity', '')}] {f.get('rule_id', '')} "
                f"@ {f.get('path', '')}:{f.get('line', '')} — {f.get('title', '')}"
                f"{triage_tag}"
            )
        parts.append("\n".join(f_lines))
    else:
        parts.append("## Findings\n（无）")

    # ---- triages 总览 ----
    if triages:
        counts: dict[str, int] = {}
        for v in triages.values():
            s = (v or {}).get("state", "?")
            counts[s] = counts.get(s, 0) + 1
        parts.append(
            "## Triage 总览\n"
            + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        )

    # ---- audits ----
    audits = artifacts.get("audits") or []
    if audits:
        a_lines = [f"## Audit 记录（最近 {len(audits)}）"]
        for a in audits:
            dim = a.get("dimension", "?")
            tgt = a.get("target", "?")
            ts = str(a.get("ts", ""))[:19]
            result = a.get("result") or {}
            sev = result.get("overall_severity") or result.get("severity") or ""
            risks = result.get("risks") or []
            risk_titles = "; ".join(
                str((r or {}).get("title", "")) for r in risks[:3] if r
            )
            extra = f"  risks=[{risk_titles}]" if risk_titles else ""
            a_lines.append(
                f"- [{dim}] target={tgt}  severity={sev}  ts={ts}{extra}"
            )
        parts.append("\n".join(a_lines))

    # ---- investigations ----
    invs = artifacts.get("investigations") or []
    if invs:
        i_lines = [f"## Investigation 记录（最近 {len(invs)}）"]
        for inv in invs:
            topic = inv.get("topic", "?") or "?"
            snippet = (inv.get("snippet") or "").splitlines()
            head = next((s for s in snippet if s.strip()), "")[:160]
            i_lines.append(f"- topic: {topic}  -  {head}")
        parts.append("\n".join(i_lines))

    return "\n\n".join(parts), notes


def _build_user_prompt(summary: str, notes: list[str]) -> str:
    notes_block = ""
    if notes:
        notes_block = (
            "\n\n**素材截断说明（这些条目你看不到，但请在报告里说明可能存在的盲区）：**\n"
            + "\n".join(f"- {n}" for n in notes)
        )

    return f"""\
基于下面已有的安全产出，给我一份 STRIDE 风格威胁模型 markdown 报告。

# 已有素材

{summary}{notes_block}

# 报告 markdown 格式（严格遵循；6 类必须全覆盖）

# 威胁模型：STRIDE 分析

## 目录
- [概述](#概述)
- [资产与信任边界](#资产与信任边界)
- [STRIDE 分析](#stride-分析)
  - [Spoofing 身份伪造](#spoofing-身份伪造)
  - [Tampering 篡改](#tampering-篡改)
  - [Repudiation 抵赖](#repudiation-抵赖)
  - [Information Disclosure 信息泄露](#information-disclosure-信息泄露)
  - [Denial of Service 拒绝服务](#denial-of-service-拒绝服务)
  - [Elevation of Privilege 权限提升](#elevation-of-privilege-权限提升)
- [风险矩阵](#风险矩阵)
- [缓解优先级](#缓解优先级)

## 概述
（项目类型、扫描范围、整体风险水位、有无被截断的素材）

## 资产与信任边界
（关键资产、信任边界、外部输入入口）

## STRIDE 分析

### Spoofing 身份伪造
**相关 finding**：
- `<8 位 ID>` @ path:line — 一句话摘要
（如无：写"未发现明显 Spoofing 风险"）

**风险评估**：critical / high / medium / low / info + 推理

**缓解建议**：可落地动作

### Tampering 篡改
（同上格式）

### Repudiation 抵赖
（同上格式）

### Information Disclosure 信息泄露
（同上格式）

### Denial of Service 拒绝服务
（同上格式）

### Elevation of Privilege 权限提升
（同上格式）

## 风险矩阵
| 威胁类别 | 严重等级 | 受影响 finding | 缓解优先级 |
|---|---|---|---|
| Spoofing | ... | ... | P0/P1/P2/P3 |
| Tampering | ... | ... | ... |
| Repudiation | ... | ... | ... |
| Information Disclosure | ... | ... | ... |
| Denial of Service | ... | ... | ... |
| Elevation of Privilege | ... | ... | ... |

## 缓解优先级
按 P0 / P1 / P2 / P3 分组列出动作，每条尽量绑定具体 finding ID。

# 严格要求
- 6 类必须全覆盖；引用 finding 用反引号包裹的 8 位短 hash ID
- 风险矩阵必须 6 行，没风险的类别填 "-"
- 不要列素材里没有的 finding ID
- 截断素材造成的盲区要在"概述"里点出来
"""


def run_threat_model(
    project_root: Path,
    llm: Any,
    console: Console,
) -> Path | None:
    """主入口：聚合产出 → LLM 综合分析 → 落盘 markdown 报告。"""
    if llm is None:
        console.print("[red]未配置 LLM；/threat-model 需要 API key[/red]")
        return None

    artifacts = load_all_artifacts(project_root)
    if not has_artifacts(artifacts):
        console.print(
            "[yellow]还没有任何产出可供建模；先做基础调查："
            "[cyan]/scan[/cyan]、[cyan]/audit tools[/cyan]、"
            "[cyan]/investigate[/cyan][/yellow]"
        )
        return None

    summary, notes = _summarize_artifacts(artifacts)
    for n in notes:
        console.print(f"[dim]素材截断: {n}[/dim]")

    user_prompt = _build_user_prompt(summary, notes)
    text = Text()
    title_base = "Threat Model"
    interrupted = False
    error: Exception | None = None
    session_id = f"threat-model-{int(time.time())}"

    try:
        with Live(
            Panel(text, title=title_base, border_style="magenta"),
            console=console,
            refresh_per_second=8,
        ) as live:
            for token, _, round_idx in stream_agent_long(
                llm,
                user_prompt,
                session_id=session_id,
                max_rounds=_MAX_ROUNDS,
                system_prompt=THREAT_MODEL_SYSTEM_PROMPT,
            ):
                text.append(token)
                title = (
                    f"{title_base} · Round {round_idx}" if round_idx > 1 else title_base
                )
                live.update(Panel(text, title=title, border_style="magenta"))
    except KeyboardInterrupt:
        interrupted = True
    except Exception as e:
        error = e

    report_md = str(text).strip()

    if interrupted:
        console.print("\n[yellow][已中断]  已生成部分将落盘[/yellow]")
    elif error is not None:
        console.print(f"\n[red]建模异常 ({error})；已生成部分将落盘[/red]")

    if not report_md:
        console.print("[yellow]未生成任何报告内容；不落盘[/yellow]")
        return None

    try:
        path = save_threat_model(project_root, report_md)
    except Exception as e:
        console.print(f"[red]报告落盘失败: {e}[/red]")
        return None

    console.print(f"[green]威胁模型已落盘:[/green] [cyan]{path}[/cyan]")
    return path

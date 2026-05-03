# Poker CLI · Command Reference

REPL 三类输入分发：

| 输入 | 含义 |
|---|---|
| `/<cmd>` | poker 内置命令（本文档） |
| `!<cmd>` | 透传给 bash 的 shell 命令；`cd` 跨调用自动持久化 |
| 其他 | chat — 跟安全 Agent 对话（`stream_agent_long` 长链路推理） |

> chat 末尾加 ` --simple` 可强制走单轮 stream_agent，跳过反思。
> chat **不会**自动走多 Agent；那是 `/investigate` 才会做的事。

所有命令在 REPL（`poker` 启动后）和 Typer 一次性命令（`poker scan ...`）中表现一致，除非另有说明。

---

## 命令一览

| 命令 | 用途 | 落盘位置（`~/.poker/state/<hash>/...`） |
|---|---|---|
| [`/scan`](#scan)                   | 全项目宽扫，按 severity 分组 | `last_scan.json` + `findings_history.jsonl` |
| [`/audit`](#audit)                 | 多维度深度审计 | `audits/<dim>_<target>_<ts>.json` |
| [`/redteam`](#redteam)             | 对 prompt 生成 / 执行攻击载荷 | `redteam/<prompt>_<ts>.json`（仅 --execute） |
| [`/trace`](#trace)                 | 数据流追踪到危险 sink | — |
| [`/explain`](#explain)             | 用项目上下文解释 finding | — |
| [`/triage`](#triage)               | LLM 协助逐条决策 finding | `triages.json` |
| [`/investigate`](#investigate)     | Agent 自主综合调查 | `investigations/<topic>_<ts>.md` 或 `multi_agent_runs/<topic>_<ts>.md` |
| [`/threat-model`](#threat-model)   | 综合产出输出 STRIDE 报告 | `threat_models/<ts>.md` |
| [`/resume`](#resume)               | 回到历史会话窗口 | — |
| [`/config`](#config)               | 显示 / 检查配置 | — |
| [`/help`](#help)                   | 命令清单 | — |
| [`/exit` · `/quit`](#exit--quit)   | 退出 REPL | — |

---

## /scan

宽而浅扫一遍：跑所有 detector，按 severity 分组渲染表格，第一列是 finding 短 hash ID（喂给 `/explain` 用）。

**用法**

```
/scan [path] [--quiet | -q] [--verbose | -v]
```

| 参数 | 说明 |
|---|---|
| `path` | 文件 / 目录，默认当前 tracked cwd |
| `--quiet` `-q` | 只显示 critical / high |
| `--verbose` `-v` | 显示全部含 info（默认排除 info） |

**示例**

```
/scan
/scan tests/e2e/sample_project/secrets_demo
/scan --quiet
```

**Typer 子命令**: `poker scan <path> --format table|json|markdown --output <file> --fail-on high`

**落盘**：每次都把全量 findings 写到 `last_scan.json` + 追加 `findings_history.jsonl`，与 `--quiet/--verbose` 显示策略无关。

---

## /audit

交互式深度审计指定维度。

**用法**

```
/audit <dimension> [--schema <path>]
```

| 维度 | 说明 |
|---|---|
| `tools` | 找 `@tool` / `Tool()` / OpenAI function schema → 静态风险检查 + LLM 模糊度评估 |
| `rag` | 识别 vectorstore / retriever / loader → 检查信任度、净化、chunk size |
| `mcp` | 解析 `.mcp.json` / `Server(...)` → 权限 / shell wrapper / 通配 tool / 明文 secret |
| `prompt` | 扫文本 + AST `SystemMessage` / `ChatPromptTemplate` → injection 抗性 |
| `mcp_schema` | 纯静态 JSON Schema 规则审计（10 条规则数据驱动），需要 `--schema <path>` |

**示例**

```
/audit tools
/audit rag
/audit mcp_schema --schema tools-config.json
```

**落盘**：`audits/<dim>_<target>_<ts>.json`，含每条 risk + LLM 摘要。

---

## /redteam

对 prompt 文件生成攻击载荷；可选实际执行。

**用法**

```
/redteam <prompt-file> [--execute --endpoint <name>]
```

| 参数 | 说明 |
|---|---|
| `<prompt-file>` | 项目内的 prompt 文件路径（必须在 project root 内） |
| `--execute` | 实际调 endpoint（**默认不调，仅生成**） |
| `--endpoint <name>` | endpoint 名称，必须在 `~/.poker/redteam_endpoints.toml` 白名单中 |

**安全约束**

- endpoint 仅 http(s)，必须显式白名单
- API key 仅从环境变量取，不接受配置写死
- `--execute` 触发完整 phrase **`yes execute attacks`** 二次确认
- 限速 + 30s 超时 + 50 条上限 + Ctrl+C 立即返回部分结果

**示例**

```
/redteam prompts/system.md
/redteam prompts/system.md --execute --endpoint my-llm
```

**落盘**：仅 `--execute` 模式落 `redteam/<prompt>_<ts>.json` + `audit.jsonl` `redteam_execute` 事件。

---

## /trace

数据流追踪：从某个变量起，沿赋值 / 拼接 / 传参链路追踪，命中 `subprocess` / `eval` / `cursor.execute` / 文件写入等危险 sink 时标 ⚠。

**用法**

```
/trace <file:line:variable>
```

跨函数（按形参映射）+ 跨文件（解 import 链）；最大深度 10，访问过的 `(file, func, frozenset(tainted))` 不二次追，避免循环。

**示例**

```
/trace agent.py:21:user_input
/trace src/handlers/api.py:45:request_body
```

输出每个 hop（行号 + 操作 + 代码片段）+ 最终判断（`safe / warn / danger`）+ 修复建议。

---

## /explain

用项目上下文解释某条 finding 在你代码里**具体**怎么被触发，输出"触发路径 / 影响范围 / 修复建议（针对本项目）"三段式 markdown，**复用 `stream_agent_long` 长链路**让 LLM 自主调 `read_file` / `search_code` / `git_diff` 验证。

**用法**

```
/explain <finding-id-prefix>
```

| 输入 | 行为 |
|---|---|
| 唯一前缀匹配 | 跑长链路解释 |
| 多匹配 | 列候选表，要求加长前缀 |
| 找不到 | 列最近 5 条 finding 让你挑 |
| 空 ID | 列最近 5 条 |
| 没跑过 scan | 提示先 `/scan` |
| LLM 调用失败 | 退化到原 finding 通用建议 |

**示例**

```
/scan                # 表格第一列就是 8 位 ID
/explain abc12345    # 完整 ID
/explain abc         # 前缀匹配（git checkout 风格）
```

**finding ID 算法**：sha256(`rule_id|path|line|evidence`) 前 8 位；同一 finding 跨次扫描稳定。

---

## /triage

对未 triage 的 finding 逐条决策。LLM 一次性给出 batch 建议（accepted / ignored / fixed + 一句话理由）写在菜单 title 里，你 ↑/↓ 选。

**用法**

```
/triage
```

每条 finding 弹 4 选项菜单：

| 选项 | 写盘 state |
|---|---|
| ✅ accept   | `accepted` — 真问题进 backlog |
| 🙈 ignore   | `ignored`  — 误报 / 测试 fixture / 文档示例 |
| 🛠 fixed    | `fixed`    — 已修复 |
| ⏭ skip     | (不写盘)   — 跳过本次 |

**约束**

- 已 triage 的 finding 不会再问
- LLM 失败 → 退化为无建议人工 triage（功能不阻塞）
- Esc / Ctrl+C → 已选保留，退出循环

**落盘**：每选一条立刻 `state.set_triage` 写到 `triages.json`，所以中断不会丢已选数据。

---

## /investigate

给定主题让 Agent 自主综合调查并出 markdown 报告。**三档分发**。

**用法**

```
/investigate <topic> [--single | --multi]
```

| 模式 | 触发 | 行为 |
|---|---|---|
| **auto**（默认） | 不带 flag | 先调 classifier 一次轻量 LLM 分类（simple / complex），simple → 单 Agent，complex → 多 Agent |
| **single** | `--single` | 强制单 Agent（即使复杂主题） |
| **multi**  | `--multi`  | 强制多 Agent（即使简单主题） |

`--single` 和 `--multi` **互斥**；同时出现报错。classifier 失败 / 输出无法识别 → 默默退化 single，绝不抛栈。

### 单 Agent 路径

`stream_agent_long(max_rounds=8, tools=get_investigate_tools())`，工具预算 30 次。capability 工具：

- `run_scan_tool(target="")` — 扫整个项目或子路径
- `run_audit_tool(dimension, target="")` — 维度 ∈ tools/rag/mcp/prompt
- `run_trace_tool(file:line:var)` — 数据流追踪
- `read_findings_tool()` — 读最近 scan 全量

外加常规读项目工具（read_file / list_files / search_code / git_diff 等）。**不暴露 write_file / apply_patch**（调查只读）。

### 多 Agent 路径（4 角色）

| 角色 | 行为 |
|---|---|
| **Planner** | 单轮 LLM 拆 ≤5 个独立子任务（JSON） |
| **Investigators** × N | `ThreadPoolExecutor` 并发跑，每个独立 budget=15、独立 session_id |
| **Critic** | **只一轮反馈**，对每个 Investigator 提 2-3 个关键问题，不无限往复 |
| **Synthesizer** | 合并所有 Investigator 产出 + critique 输出最终 markdown |

任一 Investigator 失败 → 报告标 `[Investigator <id>: 失败 - <err>]` 其他继续。Ctrl+C / 任何阶段抛错 → 已完成阶段拼装兜底报告落盘。

**示例**

```
/investigate "看下 README 怎么写的"                                # auto → simple → 单 Agent
/investigate "全面评估 prompt injection + RAG + Agent 工具误用"  # auto → complex → 多 Agent
/investigate "全面分析" --single                                  # 强制单 Agent
/investigate "看一眼 README" --multi                              # 强制多 Agent
```

**落盘**：
- 单 Agent → `investigations/<topic>_<ts>.md`
- 多 Agent → `multi_agent_runs/<topic>_<ts>.md`

报告强制 markdown 含 TOC + 关键发现 + 详细分析 + 修复建议四段式，引用 finding 用 8 位短 hash ID。

---

## /threat-model

基于已有产出（scan / audit / triage / investigation）让 LLM 输出 **STRIDE** 6 类威胁模型 markdown。

**用法**

```
/threat-model
```

不接参数。聚合 `state.load_all_artifacts(project_root)`：

- 最近一次 scan 的 findings（按 severity 取 top 30 并标"已截取"）
- 所有 triages
- 最近 20 条 audit 记录
- 最近 5 条 investigation 记录（仅 topic + 摘要）

**报告格式（强制）**

- TOC + 概述 + 资产与信任边界
- STRIDE 6 类全覆盖（**即使某类没风险也写"未发现"**）
- 风险矩阵 6 行表格
- 缓解优先级（P0 / P1 / P2 / P3）

**约束**

- 没产出 → 提示先做 `/scan` / `/audit` / `/investigate`
- 引用 finding 必须用 8 位 ID（来自素材摘要里的反引号 ID）
- Ctrl+C / LLM 异常 → 已生成部分仍落盘

**落盘**：`threat_models/<ts>.md`。

---

## /resume

按时间 gap（30 分钟）切分 `chat_history.jsonl` 成多个上下文窗口；选一个恢复 + 回放历史对话，继续聊。

**用法**

```
/resume
```

弹菜单（↑/↓ + Enter / Esc 取消），每行格式：

```
2026-05-03 14:08  ·  12 条  ·  这个项目对 prompt injection 抗性如何
```

选中后：

1. `runtime.restore_session` 把历史消息加载进内存 session
2. 按 user / assistant 顺序打印对话（让你看到上下文）
3. 提示符回来，继续聊接前文

> chat 历史本身一直在落盘 (`chat_history.jsonl`)，`/resume` 只是让你**回到**某个旧窗口；新输入写到该窗口尾部。

---

## /config

显示 / 检查 LLM 配置。

**用法**

```
/config            # 等价 /config show
/config show
/config doctor
```

| 子命令 | 行为 |
|---|---|
| `show`   | 当前 profile / provider / model / base url / api key（脱敏） / api key 就绪状态 |
| `doctor` | 三项检查：API Key / Provider 名 / Model；全部 OK 才显示 "所有检查通过" |

**配置来源优先级**（高 → 低）：

1. 环境变量（`POKER_<PROVIDER>_API_KEY` / `POKER_<PROVIDER>_MODEL` / 等）
2. 项目级 `.aisec/config.toml`
3. 用户级 `~/.poker/config.toml`

---

## /help

渲染命令清单（数据复用 `poker.ui.prompt.COMMANDS`，新加命令只动那一份）+ Shell / Chat / 快捷键说明。

**用法**

```
/help
```

---

## /exit · /quit

退出 REPL。两个命令等价。Ctrl+D / Ctrl+C 在主 prompt 上也会退出。

---

## 共性约定

### 命令补全 / 历史

- 输入 `/` 后弹候选菜单；↑/↓ 浏览；Enter **不**直接提交（让你接着敲参数），按选中项填入后再 Enter 才执行
- ↑/↓ 在主 prompt 上浏览本项目历史输入（持久化到 `repl_history`）

### 路径解析

- 所有路径参数相对当前 tracked cwd（受 `!cd` 影响，不是 `os.getcwd()`）
- 越界 / 越项目 root 的路径在 capability 内部一律拒绝

### 错误处理

- 命令实现都包了顶层 try/except；任何异常以 `[red]/<cmd> 错误: ...[/red]` 提示，不抛栈到 REPL
- LLM 不可达 / API key 缺失 → 友好提示而不是抛栈
- Ctrl+C 在 chat / `/investigate` / `/threat-model` 等长跑命令里都会落盘已生成部分再返回

### 落盘共性

所有命令产出都写到 `~/.poker/state/<project_hash>/`（project_hash = abspath sha256 前 12 位）：

```
chat_history.jsonl       conversation, 按 30 分钟 gap 切窗口
last_scan.json           最近一次 scan 全量 findings
findings_history.jsonl   每次 scan
audits/                  各维度 audit 报告
triages.json             accept / ignore / fixed
audit.jsonl              所有命令 + 工具调用的审计日志
backups/                 write_file / apply_patch 的原文件备份
redteam/                 redteam --execute 的运行结果
investigations/          /investigate 单 Agent 报告
multi_agent_runs/        /investigate --multi 多 Agent 报告
threat_models/           /threat-model 报告
repl_history             ↑/↓ 浏览的输入历史
```

`runtime/<project_hash>/<ts>.jsonl` 是另一棵树，由独立的 `poker_observer` 包写（用户在自家项目挂 `PokerCallbackHandler`），不在常规命令落盘范围。

---

## See also

- `README.md` — 项目总览 + Quick Start + `poker_observer` 集成示例
- `AI_SECURITY_CLI_TODO.md` — 路线图 + 决策变更记录
- `docs/redteam_endpoints.toml.example` — `/redteam --execute` 端点白名单示例

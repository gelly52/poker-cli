# Poker CLI MVP 实施 TODO

> 本文档基于已确认方案重写。MVP 目标：跑在用户项目里的"安全调查 Agent" CLI——能扫、能聊、能审、能模拟、能追、能自动记忆。
>
> 决策来源：`C:\Users\28581\.claude\plans\3-scan-claude-code-3-scan-fluttering-bachman.md`
>
> 后续按本文档逐 Stage 推进，每个 Stage 独立可验收。

---

## 0. 锁定决策

### 产品定位

- **一句话**：跑在用户项目里的"安全调查 Agent" CLI
- **vs Claude Code**：通才 → 安全专精
- **vs agent-audit**：批处理扫描 → 多轮交互调查
- **明确放弃**：Skill 包形态、纯静态规则引擎、Claude Code 替代品

### 代码原则（贯穿所有 Stage）

1. **代码精简**：不写冗余、不写不必要的抽象类、能用 Python 常量 / JSON 数据驱动就不写工厂模式
2. **结构可扩展但不预设**：目录设计允许将来加 detector / audit 维度 / payload，但 MVP 不预先搭脚手架（YAGNI）
3. **低耦合 + 职责单一**：每个文件只做一件事；命令入口、数据、算法分开；不把不相关职责堆在一个文件里
4. **复用优先**：现有 `Finding` / `Detector` / `runtime` / `workspace` / `report` / `engine` 直接用，不重写不包装

### 输入分流（REPL 三类输入）

```
poker> 这个项目有什么风险？   # 不带前缀 → chat
poker> /scan               # / 开头 → poker 内置命令
poker> !ls -la             # ! 开头 → 执行 shell 命令
poker> !cd ../another      # cd 也走 ! 通道（poker 内部拦截更新 tracked cwd）
```

- `/<cmd>` → poker 内置命令（scan / audit / redteam / trace / help / quit）
- `!<cmd>` → 直接 shell 执行（cd 内部拦截维护工作目录）；调用记入 `audit.jsonl`
- 其他输入 → chat

### 4 个主动命令（同级，互不重叠）

| 命令 | 类比 | 作用 | MVP 范围 |
|------|------|------|---------|
| `/scan [path]` | 点 | 宽而浅扫一遍 | 复用现有 3 个 detector + severity 分组 |
| `/audit tools` | 线 | 深度审计 agent tools，多步交互 | 仅 tools 维度 |
| `/redteam <prompt>` | 验证 | 对 prompt 文件生成攻击载荷 | 仅生成不执行 |
| `/trace <文件:行:变量>` | 流 | 数据流追踪到危险 sink | 函数内（intra-procedural） |

### MVP 范围外

按"是否后续会做"分两类，避免把延后项当作永久放弃。

**永久放弃（设计取舍，不会做）**：
- ❌ 顶级 `/fix` / `/harden` 命令 —— chat 里自然追问处理，无需独立命令
- ❌ 顶级 `/cd` 命令 —— `!cd` 接管，避免重复

**MVP 不做但后续会做（延后，非放弃）**：
- ⏳ 写文件 / 改代码 → Phase 2（`write_file` / `apply_patch`，带 diff 确认）
- ⏳ `/redteam` 实际调用 endpoint → Phase 2（带确认 + sandbox）
- ⏳ 跨文件 / 跨函数 `/trace` → Phase 2
- ⏳ 多维度 `/audit`（rag / mcp / prompt）→ Phase 2
- ⏳ 主动联网 / `web_search` 默认开启 → 视需求决定（当前默认关闭）
- ⏳ 执行 shell 命令的 Agent 工具 → 视需求决定（用户主动 `!cmd` 不受此限）

---

## 1. 实施阶段总览

| Stage | 主题 | 主要产出 | 估时 | 依赖 |
|-------|------|----------|------|------|
| 1 | REPL 改造 | `/` `!` chat 三类输入分发 | 0.5d | — |
| 2 | 自动记忆 state.py | `.poker/state/<hash>/` 持久化 | 0.5d | Stage 1 |
| 3 | /scan 增强 | severity 分组 + quiet/verbose + 落 state | 0.5d | Stage 2 |
| 4 | 能查（agent tools 扩展） | list_files/read_file/search_text/git_diff | 1d | Stage 2 |
| 5 | /audit tools | 多步审计向导 | 2d | Stage 4 |
| 6 | /redteam | payload 生成 | 1d | Stage 2 |
| 7 | /trace | intra-proc taint | 2d | Stage 2 |
| 8 | 测试样例 + 错误处理 | 10+ 样例 + 错误兜底 | 1d | All |

**总估**：约 8.5d。Stage 5/6/7 互相独立，可并行。

---

## 2. 各阶段详细 TODO

### Stage 1: REPL 改造（0.5d）

**目标**：`poker` 启动后进入 REPL，能识别 `/` `!` chat 三类输入。

- [ ] 修改 `poker/cli/repl.py`：
  - [ ] 输入解析：`/` 开头进 command 分发；`!` 开头进 shell；其他进 chat
  - [ ] 启动时记录 cwd 为 tracked project root
  - [ ] `!cd <path>` 内部拦截，更新 tracked cwd（不实际 spawn shell cd）
  - [ ] 其他 `!cmd` 用 `subprocess.run` 执行，捕获 stdout/stderr 返显
  - [ ] `/help` 列内置命令
  - [ ] `/quit` 退出
- [ ] 修改 `poker/cli/__init__.py`：注册 audit/redteam/trace 命令占位（实现留 Stage 5/6/7）
- [ ] **验收**：
  - [ ] `poker` → 出现 prompt
  - [ ] `/help` 列出所有命令
  - [ ] `!ls` 输出当前目录
  - [ ] `!cd ..` 后 `!pwd` 显示新路径
  - [ ] 不带前缀的输入进 chat

---

### Stage 2: 自动记忆 state.py（0.5d）

**目标**：所有命令产出自动持久化到 `.poker/state/<project_hash>/`。

- [ ] 新建 `poker/state.py`：
  - [ ] `project_hash(project_root: Path) -> str`：abspath 的 sha256 前 12 位
  - [ ] `get_state_dir(project_root: Path) -> Path`：返回 `.poker/state/<hash>/`，确保存在
  - [ ] `append_chat(project_root, role, content)`：追加 `chat_history.jsonl`
  - [ ] `load_chat(project_root, limit=50) -> list[dict]`：加载历史聊天
  - [ ] `save_findings(project_root, findings)`：覆盖写 `last_scan.json` + 追加 `findings_history.jsonl`
  - [ ] `load_last_findings(project_root) -> list`：读 `last_scan.json`
  - [ ] `save_audit(project_root, dimension, target, result)`：写 `audits/<dim>_<target>_<ts>.json`
  - [ ] `set_triage(project_root, finding_id, state)`：更新 `triages.json`（state ∈ {accepted, ignored, fixed}）
  - [ ] `append_audit_log(project_root, event: dict)`：追加 `audit.jsonl`
- [ ] 修改 `poker/cli/repl.py`：
  - [ ] 启动时调 `load_chat` 显示最近 N 条
  - [ ] 每轮 chat 完成调 `append_chat`（user + assistant 各一条）
  - [ ] 每个 `/cmd` 或 `!cmd` 执行前调 `append_audit_log`
- [ ] **验收**：
  - [ ] 聊几句退出，再启动看到历史
  - [ ] `.poker/state/<hash>/chat_history.jsonl` 有内容
  - [ ] `.poker/state/<hash>/audit.jsonl` 有命令日志

---

### Stage 3: /scan 增强 + 落 state（0.5d）

**目标**：scan 输出按 severity 分组，结果自动落盘。

- [ ] 修改 `poker/capabilities/scan/report.py`：
  - [ ] 新增 `print_table_grouped(console, findings)`：按 severity 分组渲染（critical → high → medium → low → info）
  - [ ] 新增 `print_summary(console, findings)`：`critical N | high N | medium N | low N | info N`
- [ ] 修改 `poker/cli/scan.py`：
  - [ ] 加 `--quiet`：只输出 critical / high
  - [ ] 加 `--verbose`：输出全部含 info
  - [ ] 默认改用 `print_table_grouped`
  - [ ] 跑完调 `state.save_findings`
- [ ] **验收**：
  - [ ] 在样例项目 `/scan` → 按 severity 分组显示
  - [ ] `--quiet` 隐藏 medium / low / info
  - [ ] `--verbose` 显示 info
  - [ ] `.poker/state/<hash>/last_scan.json` 有数据

---

### Stage 4: 能查（agent tools 扩展，1d）

**目标**：让 LLM 在 chat 中真正能"看"项目。

- [ ] 修改 `poker/agent/tools.py`，新增 6 个工具：
  - [ ] `list_files(path: str = "") -> str`：复用 `workspace.iter_text_files`，限制在 project_root，尊重 `.gitignore`
  - [ ] `read_file(path: str) -> str`：限制路径在 project_root，最大 200KB，超出截断提示
  - [ ] `search_text(pattern: str, path: str = "") -> str`：项目内文本搜索（用 `re` 或 ripgrep）
  - [ ] `search_code(pattern: str, path: str = "") -> str`：代码符号 / 模式搜索（仅 .py / .ts / .js 等）
  - [ ] `git_diff() -> str`：subprocess 跑 `git diff`
  - [ ] `git_status() -> str`：subprocess 跑 `git status --short`
- [ ] 通用要求（每个工具都要做）：
  - [ ] 输入校验：路径 resolve 后必须以 project_root 开头，越界返回错误字符串
  - [ ] 错误结构化返回（不抛异常，返回 `"错误：xxx"`）
  - [ ] 调用前调 `state.append_audit_log`
- [ ] 修改 `poker/agent/tools.py` 的 `get_agent_tools()` 注册新工具
- [ ] **验收**：
  - [ ] chat：「列出项目所有 .py 文件」 → 列出
  - [ ] chat：「读一下 README.md」 → 显示内容
  - [ ] chat：「搜索 OpenAI」 → 命中位置
  - [ ] chat：「最近改了什么」 → git diff 内容
  - [ ] 越界访问 `read_file("/etc/passwd")` 被拒

---

### Stage 5: /audit tools（2d）

**目标**：交互式深度审计 agent tools 维度。

- [ ] 新建 `poker/capabilities/audit/__init__.py`：
  - [ ] `run_audit(dimension: str, project_root: Path, llm, ui)`：分发到具体维度模块
  - [ ] MVP 只支持 `dimension == "tools"`，其他维度抛 `NotImplementedError` 友好提示
- [ ] 新建 `poker/capabilities/audit/tools.py`：
  - [ ] `find_tools(project_root) -> list[ToolInfo]`：用 AST 找：
    - LangChain `@tool` 装饰器
    - LangChain `Tool(...)` 实例化
    - OpenAI function calling schema 字典
  - [ ] `audit_tool(tool_info, llm) -> AuditResult`：分项检查
    - 参数有无校验（regex / Pydantic / 手写 if）
    - 是否拼到 shell / SQL / prompt
    - 是否有用户确认（HITL）
    - 描述模糊度（用 LLM 判断）
  - [ ] `interactive_audit(project_root, llm, ui)` 主流程：
    1. 列出 tools 让用户选（编号 / 名称）
    2. 跑 audit_tool
    3. 中途允许追问（ui 调 chat）
    4. 输出结构化评估
    5. 调 `state.save_audit`
- [ ] 新建 `poker/cli/audit.py`：
  - [ ] 命令入口，参数解析（维度），调 `capabilities.audit.run_audit`
- [ ] 修改 `poker/cli/__init__.py` 替换 audit 占位为真实实现
- [ ] **验收**：
  - [ ] 在含多个 `@tool` 的样例项目跑 `/audit tools`
  - [ ] 列出 tools
  - [ ] 用户选一个后输出结构化评估
  - [ ] `.poker/state/<hash>/audits/tools_<name>_<ts>.json` 存在

---

### Stage 6: /redteam（1d）

**目标**：对 prompt 文件生成攻击载荷（不执行）。

- [ ] 新建 `poker/capabilities/redteam/payloads.py`：
  - [ ] `PAYLOAD_LIBRARY: dict[str, list[Payload]]`，类别：
    - `jailbreak`（DAN、AIM、developer mode 等）
    - `role_override`（覆盖 system prompt 的载荷）
    - `context_smuggling`（在 user 内容里插隐藏指令）
    - `data_exfil`（诱导泄露 system prompt / 历史）
    - `instruction_hierarchy`（绕过指令优先级）
  - [ ] 每类 5+ 条，每条含 `payload` + `intent` + `references`（可空）
  - [ ] 数据写在 Python 常量或单独 JSON 文件
- [ ] 新建 `poker/capabilities/redteam/__init__.py`：
  - [ ] `analyze_prompt(prompt_text) -> dict`：识别角色 / 约束 / 是否提到 tool 调用 / 是否提到敏感数据
  - [ ] `generate_payloads(prompt_text) -> list[PayloadResult]`：根据分析结果选相关 payload，输出含意图说明
- [ ] 新建 `poker/cli/redteam.py`：
  - [ ] 参数：prompt 文件路径
  - [ ] 校验路径在 project_root 内
  - [ ] 读文件 → 调 generate_payloads → 渲染表格
  - [ ] **不实际执行 endpoint**
- [ ] 修改 `poker/cli/__init__.py` 注册 /redteam
- [ ] **验收**：
  - [ ] 对样例 system prompt 跑 `/redteam prompts/system.md`
  - [ ] 输出 ≥5 条针对性 payload + 意图说明
  - [ ] 不联网、不调 endpoint

---

### Stage 7: /trace（2d）

**目标**：函数内数据流追踪。

- [ ] 新建 `poker/capabilities/trace/sinks.py`：
  - [ ] `DANGEROUS_SINKS: list[SinkPattern]`，包括：
    - `subprocess.run` / `Popen` / `call`（shell=True 或参数到 cmd）
    - `eval` / `exec` / `compile`
    - `os.system` / `os.popen`
    - `cursor.execute` / `cursor.executemany`（拼接的 SQL）
    - prompt 拼接（`agent.invoke` / `llm.invoke` 的字符串拼接参数）
    - `open()` 的写入模式
  - [ ] 每个 sink 含：模式描述 + 触发条件 + 风险说明
- [ ] 新建 `poker/capabilities/trace/__init__.py`：
  - [ ] `trace_var(file_path: Path, line: int, var_name: str) -> TraceResult`：
    - 用 `ast` 解析文件
    - 找到 line 所在 function 节点
    - 在 function AST 内做 def-use 追踪：变量赋值 / 拼接 / 传参
    - 检查每跳是否触达 `DANGEROUS_SINKS`
    - 返回 hop 列表 + 最终判断（safe / warn / danger）
- [ ] 新建 `poker/cli/trace.py`：
  - [ ] 参数解析：`<文件:行:变量>`
  - [ ] 调 `trace_var` → 渲染数据流路径（每 hop 一行 + 最终标记）
- [ ] 修改 `poker/cli/__init__.py` 注册 /trace
- [ ] **验收**：
  - [ ] 对含 `user_input → command → subprocess.run(shell=True)` 的样例代码跑
  - [ ] 输出完整数据流路径
  - [ ] 危险 sink 标 ⚠️ 并给修复建议

---

### Stage 8: 测试样例 + 错误处理（1d）

**目标**：测试覆盖、错误兜底。

- [ ] 补样例项目到 `tests/e2e/sample_project/`：
  - [ ] `langchain_agent/` —— 含 `@tool` 的小项目（覆盖 audit + scan）
  - [ ] `secrets_demo/` —— 含硬编码 OpenAI key / AWS key 的项目（覆盖 secret detector）
  - [ ] `bad_prompt/` —— 含不安全 system prompt 的项目（覆盖 prompt detector + redteam）
  - [ ] `unsafe_tool/` —— `user_input → subprocess(shell=True)` 的项目（覆盖 trace）
- [ ] 补单元测试：
  - [ ] `tests/test_state.py` —— state 各函数（路径、读写、triage）
  - [ ] `tests/test_audit_tools.py` —— `find_tools` 能识别 @tool / Tool() / function schema
  - [ ] `tests/test_redteam.py` —— `analyze_prompt` + `generate_payloads`
  - [ ] `tests/test_trace.py` —— sink 命中 / 未命中 / 多 hop
  - [ ] `tests/test_repl.py` —— `/` `!` chat 分发
- [ ] 错误处理：
  - [ ] LLM 调用：超时重试（指数退避，max 3 次）、token 超限提示截断
  - [ ] detector 异常：单个失败不影响其他，错误记入 `audit.jsonl`
  - [ ] 文件编码 / AST 解析失败：跳过并记录跳过原因
  - [ ] 配置缺失：友好提示，不抛栈
  - [ ] 路径越界：拒绝 + 记录
- [ ] **验收**：
  - [ ] `pytest tests/` 全绿
  - [ ] 模拟 LLM 超时能正确重试
  - [ ] 越界尝试被拒并记录
  - [ ] 安全测试样例总数 ≥10

---

## 3. MVP 验收标准

跑完上面 8 个 Stage 后，应满足：

- [ ] 在含硬编码 OpenAI key + 不安全 `@tool` 的样例项目上 `/scan` 输出 ≥3 个 findings 分级显示
- [ ] `/audit tools` 能列出 tools，用户选后输出结构化评估
- [ ] `/redteam <prompt>` 输出 ≥5 条针对性 payload + 意图
- [ ] `/trace agent.py:21:user_input` 输出完整数据流路径 + 危险标记
- [ ] 跑完 scan 退出再进入，chat 中能引用历史 findings
- [ ] `!ls` 能输出，`audit.jsonl` 留记录
- [ ] `!cd ../another` 后 `/scan` 不读切换前的目录
- [ ] 没 API key 时 `--help` / `/help` / `/quit` 仍可用
- [ ] `pytest tests/` 全绿，覆盖率 >70%
- [ ] 安全测试样例 ≥10 个

---

## 4. Phase 2+ 路线（不在 MVP）

### Phase 2 —— 从 Agent CLI 变成交互式安全助手
- [ ] 跨文件 `/trace`
- [ ] `/audit` 加 rag / mcp / prompt 维度
- [ ] `/redteam` 实际执行 endpoint（带确认 + sandbox）
- [ ] 写文件能力（`write_file` / `apply_patch`）
- [ ] 会话持久化更完善（多会话切换）
- [ ] MCP 静态 schema 分析

### Phase 3 —— 从本地工具变成团队流程工具
- [ ] CI 模式（`poker scan --ci`）
- [ ] SARIF 输出
- [ ] baseline 管理（`poker scan --baseline`）
- [ ] 团队策略配置文件
- [ ] 自定义规则
- [ ] 审计日志强化

### Phase 4 —— AI 安全特色能力
- [ ] runtime 观测（LangChain callback / OpenTelemetry 钩子）
- [ ] `/threat-model` 命令
- [ ] 国产生态深度支持（Dify / Coze / FastGPT / Langchain-Chatchat / Qwen-Agent）
- [ ] Agent 权限建模
- [ ] 多 Agent 协作

---

## 5. 已完成（保留 / 复用，不动）

- [x] 项目骨架：`poker/cli/` / `agent/` / `capabilities/scan/` / `models/` / `config/` / `ui/`
- [x] Agent runtime（`langchain-core`，流式 + history）
- [x] 3 个 detector：`secret` / `prompt` / `agent_tools`
- [x] Scan engine + reporter（table / json / markdown）
- [x] 配置系统（多 provider + profile + 项目级 / 用户级 / 环境变量覆盖）
- [x] CLI 基础命令：`scan` / `init` / `config` / `chat`
- [x] 样例配置 `.aisec/config.toml.example`
- [x] Finding 数据模型（severity / category / path / line / evidence / recommendation）
- [x] Workspace 文件遍历

---

## 6. 决策变更记录

| 日期 | 决策项 | 变更内容 |
|------|--------|---------|
| 2026-04-29 | Agent Runtime 技术选型 | 确定 langchain-core，不引入完整 langchain |
| 2026-04-29 | MVP 形态 | Agent + scan，非纯扫描器 |
| 2026-05-01 | TODO 全文重写 | 锁定 4 个主动命令 + REPL 三类输入；按 Stage 拆解 |
| 2026-05-01 | `/cd` 删除 | 改用 `!cd` 内部拦截 |
| 2026-05-01 | 能力分层 | 命令入口 / 能力实现 / 基础设施三层，audit/redteam/trace 都进 `capabilities/` |

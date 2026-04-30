# AI 安全 Agent CLI 产品定位与 TODO List

> 本文档用于指导当前项目从一个通用 LangChain CLI Demo，演进为一个面向 AI 安全领域的 Agent CLI 工具。
>
> 当前的 `ping` / `run` / `version` 只是早期模板命令，未来可以删除或替换，不需要作为核心能力保留。

---

## 0. 已确认决策

以下决策在开发前必须确定，后续变更需显式更新本节。

### 技术选型

- [x] 采用 `langchain-core` 作为 Agent Runtime 基础，不引入完整 `langchain` 包。
- [x] 社区集成（OpenAI / Anthropic / DeepSeek / Qwen 等）通过 `langchain-${provider}` 单独按需引入。
- [x] Python / LangChain / OpenAI-compatible 项目优先识别。

### 架构原则

- [x] Agent Runtime 与能力模块（Capabilities）硬隔离：Agent 不关心具体能力实现，只负责 LLM 调用循环和工具路由。
- [x] 每个能力模块独立，有自己的引擎、规则、报告格式。
- [x] Agent 通过工具接口暴露能力；CLI 通过命令直接调用能力。两条路径互不干扰。
- [x] workspace 是共享基础设施，不被任何单一能力独占。

### MVP 决策

- [x] MVP 形态：Agent + 单一扫描能力（scan）。Agent 能"看"和"说"，不能"改"。
- [x] 第一批目标用户：开发 LLM 应用的 Python 开发者。
- [x] 所有扫描默认只读。
- [x] MVP 不允许工具自动修改代码。
- [x] MVP 不允许工具执行 shell 命令。
- [x] CI 不是一等公民，Phase 3 再做。
- [x] SARIF 报告 Phase 3 再做。
- [x] 离线 / 本地模型通过 langchain-core ChatModel 抽象自然支持，但不作为 MVP 验收条件。

### 决策变更记录

| 日期       | 决策项                 | 变更内容                                      |
| ---------- | ---------------------- | --------------------------------------------- |
| 2026-04-29 | Agent Runtime 技术选型 | 确定采用 langchain-core，不引入完整 langchain |
| 2026-04-29 | MVP 形态               | 确定为 Agent + scan 能力，非纯扫描器          |
| 2026-04-29 | run_command Agent 工具 | 不做永久排除，视未来需求决定                  |

---

## 1. 产品定位

### 1.1 一句话定位

这是一个面向开发者和安全工程师的本地 AI 安全 Agent CLI，用于审计、测试和加固 AI 应用、Agent 项目、LLM 工具链与代码仓库。

它不是一个通用 Claude Code 替代品，而是一个更聚焦的安全型 Agent：

- 帮你发现 AI 应用里的安全风险；
- 帮你理解风险为什么存在；
- 帮你生成可审查、可回滚的修复方案；
- 帮你把安全检查沉淀为本地、CI 和团队流程中的自动化能力。

### 1.2 目标用户

#### 核心用户

- 正在开发 LLM 应用、RAG 应用、Agent 系统、MCP Server、AI Copilot 的开发者；
- 需要审查 AI 项目安全风险的安全工程师；
- 想在 CI/CD 中加入 AI 安全检查的团队；
- 需要快速做 AI 应用威胁建模和安全基线检查的创业团队或平台团队。

#### 非核心用户

- 只想聊天的普通用户；
- 只需要普通代码生成的用户；
- 只做传统 Web 漏洞扫描、但不关心 AI/LLM 场景的用户。

### 1.3 为什么用户要选择它

用户选择它的理由应该不是"它也是一个 CLI Agent"，而是：

1. 它理解 AI 应用特有风险

   - Prompt injection；
   - Jailbreak；
   - RAG 数据污染；
   - Tool calling 越权；
   - Agent 文件/命令/网络权限过大；
   - MCP 工具暴露风险；
   - LLM 输出数据泄露；
   - 不安全的系统提示词和安全边界缺失。

2. 它能在本地项目上下文中工作

   - 读取当前仓库；
   - 理解目录结构；
   - 搜索代码；
   - 分析依赖；
   - 检查配置；
   - 审查 git diff；
   - 输出可追踪的证据和修复建议。

3. 它不是只报问题，而是能辅助修复

   - 给出风险等级；
   - 给出受影响文件和代码位置；
   - 解释攻击路径；
   - 生成补丁草案；
   - 修改前展示 diff；
   - 用户确认后才应用变更。

4. 它适合接入工程流程
   - 支持本地交互；
   - 支持一次性扫描；
   - 支持 CI 模式；
   - 支持 JSON / Markdown / SARIF 报告；
   - 支持安全策略配置；
   - 支持团队自定义规则。

### 1.4 产品边界

#### 应该做

- AI 应用安全审计；
- Agent 工具权限审计；
- Prompt / System Prompt 安全检查；
- RAG 管线安全检查；
- MCP Server / tool schema 风险分析；
- Secret 扫描；
- 依赖漏洞检查；
- 代码安全扫描整合；
- 安全修复建议；
- 安全报告生成；
- 本地项目交互式安全助手。

#### 不应该优先做

- 纯聊天助手；
- 普通代码补全；
- 大而全的 Claude Code 克隆；
- 无边界的自动执行命令 Agent；
- 无确认的自动改代码；
- 泛化到所有传统安全扫描场景。

### 1.5 推荐产品形态

产品名已确定为 Poker CLI。

建议将 Poker CLI 的产品类型定位为：

"AI Security Code Agent" 或 "LLM App Security Agent"。

它的核心不是替代 Claude Code，而是成为 Claude Code、Cursor、GitHub Copilot 之外的安全补位工具。

典型使用方式：

- 开发者写完一个 Agent 项目后，运行一次安全审查；
- 安全工程师对团队的 LLM 应用仓库做 AI 安全评估；
- CI 在 PR 阶段自动检查 prompt、tool、RAG、secret、dependency 风险；
- 用户让 CLI 解释某个风险并生成可审查修复方案。

---

## 2. 核心使用场景

### 2.1 项目级 AI 安全扫描

用户在项目根目录执行扫描，工具自动分析：

- 项目类型；
- 使用的 LLM 框架；
- Prompt 文件；
- Agent 工具定义；
- MCP Server 定义；
- RAG 代码路径；
- API Key 和 Secret 泄露；
- 依赖漏洞；
- 高风险文件操作和命令执行能力。

输出内容：

- 风险总览；
- 风险等级；
- 证据位置；
- 攻击路径；
- 修复建议；
- 是否可自动修复。

### 2.2 AI Agent 工具权限审计

分析 Agent 项目中暴露给 LLM 的工具，包括：

- 文件读写；
- shell 命令执行；
- 网络请求；
- 数据库访问；
- 浏览器自动化；
- MCP tool；
- 自定义函数工具。

重点检查：

- 工具描述是否过宽；
- 参数是否缺少校验；
- 是否允许路径穿越；
- 是否允许任意命令执行；
- 是否缺少用户确认；
- 是否缺少 allowlist / denylist；
- 是否可能被 prompt injection 诱导滥用。

### 2.3 Prompt Injection 与 Jailbreak 检查

检查项目中的：

- system prompt；
- developer prompt；
- tool prompt；
- RAG instruction；
- eval case；
- policy prompt。

识别问题：

- 安全边界模糊；
- 缺少数据泄露防护；
- 缺少工具调用约束；
- 允许外部内容覆盖系统指令；
- 未区分可信与不可信输入；
- 对检索内容缺少隔离说明。

### 2.4 RAG 安全审计

检查 RAG 系统中的风险：

- 是否将不可信文档直接拼入 prompt；
- 是否缺少来源标记；
- 是否缺少引用校验；
- 是否可能被文档注入攻击；
- 是否泄露内部文档；
- 是否将用户输入直接用于检索或过滤；
- 是否缺少访问控制。

### 2.5 MCP / Tool Server 安全审计

针对 MCP Server 或类似 tool server，检查：

- 暴露了哪些工具；
- 工具 schema 是否过于宽泛；
- 是否暴露敏感路径、环境变量、凭据、内部服务；
- 是否存在任意文件读写；
- 是否存在任意命令执行；
- 是否缺少鉴权和权限边界；
- 是否缺少审计日志。

### 2.6 安全修复 Agent

对发现的问题，工具可以：

- 解释风险；
- 给出修复方案；
- 生成 patch；
- 展示 diff；
- 等待用户确认；
- 应用修改；
- 运行测试或安全检查验证结果。

---

## 3. 建议 CLI 命令设计

### 3.1 初始命令集

以下命令可以替代当前的 `ping` / `run` / `version`：

- `init`

  - 初始化项目配置；
  - 生成默认安全策略；
  - 检测项目类型；
  - 选择模型 provider。

- `scan`

  - 对当前项目进行安全扫描；
  - 默认只读；
  - 输出人类可读报告；
  - 支持 JSON / SARIF / Markdown 输出。

- `review`

  - 审查指定文件、目录或 git diff；
  - 适合 PR 前本地检查；
  - 输出风险、证据和建议。

- `chat`

  - 进入交互式安全 Agent 会话；
  - 可以围绕当前项目问问题；
  - 支持上下文记忆；
  - 支持工具调用确认。

- `fix`

  - 针对某个风险生成修复方案；
  - 默认只生成 diff；
  - 用户确认后才写入文件。

- `threat-model`

  - 根据项目结构和代码生成威胁模型；
  - 输出资产、入口、信任边界、攻击路径和缓解建议。

- `prompt-audit`

  - 专门审查 prompt 和 LLM 指令；
  - 检查 prompt injection / jailbreak / data exfiltration 风险。

- `tools-audit`

  - 专门审查 Agent 工具、MCP 工具、LangChain tools、function calling schema；
  - 检查权限边界和参数校验。

- `config`
  - 查看和修改模型、策略、扫描器配置。

### 3.2 命令与阶段规划

| 命令           | Phase 1 (MVP) | Phase 2        | Phase 3 | Phase 4 |
| -------------- | ------------- | -------------- | ------- | ------- |
| `init`         | P0            |                |         |         |
| `scan`         | P0            |                |         |         |
| `chat`         | P0            |                |         |         |
| `config`       | P0            |                |         |         |
| `review`       |               | P1             |         |         |
| `fix`          |               | P1             |         |         |
| `prompt-audit` |               | P1（独立命令） |         |         |
| `tools-audit`  |               | P1（独立命令） |         |         |
| `threat-model` |               |                |         | P2      |

> MVP 阶段，prompt-audit / tools-audit / mcp-audit 作为 `scan` 内部自动运行的扫描器子集，不提供独立命令。独立命令在 Phase 2 引入。

### 3.3 可以后续删除的模板命令

当前命令不是未来核心能力，可以删除或重命名：

- `ping`

  - 可被 `doctor` 或 `config test` 替代。

- `run`

  - 过于泛化，可被 `chat` / `scan` / `review` / `fix` 替代。

- `version`
  - 可以保留为常规命令，但不是产品核心。

---

## 4. 架构 TODO List

### P0：重新定义项目骨架

- [x] 确定正式产品名和 CLI 命令名：Poker CLI / `poker`。
- [x] 保留包名 `poker`。
- [x] 将 `pyproject.toml` 中的 `name`、`description`、`authors` 更新为当前定位。
- [x] 保留 CLI script 为正式命令名 `poker`。
- [x] 重写 `README.md`，使其围绕 AI 安全 Agent CLI 展开。
- [x] 删除 `QUICKSTART.md` 中的模板内容。
- [ ] 明确当前阶段的 MVP 范围，不做大而全。
- [ ] 保留 MIT License 或换成更符合项目策略的许可证。

### P0：基础目录重构

演进为以下模块：

```
poker/
├── cli/                       # 命令入口和参数解析
├── config/                    # 配置加载、profile、环境变量
├── agent/                     # Agent Runtime（基于 langchain-core）
│   ├── runtime.py             # Agent 执行循环
│   ├── llm.py                 # ChatModel provider 抽象
│   ├── tools.py               # Agent 可调用的工具注册（面向 LLM）
│   └── prompts.py             # System prompt 模板
├── capabilities/              # Agent 能力模块（每个能力一个子目录）
│   └── scan/                  # 安全扫描能力
│       ├── engine.py          # 扫描编排（scan_path 等）
│       ├── detectors/         # 扫描器实现
│       └── report.py          # 扫描报告生成
├── workspace/                 # 项目文件操作（共享基础设施）
├── models/                    # 共享数据模型
└── ui/                        # Rich 输出、流式输出、diff 展示
```

- [x] 创建 `cli/` 目录，迁移命令入口和参数解析。
- [x] 创建 `config/` 目录，集中配置管理。
- [x] 创建 `agent/` 目录，实现 Agent Runtime。
- [x] 创建 `agent/llm.py`，ChatModel provider 抽象。
- [x] 创建 `agent/tools.py`，Agent 工具注册机制。
- [x] 创建 `agent/prompts.py`，System prompt 模板。
- [x] 创建 `capabilities/` 目录，作为能力模块父目录。
- [x] 创建 `capabilities/scan/` 目录，迁移现有扫描器。
- [x] 将现有 `poker/detectors/` 迁移到 `capabilities/scan/detectors/`。
- [x] 将现有 `poker/scanner.py` 迁移到 `capabilities/scan/engine.py`。
- [x] 将现有 `poker/reporter.py` 迁移到 `capabilities/scan/report.py`。
- [x] 保留 `workspace.py` 作为共享基础设施。
- [x] 创建 `ui/` 目录，Rich 输出和流式渲染。

> **核心原则**：
>
> - `agent/` 不关心具体能力实现，只负责 LLM 调用循环和工具路由。
> - `capabilities/` 中每个模块独立，有自己的引擎、规则、报告格式。
> - Agent 通过工具接口暴露能力；CLI 通过命令直接调用能力。两条路径互不干扰。
> - `workspace/` 是共享基础设施，不被任何单一能力独占。
> - 未来新增能力（threat_model、hardening 等）只需在 capabilities/ 下加目录，不改 agent 层。

### P0：配置系统重构

- [x] 避免在 import 阶段强制加载 `API_KEY`。
- [x] 允许 `help`、`version`、`init` 等命令在没有 API Key 时正常运行。
- [x] 支持多 provider 配置：OpenAI、Anthropic、DeepSeek、Qwen、本地 OpenAI-compatible endpoint。
- [x] 支持 profile，例如 `default`、`local`、`ci`。
- [x] 支持项目级配置文件，例如 `.aisec/config.toml`。
- [x] 支持用户级配置文件，例如用户主目录下的配置。
- [x] 支持环境变量覆盖配置文件。
- [x] 支持 `doctor` 或 `config test` 检查配置是否有效。
- [x] 对敏感配置做脱敏展示。

### P0：Agent Runtime 重构

- [x] 基于 `langchain-core` 实现 Agent 执行循环。
- [x] 将 `create_agent()` 拆分为 LLM 初始化、prompt 构建、工具注册、memory/session 管理。
- [x] 定义安全 Agent 的 system prompt。
- [x] 加入工具调用策略说明。
- [x] 加入风险等级输出规范。
- [x] 加入证据引用规范。
- [x] 加入"修改前必须展示 diff 并确认"的规则。
- [x] 加入"危险操作必须请求确认"的规则。
- [x] 支持流式输出。
- [x] 支持交互式会话。
- [ ] 支持 session 持久化（Phase 2）。
- [ ] 支持中断和恢复（Phase 2）。

---

## 5. 工具系统 TODO List

### 设计说明

Poker CLI 的工具系统分为两类，职责和生命周期完全不同：

| 分类               | 位置                   | 面向        | 说明                                                               |
| ------------------ | ---------------------- | ----------- | ------------------------------------------------------------------ |
| **Agent 操作工具** | `agent/tools.py` 注册  | LLM         | Agent 用来探索和操作项目的通用工具（read_file, search_text 等）    |
| **能力内部工具**   | `capabilities/*/` 内部 | CLI / Agent | 能力模块的内部实现，如扫描器、审计器等。Agent 通过工具接口间接调用 |

> Agent 操作工具是"Agent 的手"；能力内部工具是"Agent 的专业知识"。两者不在同一目录下，也不共享注册机制。

### P0：Agent 操作工具抽象

- [ ] 定义统一的 Agent Tool 接口（基于 langchain-core BaseTool）。
- [ ] 每个工具必须包含名称、描述、输入 schema、输出 schema。
- [ ] 每个工具必须标记风险等级：read-only、write、network、shell、destructive。
- [ ] 每个工具必须声明是否需要用户确认。
- [ ] 每个工具执行前写审计日志。
- [ ] 每个工具执行后返回结构化结果。
- [ ] 工具错误必须结构化返回，而不是只抛异常。
- [ ] 支持启用/禁用工具。
- [ ] 支持按 policy 决定工具是否可用。

### P0：基础项目工具（Agent 操作工具）

- [ ] `list_files`：列出项目文件，尊重 `.gitignore`。
- [ ] `read_file`：读取指定文件，限制项目根目录内访问。
- [ ] `search_text`：全文搜索。
- [ ] `search_code`：代码符号或模式搜索。
- [ ] `git_diff`：读取当前 git diff。
- [ ] `git_status`：读取 git 状态。
- [x] `scan_project`：调用 capabilities.scan 引擎，返回扫描结果。（Agent 调用扫描能力的入口）

### P1：写操作工具（Phase 2，fix 命令需要）

- [ ] `write_file`：写文件，默认需要确认。
- [ ] `apply_patch`：应用 patch，必须展示 diff 并确认。
- [ ] `run_command`：执行命令，高风险，必须确认，并支持 allowlist。

### P0：安全扫描器（能力内部工具，在 capabilities/scan/detectors/ 内）

- [x] `secret_scan`：扫描 API Key、token、私钥、密码。
- [x] `prompt_audit`：扫描 prompt injection / jailbreak 风险。
- [x] `agent_tool_audit`：审查 LangChain @tools / function calling schema。
- [ ] `dependency_audit`：检查 Python 依赖漏洞。
- [ ] `mcp_audit`：审查 MCP Server 工具暴露风险。
- [ ] `rag_audit`：审查 RAG 注入和数据泄露风险。
- [ ] `unsafe_command_audit`：检查代码中危险命令执行。
- [ ] `file_permission_audit`：检查危险文件读写。

### P1：第三方工具集成

- [ ] 集成 Bandit，用于 Python 安全扫描。
- [ ] 集成 pip-audit，用于 Python 依赖漏洞检查。
- [ ] 集成 Semgrep，用于规则化代码扫描。
- [ ] 集成 detect-secrets 或 gitleaks，用于 secret 扫描。
- [ ] 集成 OSV 数据源。
- [ ] 支持工具不可用时给出安装建议。
- [ ] 支持 CI 环境下静默执行。

---

## 6. 安全能力 TODO List

### P0：风险模型

- [x] 定义统一 Finding 数据结构。
- [x] Finding 包含：rule_id、title、severity、category、path、line、evidence、recommendation。
- [x] severity 包含：critical、high、medium、low、info。
- [x] category 包含：secret、tool-permission、prompt-injection 等。
- [ ] Finding 增加 confidence 字段：high、medium、low。
- [ ] Finding 增加 impact 字段。
- [ ] 支持去重。
- [x] 支持风险排序（已实现按 severity 排序）。
- [ ] 支持风险忽略和 baseline。

### P0：AI 应用识别

- [ ] 识别 LangChain 项目。
- [ ] 识别 LlamaIndex 项目。
- [ ] 识别 OpenAI SDK 使用。
- [ ] 识别 Anthropic SDK 使用。
- [ ] 识别 MCP Server 项目。
- [ ] 识别 Prompt 文件。
- [ ] 识别 RAG 相关代码。
- [ ] 识别 Agent 工具定义。
- [ ] 识别 function calling / tool schema。

### P0：Prompt 安全审计

- [x] 扫描 prompt 文件和代码中的 prompt 字符串。
- [x] 检查是否区分 trusted / untrusted content。
- [ ] 检查是否允许用户或文档覆盖 system instruction。
- [ ] 检查是否缺少数据泄露约束。
- [ ] 检查是否缺少工具调用边界。
- [ ] 检查是否缺少引用和来源说明。
- [ ] 输出具体风险和修复建议。

### P0：Agent Tool 安全审计

- [x] 识别 LangChain `@tool`。
- [x] 检查 shell 执行风险。
- [x] 检查文件写操作风险。
- [x] 检查网络请求风险。
- [x] 检查高风险工具是否缺少用户确认。
- [ ] 识别 OpenAI function calling schema。
- [ ] 识别 MCP tools。
- [ ] 检查工具描述是否过于宽泛。
- [ ] 检查参数是否缺少校验。
- [ ] 检查文件路径参数是否可能路径穿越。
- [ ] 检查命令参数是否可能任意命令执行。
- [ ] 检查网络请求是否可能 SSRF。
- [ ] 检查敏感数据访问是否缺少权限判断。

### P1：RAG 安全审计

- [ ] 识别文档加载器。
- [ ] 识别向量库。
- [ ] 识别 retriever。
- [ ] 识别把检索结果拼入 prompt 的位置。
- [ ] 检查是否缺少文档来源隔离。
- [ ] 检查是否缺少 prompt injection 防护。
- [ ] 检查是否缺少访问控制。
- [ ] 检查是否可能越权检索。
- [ ] 给出安全 RAG prompt 模板建议。

### P1：MCP 安全审计

- [ ] 识别 MCP server 配置。
- [ ] 枚举 MCP tools。
- [ ] 检查 tool schema。
- [ ] 检查是否暴露本地文件系统。
- [ ] 检查是否暴露 shell。
- [ ] 检查是否暴露环境变量。
- [ ] 检查是否暴露内部服务。
- [ ] 检查是否缺少权限边界。
- [ ] 输出 MCP 安全基线报告。

---

## 7. CLI 体验 TODO List

### P0：基础 CLI 体验

- [ ] 使用 Rich 输出清晰的扫描进度。
- [ ] 对风险按 severity 分组展示。
- [ ] 每个风险展示文件、行号、证据、原因、建议。
- [x] 支持 `--format markdown`。
- [x] 支持 `--format json`（已有 print_json）。
- [x] 支持 `--format table`（已有 print_table）。
- [x] 支持 `--output report.md`。
- [x] 支持 `--fail-on high` 用于 CI。
- [ ] 支持 `--quiet`。
- [ ] 支持 `--verbose`。

### P0：交互式 Agent 体验

- [x] 增加 `chat` / REPL 模式。
- [x] 支持连续对话。
- [ ] 支持查看当前会话上下文。
- [ ] 支持引用文件。
- [x] 支持让 Agent 解释某个 finding。
- [x] 支持让 Agent 生成修复建议。
- [x] 支持流式输出。
- [ ] 支持 Ctrl+C 中断。
- [ ] 支持会话保存和恢复（Phase 2）。

### P0：安全确认体验

- [ ] 写文件前展示 diff。
- [ ] 执行命令前展示命令、工作目录、风险等级。
- [ ] 删除文件前强制二次确认。
- [ ] 网络访问前展示 URL 和原因。
- [ ] 支持 `--yes` 但默认不启用。
- [ ] CI 模式下禁止交互式危险操作。
- [ ] 所有确认记录写入审计日志。

---

## 8. 报告系统 TODO List

### P0：Markdown 报告

- [x] 生成项目安全总览。
- [x] 生成风险统计表。
- [ ] 按 severity 分组。
- [x] 每个 finding 包含证据和修复建议。
- [x] 生成可复制到 issue / PR 的说明。

### P0：JSON 报告

- [x] 定义稳定 JSON schema（已有 to_dict）。
- [x] JSON 报告增加扫描配置、扫描时间、项目元数据。
- [x] 包含所有 findings。
- [x] 支持被 CI 或其他工具消费。

### P1：SARIF 报告（Phase 3）

- [ ] 支持 SARIF 输出。
- [ ] 使结果可接入 GitHub Code Scanning。
- [ ] 映射 severity 到 SARIF level。
- [ ] 映射文件和行号。

---

## 9. 测试 TODO List

### P0：单元测试

- [x] 配置加载测试（待 config 模块完成后补全）。
- [ ] CLI 参数测试。
- [x] Tool registry 测试（已有 AgentToolDetector 测试）。
- [ ] 文件访问边界测试。
- [x] 风险模型测试（已有 Finding + Severity）。
- [x] 报告生成测试（已有 print_table / print_json）。

### P0：安全规则测试

- [x] Secret 检测样例。
- [x] Prompt injection 检测样例。
- [x] Agent tool 风险检测样例。
- [ ] RAG 注入检测样例。
- [ ] MCP tool 风险检测样例。
- [ ] 任意命令执行检测样例。
- [ ] 路径穿越检测样例。
- [ ] 补充至 10+ 安全测试样例（当前约 6 个，需补充）。

### P0：Agent 交互测试

- [ ] Agent 基本对话测试。
- [ ] Agent 调用 scan_project 工具测试。
- [ ] Agent 流式输出测试。
- [ ] Agent Ctrl+C 中断测试。

### P1：集成测试

- [ ] 构造一个示例 LangChain 项目。
- [ ] 构造一个示例 RAG 项目。
- [ ] 构造一个示例 MCP Server。
- [ ] 在这些示例项目上运行 `scan`。
- [ ] 验证 findings 是否符合预期。
- [ ] 验证报告格式是否稳定。

---

## 10. MVP 建议

### 10.1 第一版 MVP 目标

第一版不要做成完整 Claude Code 替代品，而是聚焦于：

"一个能对话的 AI 安全 Agent，拥有扫描能力，能在项目目录下发现安全问题并解释结果。"

Agent 在 MVP 阶段能"看"和"说"，不能"改"。

### 10.2 MVP 必须包含

- [x] 新项目名和 CLI 命令名：Poker CLI / `poker`。
- [x] `scan` 命令（直接调用扫描引擎）。
- [x] `chat` / REPL 模式（Agent 对话，可调用扫描能力）。
- [x] `init` 命令（初始化配置）。
- [x] `config` 命令（查看/修改配置）。
- [x] LangChain `@tool` 风险审计。
- [x] Secret 扫描。
- [x] Prompt 字符串安全审计。
- [x] Markdown 报告（table 格式）。
- [x] JSON 报告。
- [x] 基础配置系统（provider + API key + 项目级配置）。
- [x] Agent Runtime（基于 langchain-core）。
- [x] 安全 Agent System Prompt。
- [ ] 至少 10 个安全测试样例。
- [ ] Agent 交互基本测试。

### 10.3 MVP 暂不做（延后至后续阶段，非删除）

| 延后项                  | 目标阶段   | 理由                           |
| ----------------------- | ---------- | ------------------------------ |
| 自动修复写文件          | Phase 2    | MVP Agent 只读不改             |
| `review` 命令           | Phase 2    | scan 已覆盖核心，review 是增强 |
| `fix` 命令              | Phase 2    | 依赖写操作工具                 |
| `prompt-audit` 独立命令 | Phase 2    | MVP 中作为 scan 子集运行       |
| `tools-audit` 独立命令  | Phase 2    | 同上                           |
| `threat-model` 命令     | Phase 4    | 复杂度高，需要 Agent 深度推理  |
| 任意 shell 执行         | 视需求决定 | 风险极高，需充分设计确认机制   |
| 复杂 RAG 深度分析       | Phase 3    | 需要代码流分析能力             |
| 完整 MCP 运行时连接     | Phase 2    | MVP 只做静态 schema 分析       |
| SARIF 报告              | Phase 3    | 依赖 CI 模式                   |
| CI 模式                 | Phase 3    | 需要稳定报告格式和退出码规范   |
| Session 持久化          | Phase 2    | MVP 会话不需要持久化           |
| 多 Agent 协作           | Phase 4    | 远期目标                       |
| 插件市场                | 远期       | 需要先有稳定的 API 契约        |
| 云端平台                | 远期       | 产品形态可能变化               |

### 10.4 MVP 验收标准

- [ ] 在一个示例 LangChain Agent 项目中，能发现高风险工具暴露问题。
- [ ] 在一个示例 prompt 文件中，能发现 prompt injection 防护缺失。
- [ ] 在一个示例项目中，能发现硬编码 API key。
- [ ] `scan` 命令能输出 Markdown 和 JSON 报告。
- [ ] `chat` 命令能进入交互式 Agent 会话，回答安全问题。
- [ ] Agent 能在 chat 中调用扫描能力，解释 findings。
- [ ] 没有 API key 时，`help`、`init`、`version` 类命令仍可运行。
- [ ] 所有扫描默认只读。
- [ ] 所有高风险操作默认禁止或需要确认。

---

## 11. 后续演进路线

### Phase 1（MVP）：从 Demo 变成安全 Agent CLI

- [x] 重命名项目。
- [x] 重写文档。
- [x] 增加 `scan`。
- [ ] 增加基础安全规则。
- [x] 增加 Markdown / JSON 报告。
- [x] 增加 `chat` / REPL 模式。
- [x] 增加 Agent Runtime。
- [x] 增加配置系统。
- [ ] 增加测试样例。

### Phase 2：从 Agent CLI 变成交互式安全助手

- [ ] 增加 `review` 命令。
- [ ] 增加 `fix` 命令。
- [ ] 增加写操作 Agent 工具（write_file, apply_patch）。
- [ ] 增加独立 `prompt-audit` / `tools-audit` 命令。
- [ ] 支持项目上下文。
- [ ] 支持解释 finding。
- [ ] 支持生成修复建议。
- [ ] 支持展示 diff。
- [ ] 支持用户确认后应用修复。
- [ ] 支持会话持久化和恢复。
- [ ] MCP 静态 schema 分析。
- [ ] RAG 基础审计。

### Phase 3：从本地工具变成团队流程工具

- [ ] 支持 CI 模式。
- [ ] 支持 SARIF。
- [ ] 支持 baseline。
- [ ] 支持团队策略配置。
- [ ] 支持自定义规则。
- [ ] 支持审计日志。
- [ ] RAG 深度分析。

### Phase 4：形成 AI 安全特色能力

- [ ] 深度 Prompt Injection 检测。
- [ ] MCP 安全审计（运行时连接）。
- [ ] Agent 权限建模。
- [ ] `threat-model` 命令。
- [ ] AI 应用威胁建模。
- [ ] 自动生成安全测试用例。
- [ ] 多 Agent 协作。

---

## 12. 当前代码处理建议

### 可以保留

- [ ] Typer 作为 CLI 框架。
- [ ] Rich 作为终端 UI。
- [ ] OpenAI-compatible API 配置思路。
- [ ] Python 项目结构。
- [ ] Poetry 管理方式。

### 应该重写

- [ ] `README.md`。
- [ ] `QUICKSTART.md`。
- [x] `poker/cli.py` 中的命令设计。
- [x] 删除 `poker/agent.py` 中的通用助手 prompt。
- [x] 删除 `poker/tools/echo.py` 示例工具。
- [x] 删除 `poker/config.py` 的强制加载方式。

### 可以删除

- [x] `echo` 工具。
- [x] 泛化的 `run` 命令。
- [ ] 模板式 Roadmap。
- [ ] "通用 AI Agent Framework" 的定位描述。

### 迁移计划（对应第 4 节目录重构）

| 当前位置             | 迁移目标                                  | 说明         |
| -------------------- | ----------------------------------------- | ------------ |
| `poker/cli.py`       | `poker/cli/scan.py` + `poker/cli/chat.py` | 按命令拆分   |
| `poker/scanner.py`   | `poker/capabilities/scan/engine.py`       | 扫描编排     |
| `poker/detectors/`   | `poker/capabilities/scan/detectors/`      | 扫描器实现   |
| `poker/reporter.py`  | `poker/capabilities/scan/report.py`       | 报告生成     |
| `poker/models.py`    | `poker/models/finding.py`                 | 数据模型     |
| `poker/workspace.py` | `poker/workspace/`                        | 共享文件操作 |

---

## 13. 关键决策

### 已决定

- [x] 产品名：Poker CLI。
- [x] MVP 形态：Agent + 单一扫描能力。Agent 能"看"和"说"，不能"改"。
- [x] Agent Runtime 基于 langchain-core，不引入完整 langchain。
- [x] 第一批目标用户：开发 LLM 应用的 Python 开发者。
- [x] Python / LangChain / OpenAI-compatible 项目优先。
- [x] 所有扫描默认只读。
- [x] 自动修复 MVP 只生成 diff，不直接写入。
- [x] CI 输出支持 JSON 和 Markdown，SARIF 放到 Phase 3。
- [x] AI 安全能力作为差异化核心，不追求完整替代 Claude Code。
- [x] Agent Runtime 与能力模块硬隔离。
- [x] MCP 安全审计 Phase 2 再做。
- [x] 离线 / 本地模型通过 ChatModel 抽象支持，但不作为 MVP 验收条件。

### 待后续决定

- [ ] 是否允许工具自动修改代码？→ Phase 2 `fix` 命令时决定。
- [ ] 是否允许工具执行 shell 命令？→ 视未来需求决定，不做永久排除。
- [ ] CI 是否一等公民？→ Phase 3 决定。
- [ ] 报告格式是否需要 SARIF？→ Phase 3 决定。
- [ ] `run_command` Agent 工具是否最终引入？→ 需充分设计确认机制后再决定。

---

## 14. 错误处理与可观测性

### P0：错误处理策略

- [ ] LLM 调用失败处理：超时重试（指数退避）、限流降级、token 超限截断提示。
- [ ] 扫描器异常容错：文件编码错误跳过并记录、AST 解析失败跳过并记录、单个扫描器异常不影响其他扫描器。
- [ ] Agent 调用工具失败：结构化错误返回 + 用户提示，不静默跳过。
- [ ] 配置缺失处理：缺 API key 时给出明确指引，不抛模糊异常。
- [ ] 全局异常兜底：未预期异常输出友好提示 + 调试信息获取方式。

### P0：可观测性

- [ ] 日志级别：ERROR / WARNING / INFO / DEBUG，默认 WARNING。
- [ ] `--verbose` 输出 INFO 级别日志。
- [ ] `--debug` 输出 DEBUG 级别日志（含 LLM 请求/响应摘要）。
- [ ] 审计日志：所有 Agent 工具调用记录到本地审计文件。
- [ ] 扫描跳过记录：被跳过的文件和原因写入报告附录。
- [ ] Agent 执行 trace：记录 Agent 的每一步决策和工具调用链路（Phase 2）。

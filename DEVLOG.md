# Devlog

这个文件记录每次阶段性代码更新后的开发日志。

维护规则：

1. 每次更新代码后，都要更新 `README.md`
2. 每次更新代码后，都要在 `DEVLOG.md` 追加一条记录
3. 每完成一个阶段，都要在 `docs/` 下新增对应的阶段讲解文档
4. Devlog 要写清楚本次改了什么、为什么这么改、怎么验证、读者下一步应该看哪里
5. `docs/` 是本地学习讲解材料，当前由 `.gitignore` 忽略，不加入 git

## 2026-05-11 - Debug Log 全链路调试日志

### 改动内容

- 新增 `src/agent/debuglog.py`
  - `DebugLog` 以 JSONL 追加写事件
  - `to_debug_json()` 将 Pydantic、dict、list、SDK 对象转成可 JSON 化结构
  - 日志写入失败不会中断 Agent 主流程
- 更新 `src/agent/config.py` 和 `src/agent/models.py`
  - 新增 `debug_log_enabled`
  - 新增 `debug_log_path`
- 更新 `src/agent/main.py`
  - 新增 CLI 参数 `--debug-log`
  - 新增 CLI 参数 `--debug-log-path`
- 更新 `src/agent/runtime/builder.py`
  - 根据配置创建 `DebugLog`
  - 注入 model client、tool registry 和 core loop
- 更新 `src/agent/core/loop.py`
  - 记录实际 system prompt、用户请求、发给模型的 messages/tool schemas、assistant message
- 更新 `src/agent/providers/litellm_client.py`
  - 记录 LiteLLM request
  - 记录非流式 raw response
  - 记录流式 raw summary，避免供应商按字符切 chunk 时把日志刷爆
  - 记录归一化后的 `ModelTurn`
  - 对 `api_key/token/secret` 类 request 字段做脱敏
- 更新 `src/agent/core/tool_executor.py` 和 `src/agent/tools/registry.py`
  - 记录工具调用、pre/post hook、工具结果、权限判断、工具异常
- 更新 README
  - 增加调试日志使用方式、配置模板和事件范围说明
- 更新测试
  - 配置加载覆盖 debug 字段
  - loop 调试事件顺序
  - provider raw response 日志和密钥脱敏

### 为什么这么改

调试 Agent 时，问题通常不在单个函数里，而在“用户请求 → system prompt → model payload → raw model response → tool call → tool result → 下一轮 model input”的链路上。已有 `SessionStore` 只保存会话消息，不保存模型 SDK raw 返回、tool schema、权限决策和 hook 细节，排查 provider/tool-call 兼容问题时信息不够。

这次把日志设计为独立 `DebugLog`，由 runtime composition root 注入到 loop、provider 和 tools 边界。这样业务工具不需要知道调试系统，默认关闭时也不会改变现有执行路径。JSONL 适合长时间运行和增量查看，每行独立事件，后续可以用 `jq` 或脚本分析。

### 验证

```bash
uv run pytest tests/test_config.py tests/test_loop.py tests/test_provider_mock.py tests/test_runtime.py -q
uv run pytest -q
```

本次验证结果：`155 passed, 1 skipped, 9 warnings`。warnings 来自 ChromaDB legacy embedding function config，不是本次调试日志改动引入。

### 读者入口

- 调试日志核心：`src/agent/debuglog.py`
- 核心循环埋点：`src/agent/core/loop.py`
- 模型 raw 日志：`src/agent/providers/litellm_client.py`
- 工具调用日志：`src/agent/core/tool_executor.py`、`src/agent/tools/registry.py`
- 配置入口：`src/agent/config.py`、`src/agent/main.py`

## 2026-05-10 - Phase 06: CodeContext RAG 上下文层

## 2026-05-11 - CodeContext 后台索引与 Hybrid Search

### 改动内容

- 更新 `src/agent/code_context/indexer.py`
  - 新增 `start_background_index()`，支持进程内后台索引
  - 新增 `wait_background_index()`，方便测试和内部等待
  - 索引状态持久化到 `<codebase_id>.status.json`
  - `get_status()` 返回 `indexing / indexed / indexfailed / not_found`、`percentage`、`phase`、`current`、`total`
  - `search_code()` 改为 hybrid 检索：vector candidates + keyword candidates + RRF + line-range dedupe
- 新增 `src/agent/code_context/ranker.py`
  - BM25-like 关键词检索
  - RRF 排名融合
- 更新 `src/agent/code_context/chroma_store.py`
  - 新增 `documents_for_codebase()`，用于本地关键词通道
- 更新 `src/agent/tools/code_context.py`
  - `code_index` 新增 `background: bool`
- 更新测试
  - 后台索引进度和完成状态
  - `code_index(background=true)` 工具入口
  - exact identifier 通过关键词通道被提升
  - 重叠行区间结果去重

### 为什么这么改

同步索引闭环简单，但面试追问生产化时会问长任务如何不阻塞。后台索引和进度状态能展示从 MVP 到更接近 `claude-context` 的演进路径，同时复用现有工具模型，不引入新的服务进程。

纯向量搜索对代码里的精确标识符、配置名、错误码不够稳定。Hybrid search 用 dense/vector 处理语义相似，用 BM25-like keyword channel 处理精确词和符号，再用 RRF 融合两路排名。最后按文件和行区间去重，避免同一段代码重复占用上下文。

### 验证

```bash
uv run pytest tests/test_code_context_indexer.py tests/test_code_context_tools.py -q
uv run pytest -q
```

验证结果见本次最终回复。

### 读者入口

- 后台索引状态：`src/agent/code_context/indexer.py`
- Hybrid ranker：`src/agent/code_context/ranker.py`
- Chroma 文档枚举：`src/agent/code_context/chroma_store.py`
- 工具入口：`src/agent/tools/code_context.py`

### 改动内容

- 新增 `src/agent/code_context/`
  - `models.py`：CodeChunk、CodeSearchResult、CodeIndexStats、CodeIndexStatus
  - `file_rules.py`：默认支持扩展名、默认忽略规则、`.gitignore` / `.contextignore` 加载和代码文件遍历
  - `splitter.py`：Python AST-aware splitter 和通用 line-based splitter
  - `chroma_store.py`：ChromaDB `PersistentClient` 封装，固定 collection `agent_code_context`
  - `indexer.py`：同步 index/search/status/clear，文件级 sha256 snapshot 增量索引
- 新增 `src/agent/tools/code_context.py`
  - `code_index`
  - `code_search`
  - `code_index_status`
  - `code_index_clear`
- 更新 `src/agent/config.py`
  - 增加 `code_context_enabled`、Chroma 路径、collection 名、返回限长等配置
- 更新 `src/agent/runtime/builder.py`
  - 当 `code_context_enabled=true` 时创建 `CodeContextIndexer` 并注册工具
- 更新 `src/agent/prompt.py`
  - 增加 CodeContext 工具使用策略：陌生代码库、架构边界、自然语言代码定位时优先 `code_search`
- 更新 `src/agent/permissions.py`
  - `code_index_clear` 默认走审批，因为它会删除本地持久索引状态
- 新增测试
  - `tests/test_code_context_file_rules.py`
  - `tests/test_code_context_splitter.py`
  - `tests/test_code_context_indexer.py`
  - `tests/test_code_context_tools.py`
- 新增面试文档
  - `docs/phase-06-CodeContext-RAG讲解.md`
  - `docs/interview-04-CodeContext-RAG问答.md`

### 为什么这么改

原来的 `ContextGuard` 只解决 conversation context 的压缩问题，无法解决 codebase context 过大的问题。`claude-context` 的核心启发是把代码库作为外部语义上下文，通过索引和搜索按需召回代码片段。

这次没有直接接入 `@zilliz/claude-context-mcp`，而是在 Python Runtime 内实现 CodeContext 子系统。这样面试时可以讲清楚 Agent Context Management 的两层边界：`ContextGuard` 管对话上下文，`CodeContext` 管代码库上下文。ChromaDB 只作为向量存储后端，文件规则、chunking、增量索引、权限和工具语义都由本项目控制。

第一版选择同步索引、文件级 sha256 snapshot、单 Chroma collection + metadata filter，是为了保持闭环简单、测试清晰、面试可解释。后台索引、Merkle DAG、hybrid search、MMR 和 tree-sitter 多语言 AST 都作为后续演进点。

### 验证

```bash
uv run pytest tests/test_code_context_file_rules.py tests/test_code_context_splitter.py tests/test_code_context_indexer.py tests/test_code_context_tools.py
uv run pytest -q
```

验证结果见本次最终回复。

### 读者入口

- CodeContext 核心：`src/agent/code_context/indexer.py`
- Chroma store：`src/agent/code_context/chroma_store.py`
- Agent 工具：`src/agent/tools/code_context.py`
- Runtime 接线：`src/agent/runtime/builder.py`
- 阶段讲解：`docs/phase-06-CodeContext-RAG讲解.md`
- 面试问答：`docs/interview-04-CodeContext-RAG问答.md`

## 2026-04-20 - Prompt Runtime Metadata 与真实工具索引

### 改动内容

- 更新 `src/agent/prompt.py`
  - `SystemPromptBuilder.build()` 支持传入 `tool_schemas`
  - `Tools` section 会从真实工具 schema 渲染工具名、描述和参数名
  - `Runtime` section 新增当前日期、模型名、平台信息和 Python 版本
- 更新 `src/agent/runtime/builder.py`
  - `_build_runtime()` 在工具注册完成后再生成 system prompt
  - plugin 工具会进入最终 prompt 工具索引
  - `_attach_mcp_tools()` 发现 MCP 工具后刷新 system prompt，让 MCP 工具也进入索引
- 更新 `src/agent/runtime/runner.py`
  - `--print-system-prompt` 直接输出 runtime 已生成的 `config.system_prompt`
  - 避免打印时重新 build prompt 导致工具索引丢失
- 更新测试
  - `tests/test_memory_todo_prompt.py`
  - `tests/test_runtime.py`
- 更新 README 和本地阶段讲解文档；`docs/` 继续由 `.gitignore` 忽略，不加入 git

### 为什么这么改

`docs/12-Python实战技术选型.md` 的 prompt 管线要求动态部分包含日期、模型、平台信息，并且 prompt 里要有真实工具索引。之前 `SystemPromptBuilder` 的 `Tools` section 只是通用说明，且 system prompt 在 `ToolRegistry` 构建前生成，导致 plugin/MCP 这类动态工具不会出现在 prompt 中。

这次把 prompt 生成移动到工具注册之后，并允许 `SystemPromptBuilder` 接收 `ToolRegistry.schemas()`。这样 prompt 中展示的工具索引和实际模型可调用的工具 schema 来自同一个数据源，避免文档、prompt 和 registry 三份信息漂移。

### 验证

```bash
uv run pytest tests/test_memory_todo_prompt.py tests/test_runtime.py -q
uv run pytest -q
uv run agent --print-system-prompt --no-memory --no-todos --no-skills --no-tasks
git check-ignore -v docs/phase-05G-12技术选型对齐讲解.md
```

验证结果：

- Prompt/runtime 目标测试通过，`14 passed`
- 完整测试通过，见本次最终回复
- `--print-system-prompt` 可看到 `Current date`、`Model`、`Platform` 和 `Available tools`

### 读者入口

- Prompt builder：`src/agent/prompt.py`
- Runtime 接线：`src/agent/runtime/builder.py`
- Prompt 测试：`tests/test_memory_todo_prompt.py`
- Runtime 测试：`tests/test_runtime.py`

## 2026-04-20 - Phase 5G: 12 技术选型对齐补强

### 改动内容

- 新增 `src/agent/skills.py`
  - `SkillStore`
  - `SkillMetadata`
  - `SkillDocument`
  - 支持扫描 `skills/` 或 `workspace/skills/` 的 markdown 技能
  - prompt 只渲染技能索引，完整正文按需加载
- 新增 `src/agent/tools/skill.py`
  - `load_skill` 工具
  - 支持 `name` 和 `max_chars`
- 新增 `src/agent/tasks.py`
  - `TaskStore`
  - `TaskRecord`
  - 每个 DAG task 一个 JSON 文件
  - 支持 `blocked_by`、`ready()` 和完成上游后自动解锁下游
- 新增 `src/agent/tools/tasks.py`
  - `task_create`
  - `task_complete`
  - `task_list`
- 新增 `src/agent/background.py`
  - `BackgroundTaskManager`
  - `BackgroundResult`
  - 使用 `asyncio.create_task + asyncio.Queue` 返回后台任务结果
- 更新 `src/agent/prompt.py`
  - 新增 `Project Instructions` section，读取项目根目录 `CLAUDE.md`
  - 新增 `Skills` section
  - 新增 `Tasks` section
- 更新 `src/agent/runtime/builder.py`
  - 创建并注入 `SkillStore`
  - 创建并注入 `TaskStore`
  - 注册 `load_skill` 和 `task_*` 工具
- 更新 `src/agent/main.py` / `src/agent/config.py`
  - 新增 `--skills-dir / --tasks-dir`
  - 新增 `--no-skills / --no-tasks`
  - 新增 `enable_skills / skills_dir / enable_tasks / tasks_dir`
- 更新 `src/agent/memory.py`
  - 新增 `MemoryRecord`
  - 新增 `save_record()`，支持 `.memory/<name>.md` 命名记忆文件
  - 新增 `load_index()`
- 更新工具系统
  - `Tool.is_concurrency_safe()` 默认返回 false
  - `read_file` 支持 12 文档里的 `file_path / offset / limit`
  - `read_file` 和 `grep` 标记为并发安全
- 更新 `src/agent/subagent.py`
  - 新增 `spawn_background()`，返回后台子代理结果队列
- 更新 `src/agent/heartbeat.py`
  - 修复 `tick(now=None)` 真实运行路径缺少 `time` import 的问题
- 新增兼容入口
  - `src/agent/planning.py`
  - `src/agent/compact.py`
  - `src/agent/mcp_client.py`
- 新增和扩展测试
  - `tests/test_skill_loading.py`
  - `tests/test_task_dag.py`
  - `tests/test_background_tasks.py`
  - `tests/test_tools.py`
  - `tests/test_memory_todo_prompt.py`
  - `tests/test_subagent.py`
  - `tests/test_heartbeat_cron.py`
- 更新 README，并新增本地阶段讲解文档；`docs/` 继续由 `.gitignore` 忽略，不加入 git

### 为什么这么改

用户要求以 `docs/12-Python实战技术选型.md` 为规格补实现，而不是把文档改成现状。这次排除 `s15-s17 Agent Teams`，集中补齐文档中已经写明但代码缺口较大的能力。

Skill Loading 采用两层加载：prompt 只放技能索引，避免所有技能全文长期占用上下文；模型需要时再通过 `load_skill` 拉取完整 markdown。

Todo 和 Task System 明确拆开：Todo 是单会话轻量计划，Task System 是跨压缩/重启的 DAG 编排。DAG 用 `blocked_by` 表达依赖，不引入数据库，保持 JSON 文件可读、可调试。

BackgroundTaskManager 是对 12 文档里 `asyncio.create_task + Queue` 模式的通用实现。现有 DeliveryDaemon、Heartbeat、Cron 继续保留各自的业务循环；通用后台任务管理器用于后续长任务和后台子代理复用。

新增 `planning.py / compact.py / mcp_client.py` 只是兼容入口，不改变内部结构。这样读 12 文档时能按模块名跳到代码，同时真实实现仍保持当前更细的分层。

### 验证

```bash
uv run pytest tests/test_skill_loading.py tests/test_task_dag.py tests/test_background_tasks.py tests/test_tools.py tests/test_memory_todo_prompt.py tests/test_config.py -q
uv run pytest tests/test_subagent.py -q
uv run pytest tests/test_heartbeat_cron.py -q
uv run pytest -q
uv run agent --help
git check-ignore -v docs/phase-05G-12技术选型对齐讲解.md docs/12-Python实战技术选型.md
```

验证结果：

- 目标测试通过，`20 passed`
- Subagent 目标测试通过，`5 passed`
- Heartbeat/Cron 目标测试通过，`11 passed`
- 完整测试通过，`110 passed`
- CLI help 正常显示 `--skills-dir / --tasks-dir / --no-skills / --no-tasks`
- `git check-ignore` 确认 `docs/` 仍由 `.gitignore` 忽略，不加入 git

### 读者入口

- 总览入口：`README.md`
- 本阶段讲解：`docs/phase-05G-12技术选型对齐讲解.md`
- Skill Loading：`src/agent/skills.py`、`src/agent/tools/skill.py`
- DAG Task System：`src/agent/tasks.py`、`src/agent/tools/tasks.py`
- Background Tasks：`src/agent/background.py`
- Prompt 接线：`src/agent/prompt.py`
- 运行时接线：`src/agent/runtime/builder.py`
- 测试：`tests/test_skill_loading.py`、`tests/test_task_dag.py`、`tests/test_background_tasks.py`

## 2026-04-19 - Phase 5F: MCP、Worktree 与 Plugin System

### 改动内容

- 新增 `src/agent/mcp/`
  - `MCPServerConfig`
  - `MCPToolDefinition`
  - `StdioMCPClient`
  - `MCPToolRouter`
  - `MCPProxyTool`
  - 支持 `initialize / tools/list / tools/call`
- 更新 `src/agent/config.py`
  - 新增 `mcp_servers`
  - 新增 `plugin_dirs`
  - 新增 `enable_worktree_isolation`
  - 新增 `worktree_dir`
- 更新 `src/agent/main.py`
  - 运行时发现并注册 MCP 工具
  - 运行时发现并注册 plugin 工具
  - `--worktree-isolation` 开启后注册 worktree 工具
  - 新增 `--worktree-dir`
- 新增 `src/agent/worktree.py`
  - `WorktreeManager`
  - `WorktreeLease`
  - 基于 `git worktree add -b` 创建隔离工作区
  - 支持清理 worktree 和可选删除 branch
- 新增 `src/agent/tools/worktree.py`
  - `worktree_create`
  - `worktree_cleanup`
- 新增 `src/agent/plugins.py`
  - `PluginManifest`
  - `PluginToolConfig`
  - `PluginManager`
  - `PluginCommandTool`
- 新增和扩展测试
  - `tests/test_mcp.py`
  - `tests/test_worktree.py`
  - `tests/test_plugins.py`
  - `tests/test_config.py`
  - `tests/test_main.py`
- 更新 README，并新增第五阶段 5F 讲解文档；`docs/` 继续由 `.gitignore` 忽略，只保留本地

### 为什么这么改

第五阶段剩余的硬缺口是 MCP、worktree isolation 和 plugin system。这里选择最小但真实可运行的实现。

MCP 没有引入新依赖，而是先做 stdio JSON-RPC 子集：`initialize`、`tools/list`、`tools/call`。这样不用额外安装 SDK，也能展示 MCP 的核心工具发现与调用路径。发现到的工具被包装成 `mcp__server__tool`，进入同一个 `ToolRegistry`，因此权限系统、hook 和错误处理都能复用。

Worktree 没有直接把 Subagent 强行改成自动隔离，因为那会牵动文件工具的相对路径解析。当前先提供 `WorktreeManager` 和 `worktree_create / worktree_cleanup` 工具。Agent 可以显式创建 worktree，再把返回的 path 传给 `bash(workdir=...)` 或文件工具使用。

Plugin System 先做本地命令型插件：每个插件目录有 `plugin.json`，工具命令从 stdin 接收 JSON 参数，stdout 返回工具结果。插件工具同样进入 `ToolRegistry`，不会绕过权限系统。

### 验证

```bash
uv run pytest tests/test_mcp.py tests/test_worktree.py tests/test_plugins.py tests/test_config.py tests/test_main.py -q
uv run pytest -q
uv run agent --help
git check-ignore -v docs/phase-05F-MCP-Worktree-Plugin讲解.md docs/README.md
```

验证结果：

- 目标测试通过，`18 passed`
- 完整测试通过，`95 passed`
- CLI help 正常显示 `--worktree-isolation` 和 `--worktree-dir`
- `git check-ignore` 确认 `docs/` 仍由 `.gitignore` 忽略，不加入 git

### 读者入口

- 总览入口：`README.md`
- 第五阶段 5F 讲解：`docs/phase-05F-MCP-Worktree-Plugin讲解.md`
- MCP client：`src/agent/mcp/client.py`
- MCP router：`src/agent/mcp/router.py`
- Worktree：`src/agent/worktree.py`
- Worktree 工具：`src/agent/tools/worktree.py`
- Plugin：`src/agent/plugins.py`
- 运行时接线：`src/agent/main.py`
- 测试：`tests/test_mcp.py`、`tests/test_worktree.py`、`tests/test_plugins.py`

## 2026-04-19 - Phase 5E: ResilienceRunner

### 改动内容

- 更新 `src/agent/recovery.py`
  - 新增 `ResilienceRunner`
  - 新增 `SleepFunc`
  - 将原 `run_model_turn_with_recovery()` 的主体逻辑迁移进 runner
  - 保留 `run_model_turn_with_recovery()` 作为兼容入口，`loop.py` 不需要改
- 新增 `tests/test_resilience_runner.py`
  - 覆盖 `length / max_tokens` 截断续写
  - 覆盖 `overflow` 后调用 `ContextGuard.compact_history()` 并重试
- 更新 README，并新增第五阶段 5E 讲解文档；`docs/` 继续由 `.gitignore` 忽略，只保留本地

### 为什么这么改

5D 已经实现了 profile fallback，但模型 turn 内部的恢复逻辑仍然集中在函数里。第五阶段路线图提到三层恢复洋葱：profile 轮换、overflow 截断/压缩、tool-use loop。5E 把单次模型 turn 的恢复策略抽成 `ResilienceRunner`，让这三层边界更清晰：

- `FallbackModelClient` 负责 profile / key / model fallback
- `ResilienceRunner` 负责 timeout / backoff / overflow compact / continuation
- `loop.py` 负责 tool-use loop 和 max iterations

保留旧函数入口是为了降低改动面。`loop.py` 继续调用 `run_model_turn_with_recovery()`，但函数内部委托给 runner。这样既完成了结构收敛，又避免把 Agent loop 和恢复策略绑在一起。

### 验证

```bash
uv run pytest tests/test_resilience_runner.py tests/test_recovery.py tests/test_loop.py -q
uv run pytest -q
uv run agent --help
git check-ignore -v docs/phase-05E-ResilienceRunner讲解.md docs/README.md
```

验证结果：

- 目标测试通过，`5 passed`
- 完整测试通过，`84 passed`
- CLI help 正常显示
- `git check-ignore` 确认 `docs/` 仍由 `.gitignore` 忽略，不加入 git

### 读者入口

- 总览入口：`README.md`
- 第五阶段 5E 讲解：`docs/phase-05E-ResilienceRunner讲解.md`
- 恢复运行器：`src/agent/recovery.py`
- Agent loop 调用点：`src/agent/loop.py`
- Profile fallback：`src/agent/profiles.py`
- 测试：`tests/test_resilience_runner.py`、`tests/test_recovery.py`、`tests/test_loop.py`

## 2026-04-19 - Phase 5D: Profile 与 Fallback Model

### 改动内容

- 新增 `src/agent/profiles.py`
  - `ModelProfile`
  - `ProfileManager`
  - `FallbackModelClient`
  - `AllProfilesExhaustedError`
  - 支持 profile cooldown 和 `api_key_env` 运行时取 key
- 更新 `src/agent/config.py`
  - 新增 `fallback_models`
  - 新增 `model_profiles`
  - 新增 `resolved_model_profiles()`，合并 TOML profile 和 CLI fallback 模型
- 更新 `src/agent/main.py`
  - 新增 `_build_model_client()`
  - 单 profile 使用 `LiteLLMModelClient`
  - 多 profile 使用 `FallbackModelClient`
  - CLI 新增 `--fallback-models`
- 更新 `src/agent/recovery.py`
  - `classify_error()` 支持 `rate_limit / auth / timeout / overflow / billing / unknown`
  - overflow 分类继续接入已有 context compaction 逻辑
- 新增和扩展测试
  - `tests/test_profiles.py`
  - `tests/test_recovery.py`
  - `tests/test_config.py`
  - `tests/test_main.py`
- 更新 README，并新增第五阶段 5D 讲解文档；`docs/` 继续由 `.gitignore` 忽略，只保留本地

### 为什么这么改

第五阶段要求支持多 profile / 多 key / fallback model。这里先实现模型调用层的 fallback，而不是一次性重构完整 `ResilienceRunner`。

设计上保持 Agent loop 不变：`loop.py` 仍然只依赖 `ModelClient` 协议。`FallbackModelClient` 作为 wrapper 包住多个 `LiteLLMModelClient`，对外仍然暴露同一个 `run_turn()` 接口。

Profile 配置只保存环境变量名，不保存真实 key。这样 `.zx-code/config.toml` 可以提交或展示结构，但不会泄漏密钥。运行时再从 `api_key_env` 读取真实 key，并传给 `litellm` 的 `api_key` 参数。

Fallback 只处理 `rate_limit / auth / billing / timeout` 这类可恢复故障；`unknown` 错误直接抛出。这样可以避免把本地代码 bug、tool schema bug 或 provider response 解析 bug 错误地隐藏成“换个模型就好了”。

### 验证

```bash
uv run pytest tests/test_profiles.py tests/test_recovery.py tests/test_config.py tests/test_main.py -q
uv run pytest -q
uv run agent --help
git check-ignore -v docs/phase-05D-Profile与Fallback讲解.md docs/README.md
```

验证结果：

- 目标测试通过，`11 passed`
- 完整测试通过，`82 passed`
- CLI help 正常显示 `--fallback-models`
- `git check-ignore` 确认 `docs/` 仍由 `.gitignore` 忽略，不加入 git

### 读者入口

- 总览入口：`README.md`
- 第五阶段 5D 讲解：`docs/phase-05D-Profile与Fallback讲解.md`
- Profile 与 fallback：`src/agent/profiles.py`
- 错误分类：`src/agent/recovery.py`
- 配置读取：`src/agent/config.py`
- 运行时接线：`src/agent/main.py`
- 测试：`tests/test_profiles.py`、`tests/test_recovery.py`、`tests/test_config.py`、`tests/test_main.py`

## 2026-04-19 - Phase 5C: Subagent

### 改动内容

- 新增 `src/agent/subagent.py`
  - `SubagentRunner`
  - `SubagentRunResult`
  - `SubagentRecursionError`
  - 子 Agent 独立 session id
  - 最大递归深度限制
- 新增 `src/agent/tools/subagent.py`
  - 暴露 `subagent_run` 工具
  - 支持 `task` 和 `label`
  - 返回子会话 id、执行深度和最终文本
- 更新 `src/agent/tools/__init__.py`
  - `build_default_registry()` 支持按需注入 `SubagentRunner`
  - 到达最大深度时不注册 `subagent_run`
- 更新 `src/agent/main.py`
  - `_build_runtime()` 支持 `lane_scheduler` 和 `subagent_depth`
  - Gateway / Heartbeat / Cron 触发 Agent turn 时都会传递同一个 `LaneScheduler`
  - 新增 `--subagent-max-depth` 和 `--no-subagents`
- 更新 `src/agent/config.py`
  - 新增 `enable_subagents`
  - 新增 `subagent_max_depth`
- 更新 `src/agent/lanes.py`
  - 支持当前 worker 内的嵌套 lane job
  - 避免主 Agent 在 `main` lane 中同步等待 `subagent` lane 时死锁
- 新增和扩展测试
  - `tests/test_subagent.py`
  - `tests/test_lanes.py`
  - `tests/test_main.py`
- 更新 README，并新增第五阶段 5C 讲解文档；`docs/` 继续由 `.gitignore` 忽略，只保留本地

### 为什么这么改

第五阶段要求补齐 Subagent。这里先做最小但真实可运行的子代理架构，而不是直接做复杂的多 worktree 并发系统。

核心设计是把子代理做成工具能力：主 Agent 通过 `subagent_run` 把聚焦任务交给子 Agent，子 Agent 使用独立 child session 跑完整 Agent loop，再把最终文本作为工具结果返回。这样主会话不会被子 Agent 的中间探索污染。

递归限制没有交给模型自觉遵守，而是在 `_build_runtime()` 里通过工具注册表控制：当 `subagent_depth >= subagent_max_depth` 时，子 Agent 不再获得 `subagent_run` 工具。

Lane 嵌套执行是本阶段的运行时关键点。主 Agent 已经在 `main` lane worker 中执行，如果 `subagent_run` 再把子任务排到同一个 worker 队列里，主任务会等待子任务，而 worker 又无法取出子任务，形成死锁。因此 `LaneScheduler` 用 `ContextVar` 识别当前 worker，遇到同一个 scheduler 的嵌套调用时直接内联执行，同时仍然记录 lane history。

### 验证

```bash
uv run pytest tests/test_subagent.py tests/test_lanes.py tests/test_main.py -q
uv run pytest -q
uv run agent --help
git check-ignore -v docs/phase-05C-Subagent讲解.md docs/README.md
```

验证结果：

- 目标测试通过，`11 passed`
- 完整测试通过，`76 passed`
- CLI help 正常显示 `--subagent-max-depth` 和 `--no-subagents`
- `git check-ignore` 确认 `docs/` 仍由 `.gitignore` 忽略，不加入 git

### 读者入口

- 总览入口：`README.md`
- 第五阶段 5C 讲解：`docs/phase-05C-Subagent讲解.md`
- 子代理运行时：`src/agent/subagent.py`
- 子代理工具：`src/agent/tools/subagent.py`
- 运行时接线：`src/agent/main.py`
- Lane 嵌套执行：`src/agent/lanes.py`
- 测试：`tests/test_subagent.py`、`tests/test_lanes.py`、`tests/test_main.py`

## 2026-04-19 - Phase 5B: Delivery Daemon

### 改动内容

- 更新 `src/agent/delivery.py`
  - 新增 `DeliveryDaemon`
  - `DeliveryRunner` 增加 async lock，避免同步投递和后台 daemon 重复发送同一条消息
  - `deliver_ready_once()` 在同一把锁内批量处理 ready entries
- 更新 `src/agent/main.py`
  - watch loop 启动 `DeliveryDaemon`
  - watch loop 退出时关闭 daemon
  - 主动任务不再依赖每轮手动 `drain_delivery()`
- 更新 `src/agent/config.py` 和 CLI
  - 新增 `delivery_daemon_interval_s`
  - 新增 `--delivery-daemon-interval`
- 扩展 `tests/test_delivery.py`
  - 覆盖后台 daemon 自动投递 ready entry
  - 覆盖并发投递同一条消息不会重复发送
- 更新 README，并新增第五阶段 5B 讲解文档；`docs/` 继续由 `.gitignore` 忽略，只保留本地

### 为什么这么改

第四阶段虽然有 `DeliveryQueue`，但实际投递依赖用户回复后的同步尝试和 watch loop 中的 `drain_delivery()`。这意味着没有独立后台任务持续处理失败重试和主动输出。

5B 把投递重试独立成 `DeliveryDaemon`。用户回复仍然立即尝试发送一次，失败后保留在 `queued/`；后台 daemon 按 `next_retry_at` 继续投递。Heartbeat 和 Cron 产生的主动输出也走同一个 daemon。

`DeliveryRunner` 加锁是为了防止两个路径同时投递同一个 delivery id：比如用户回复后的同步投递和后台 daemon 恰好同时看到同一条 queued entry。

### 验证

```bash
uv run pytest tests/test_delivery.py -q
uv run pytest -q
uv run agent --help
```

验证结果：

- 目标测试通过，`8 passed`
- 完整测试通过，`69 passed`
- CLI help 正常显示 `--delivery-daemon-interval`

### 读者入口

- 总览入口：`README.md`
- 第五阶段 5B 讲解：`docs/phase-05B-Delivery-Daemon讲解.md`
- 可靠投递与 daemon：`src/agent/delivery.py`
- Watch loop 接入：`src/agent/main.py`
- 测试：`tests/test_delivery.py`

## 2026-04-19 - Phase 5A: Priority Lane 与 Cron 状态持久化

### 改动内容

- 新增 `src/agent/lanes.py`
  - `LaneScheduler`
  - `LaneRunRecord`
  - `main > subagent > cron > heartbeat` 优先级
  - 基于 `asyncio.PriorityQueue` 的协作式调度
  - 记录每个 lane job 的等待时间、执行时间和结果状态
- 更新 `src/agent/main.py`
  - 用户消息进入 `main` lane
  - Heartbeat agent turn 进入 `heartbeat` lane
  - Cron agent turn 进入 `cron` lane
  - watch loop 中 heartbeat / cron tick 改为后台 task，避免主动任务阻塞通道轮询
- 更新 `src/agent/cron.py`
  - `CronScheduler` 支持 `state_path`
  - 持久化 `last_fired_at / next_run_at` 到 `.agent/cron-state.json`
  - 进程重启后恢复 cron job 的调度状态
- 新增 `tests/test_lanes.py`
- 扩展 `tests/test_heartbeat_cron.py`，覆盖 cron 状态持久化
- 更新 README，并新增第五阶段 5A 讲解文档；`docs/` 继续由 `.gitignore` 忽略，只保留本地

### 为什么这么改

第五阶段的完整范围很大，包含 resilience、profile fallback、lanes、subagent、MCP、worktree。直接全部实现会让边界失控，所以这次先切出 Phase 5A：把长期运行时最核心的调度秩序补上。

`LaneScheduler` 选择协作式调度而不是抢占式调度，因为 LLM 调用本身无法在 mid-turn 安全暂停。新的高优先级任务会在当前 turn 结束后优先执行，这和真实 Agent Runtime 的约束一致。

Cron 状态持久化补齐了第四阶段的一个运行时缺口：没有状态文件时，watch 进程重启可能导致 `every / cron` 任务重复触发。现在状态跟随 `.agent/cron-state.json` 恢复。

### 验证

```bash
uv run pytest tests/test_lanes.py tests/test_heartbeat_cron.py -q
uv run pytest -q
uv run agent --help
```

验证结果：

- 目标测试通过，`12 passed`
- 完整测试通过，`67 passed`
- CLI help 正常显示

### 读者入口

- 总览入口：`README.md`
- 第五阶段 5A 讲解：`docs/phase-05A-Priority-Lane与Cron状态持久化讲解.md`
- Lane 调度：`src/agent/lanes.py`
- Watch loop 接入：`src/agent/main.py`
- Cron 状态持久化：`src/agent/cron.py`
- 测试：`tests/test_lanes.py`、`tests/test_heartbeat_cron.py`

## 2026-04-18 - Phase 4: 可靠投递与主动调度

### 改动内容

- 新增 `src/agent/delivery.py`
  - `DeliveryEntry`
  - `DeliveryQueue`
  - `DeliveryRunner`
  - `chunk_message()`
  - queued / sent / failed 三目录投递状态
  - `tmp + fsync + os.replace` 原子写入
  - 指数退避、jitter、最大尝试次数和失败目录
- 新增 `src/agent/heartbeat.py`
  - `ActivityTracker`
  - `HeartbeatConfig`
  - `HeartbeatRunner`
  - 用户活跃或 Agent 正在处理时跳过 Heartbeat
  - `HEARTBEAT_OK` sentinel 不推送
- 新增 `src/agent/cron.py`
  - `CronJob`
  - `CronScheduler`
  - 支持 `at / every / cron`
  - 支持从 `.zx-code/cron.json` 或 `--cron-jobs` 加载任务
- 更新 `src/agent/gateway.py`，把直接 `channel.send()` 改成先写 `DeliveryQueue` 再由 `DeliveryRunner` 投递
- 更新 `src/agent/main.py`，在 watch loop 中驱动 `receive_once / heartbeat.tick / cron.tick / drain_delivery`
- 更新 `src/agent/config.py` 和 CLI，新增 delivery、heartbeat、cron 配置参数
- 更新 `pyproject.toml` 和 `uv.lock`，加入 `croniter` 作为 cron 表达式解析依赖
- 新增 `tests/test_delivery.py` 和 `tests/test_heartbeat_cron.py`
- 更新 README，并新增第四阶段讲解文档；`docs/` 按当前要求继续由 `.gitignore` 忽略，只保留本地

### 为什么这么改

第三阶段完成了手机入口，但出站回复仍然存在“生成了答案但发送失败就丢”的风险。第四阶段把出站消息统一放到 `DeliveryQueue`，让用户回复、Heartbeat 输出和 Cron 输出都走同一套可靠投递路径。

这次实现遵循 `docs/12-Python实战技术选型.md` 的方向：不用数据库，先用可读、可测试、可恢复的文件持久化；后台行为用 `asyncio` tick 组织；重试采用自定义指数退避，方便后续接入限流和错误分类。

### 验证

```bash
uv run pytest -q
uv run agent --help
```

验证结果：

- `uv run pytest -q` 通过，`64 passed`
- CLI help 正常显示 delivery、heartbeat、cron 参数

裸 `pytest -q` 在当前全局 Python 环境会因为没有安装 `python-frontmatter` 失败；项目验证以 `uv run pytest -q` 为准。

### 读者入口

- 总览入口：`README.md`
- 第四阶段讲解：`docs/phase-04-可靠投递与主动调度讲解.md`
- 可靠投递：`src/agent/delivery.py`
- Heartbeat：`src/agent/heartbeat.py`
- Cron：`src/agent/cron.py`
- Gateway 接入：`src/agent/gateway.py`
- CLI wiring：`src/agent/main.py`
- 测试：`tests/test_delivery.py`、`tests/test_heartbeat_cron.py`

## 2026-04-17 - Phase 3 Hardening: 安全与持久化加固

### 改动内容

- `sessions.py`：JSONL 追加写增加 `fcntl.flock` 文件锁，防止并发写入损坏 JSON 行
- `memory.py`：写入改为 write-to-temp + `os.rename()` 原子替换，中断时不丢文件
- `todo.py`：同上，改为原子写入
- `permissions.py`：新增 `write_file` / `edit_file` 权限检查——敏感路径（`/etc/`、`.ssh/`、`.env` 等）和工作目录外路径默认需要确认；新增 `working_dir` 参数和 `sensitive_paths` 模式列表
- `tools/read_file.py`、`tools/write_file.py`、`tools/edit_file.py`：增加符号链接检测，拒绝跟踪 symlink，防止路径遍历
- `context.py`：截断历史时新增 `_safe_split_index()`，不在 assistant+tool_calls 和对应 tool result 之间切断，避免模型丢失工具执行结果
- `channels/telegram.py`：新增 `max_buffer_size = 1000`，`_media_groups` 和 `_text_buffer` 超限时自动 flush，防止内存无限增长
- `tools/registry.py`：工具执行异常保留异常类型和最近 3 层 traceback，方便调试

### 为什么这么改

对照 claw0 的 s08（Write-Ahead Queue）和 s09（Resilience）思路做了一次安全审查。发现三个持久化模块（session、memory、todo）都存在 crash 丢数据风险，文件工具没有权限检查且可以跟踪 symlink，上下文截断可能在错误位置切断导致模型丢失工具结果。这些问题在单机 CLI 下不明显，但多通道持续运行时会暴露。

### 验证

```bash
uv run pytest -q
```

验证结果：`42 passed`，所有测试通过。

### 读者入口

- 原子写入：`src/agent/sessions.py`、`src/agent/memory.py`、`src/agent/todo.py`
- 文件工具权限与 symlink 检查：`src/agent/permissions.py`、`src/agent/tools/read_file.py`、`src/agent/tools/write_file.py`、`src/agent/tools/edit_file.py`
- 上下文截断安全边界：`src/agent/context.py`
- Telegram 缓冲区上限：`src/agent/channels/telegram.py`
- 工具异常信息增强：`src/agent/tools/registry.py`

## 2026-04-17 - Phase 3 Patch: 参考 claw0 接通 Telegram / 飞书

### 改动内容

- 参考 `../claw0/sessions/zh/s04_channels.py` 的 Channel/Gateway 思路，补强第三阶段移动端接入
- `TelegramChannel` 新增 offset 文件持久化、allowed chats、forum topic 归一化、文本合并、媒体组缓冲、长消息切块和 `sendChatAction`
- `FeishuChannel` 从占位实现升级为 webhook 通道，支持 challenge、`verification_token` 校验、text/post/image/file 解析、群聊 mention 过滤、`tenant_access_token` 缓存和 `im/v1/messages` 文本发送
- `main.py` 新增 `--watch` 通道监听模式，Telegram 可持续长轮询，飞书可启动本地 webhook server 并从队列消费事件
- `config.py` 新增 Telegram/飞书通道配置项
- README、第三阶段讲解文档和路线图同步更新真实连接方式
- 通道测试从 CLI/Telegram 基础解析扩展到 Telegram offset/topic/chunk 和飞书 webhook/send

### 为什么这么改

上一版第三阶段只做到了 Gateway 内核和 Telegram 最小单次拉取，飞书还是接口占位。  
这次把 claw0 里已经验证过的方向迁移到当前项目：平台差异留在 Channel，所有入站消息都先归一化为 `InboundMessage`，再走同一条 Gateway 和 Agent Brain。

Telegram 采用拉取模型，重点是 offset、topic、切块和持续监听。飞书采用 webhook 模型，重点是本地 HTTP server、事件解析、token 校验和发送 API。

### 验证

```bash
pytest -q
uv run pytest -q
uv run agent --help
uv run agent --channel telegram --telegram-token test --no-memory --no-todos --print-system-prompt
uv run agent --channel feishu --feishu-app-id app --feishu-app-secret secret --feishu-webhook-port 8787 --no-memory --no-todos --print-system-prompt
```

验证结果：

- `pytest -q` 通过，`42 passed`
- `uv run pytest -q` 通过，`42 passed`
- CLI help 正常显示 Telegram/飞书新增参数和 `--watch`
- Telegram / 飞书通道下的 prompt 打印路径正常

### 读者入口

- 连接方式：`README.md`
- 第三阶段讲解：`docs/phase-03-通道网关与手机接入讲解.md`
- Telegram 通道：`src/agent/channels/telegram.py`
- 飞书通道：`src/agent/channels/feishu.py`
- CLI wiring：`src/agent/main.py`
- 通道测试：`tests/test_channels.py`

## 2026-04-17 - Phase 3: 多通道 Gateway

### 改动内容

- 新增 `InboundMessage`、`Channel`、`ChannelManager`
- 新增 `CLIChannel`，让本地 CLI 输入也能归一化成通道消息
- 新增 `TelegramChannel`，使用标准库 HTTP 适配 `getUpdates / sendMessage`，并提供 Telegram update 到 `InboundMessage` 的解析
- 初版新增 `FeishuChannel` 预留边界，后续 patch 已升级为 webhook 通道
- 新增 `build_session_key()`，支持 `per-account-channel-peer / per-channel-peer / per-peer / per-agent`
- 新增 `BindingTable`，支持默认 agent、最具体规则优先、手动 switch 和 force route
- 新增 `Gateway`，把不同通道的入站消息统一路由到 Agent Brain，再通过原通道回复
- 更新 CLI，新增 `--channel / --account-id / --agent-id / --default-agent-id / --force-agent-id / --dm-scope / --telegram-token / --telegram-offset / --telegram-timeout`
- 更新 README，并新增第三阶段讲解文档
- 调整 `.gitignore`，不再忽略 `docs/`，保证阶段讲解文档可以进入版本控制
- 补充 README 里的 Telegram 连接手册和飞书接入状态说明

### 为什么这么改

第二阶段的 Agent 已经能连续使用，但入口仍然是 CLI。第三阶段的目标是把入口抽象出来，让 CLI、Telegram、飞书这类平台都先归一化成同一种 `InboundMessage`，再进入同一条 Agent Brain。

这次没有把 Telegram 逻辑塞进主循环，而是拆成：

- 通道层只负责 `receive / send`
- 网关层只负责路由和会话 key
- Agent Brain 继续复用 `run_task()`

这样后续做 DeliveryQueue、Heartbeat、Cron 时，不需要重写 Agent 核心。

### 验证

```bash
pytest -q
uv run pytest -q
PYTHONPATH=src python3 -m agent.main --help
PYTHONPATH=src python3 -m agent.main --channel telegram --no-memory --no-todos --print-system-prompt
uv run agent --help
uv run agent --channel telegram --no-memory --no-todos --print-system-prompt
```

验证结果：

- `pytest -q` 通过，`32 passed`
- `uv run pytest -q` 通过，`32 passed`
- CLI help 正常显示第三阶段参数
- Telegram 通道下的 prompt 打印路径正常

### 读者入口

- 总览入口：`README.md`
- 第三阶段讲解：`docs/phase-03-通道网关与手机接入讲解.md`
- Gateway 核心：`src/agent/gateway.py`
- 通道抽象：`src/agent/channels/base.py`
- CLI 通道：`src/agent/channels/cli.py`
- Telegram 通道：`src/agent/channels/telegram.py`
- 路由测试：`tests/test_gateway.py`
- 通道测试：`tests/test_channels.py`

## 2026-04-17 - Phase 2: 持久化运行时

### 改动内容

- 新增 `SessionStore`，使用 JSONL append-only 保存会话消息
- 新增 `ContextGuard`，在模型调用前处理长上下文、长 tool result 和 compact 后的孤立 tool message
- 新增 `SystemPromptBuilder`，按 `Identity / Operating Rules / Project / Tools / Memory / Todos / Runtime` 分层构建 prompt
- 新增 `.memory/MEMORY.md` 记忆系统
- 新增 `TodoManager` 和 todo 工具：`todo_create / todo_update / todo_complete / todo_list`
- 新增 `PermissionManager`，在 `ToolRegistry` 层统一执行 `allow / deny / ask` 策略
- 新增 `ConfigLoader`，支持 `CLI > 项目 > 用户` 三层配置
- 更新 CLI，支持 `--session-id / --data-dir / --context-max-chars / --no-memory / --no-todos / --print-system-prompt`
- 更新 README，并新增第二阶段讲解文档

### 为什么这么改

第一阶段只有单次 Agent loop。第二阶段的目标是把它升级成可连续使用的本地助手，所以重点不是增加更多工具，而是补齐运行时状态：

- 会话要能恢复
- 历史要能控长
- prompt 要能解释和调试
- 记忆和 todo 要能跨轮次存在
- 工具执行要有安全边界
- 配置要能按用户、项目、CLI 分层覆盖

### 验证

```bash
pytest -q
uv run pytest -q
uv run agent --help
uv run agent --no-memory --no-todos --print-system-prompt
```

验证结果：

- `pytest -q` 通过，`23 passed`
- `uv run pytest -q` 通过，`23 passed`
- CLI help 正常
- 分层 system prompt 正常打印

### 读者入口

- 总览入口：`README.md`
- 第二阶段讲解：`docs/phase-02-持久化上下文权限记忆讲解.md`
- 路线图：`docs/ZX-code.md`
- 核心运行链路：`src/agent/loop.py`
- CLI wiring：`src/agent/main.py`

## 2026-04-17 - Phase 1: 单 Agent 核心

### 改动内容

- 初始化 Python 项目结构和 `pyproject.toml`
- 实现 `ModelClient` 抽象和 `LiteLLMModelClient`
- 实现 Agent 主循环 `run_task()`
- 实现基础工具系统：`Tool`、`ToolRegistry`
- 接入五个基础工具：`bash / read_file / write_file / edit_file / grep`
- 实现 CLI 一次性执行模式和 REPL 模式
- 增加最小流式输出和基础错误恢复
- 补充 loop、tools、provider mock 测试

### 为什么这么改

第一阶段目标是先跑通最小闭环：

```text
user input -> model -> tool use -> tool_result -> final answer
```

为了后续阶段可扩展，第一阶段就把边界拆成：

- `models`
- `providers`
- `tools`
- `loop`
- `main`

CLI 只是入口，不承载核心业务逻辑。

### 验证

```bash
pytest -q
uv run pytest -q
uv run agent --help
```

### 读者入口

- 第一阶段讲解：`docs/phase-01-单Agent核心讲解.md`
- 主循环：`src/agent/loop.py`
- 工具注册：`src/agent/tools/registry.py`
- LiteLLM 适配：`src/agent/providers/litellm_client.py`

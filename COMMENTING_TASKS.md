# 注释治理任务表

目标：给现有 Python 代码补上“应有的注释”，重点是模块职责、公共类型/函数契约、复杂流程和不直观的边界条件。不要给显而易见的赋值、简单包装函数、测试断言堆机械注释。

## 注释标准

| 类型 | 应该补什么 | 不应该补什么 |
| --- | --- | --- |
| 模块 docstring | 这个文件在系统里的职责、输入输出边界、和相邻模块的关系 | 文件名的重复解释 |
| 类 docstring | 这个类维护什么状态、生命周期、线程/异步约束、失败语义 | 字段逐字翻译 |
| 公共函数 docstring | 参数含义、返回协议、异常/错误返回、调用者需要知道的副作用 | 简单 getter/setter |
| 复杂块注释 | 为什么这样做、兼容旧协议的原因、并发/截断/恢复的关键判断 | “循环列表”“设置变量” |
| 测试注释 | 测试保护的行为契约，尤其是回归场景 | 每个 assert 的解释 |

## 执行顺序

| 批次 | 模块 | 文件 | 注释重点 | 验收 |
| --- | --- | --- | --- | --- |
| 1 | Tool 协议与执行链 | `src/agent/models.py`, `src/agent/tools/base.py`, `src/agent/core/tool_executor.py`, `src/agent/tools/truncator.py`, `src/agent/tools/registry.py` | `ToolEnvelope`/`ToolResult` 边界、兼容旧 content 的原因、tool 执行/截断/trace/cache 的数据流 | `pytest tests/test_tool_base.py tests/test_tool_executor.py tests/test_truncator.py tests/test_tools.py` |
| 2 | 内置工具 | `src/agent/tools/read.py`, `write.py`, `edit.py`, `bash.py`, `grep.py`, `code_context.py`, `memory.py`, `todo.py`, `tasks.py`, `skill.py`, `tool_search.py`, `subagent.py`, `team.py`, `worktree.py`, `circuit_breaker.py`, `__init__.py` | 每个工具的 envelope data/text/context 契约、权限/文件边界、外部副作用 | `pytest tests/test_tools.py tests/test_code_context_tools.py tests/test_team_tools.py tests/test_permissions.py` |
| 3 | Agent 主循环 | `src/agent/core/loop.py`, `context.py`, `recovery.py`, `src/agent/runtime/runner.py`, `builder.py`, `infra.py`, `utils.py`, `markdown_stream.py` | 消息上下文、compaction/recovery、runtime 组装、stream 渲染和模型/tool 的协作关系 | `pytest tests/test_loop.py tests/test_runtime.py tests/test_context.py tests/test_recovery.py tests/test_markdown_stream.py` |
| 4 | Provider 与 OpenAI SDK | `src/agent/providers/base.py`, `openai_client.py`, `__init__.py` | provider 抽象、Responses/stream 事件转换、reasoning/raw trace 的边界 | `pytest tests/test_provider_mock.py tests/test_loop.py` |
| 5 | CodeContext | `src/agent/code_context/models.py`, `chroma_store.py`, `file_rules.py`, `splitter.py`, `indexer.py`, `sparse_index.py`, `ranker.py`, `__init__.py` | 索引生命周期、文件规则、chunk/sparse/rank 的职责分层、Chroma 失败/降级语义 | `pytest tests/test_code_context_indexer.py tests/test_code_context_splitter.py tests/test_code_context_file_rules.py tests/test_code_context_tools.py` |
| 6 | 状态存储 | `src/agent/state/sessions.py`, `tasks.py`, `skills.py`, `memory.py`, `memory_search.py`, `todo.py`, `__init__.py` | 本地状态文件格式、session/task/memory/todo 生命周期、搜索和持久化边界 | `pytest tests/test_sessions.py tests/test_task_dag.py tests/test_memory_todo_prompt.py tests/test_skill_loading.py` |
| 7 | 多 Agent / worktree | `src/agent/agents/subagent.py`, `team.py`, `worktree.py`, `__init__.py` | 子 agent 调度、team 通信、worktree 隔离策略、失败回收 | `pytest tests/test_subagent.py tests/test_team.py tests/test_worktree.py` |
| 8 | Channel / Gateway / Delivery | `src/agent/channels/base.py`, `cli.py`, `telegram.py`, `gateway.py`, `delivery.py`, `__init__.py` | 入站消息、delivery 语义、CLI/Telegram 差异、媒体和队列边界 | `pytest tests/test_channels.py tests/test_gateway.py tests/test_delivery.py` |
| 9 | Scheduling | `src/agent/scheduling/activity.py`, `background.py`, `cron.py`, `heartbeat.py`, `lanes.py`, `__init__.py` | heartbeat/cron/background task 的时间语义、lane 调度、公平性和停止条件 | `pytest tests/test_heartbeat_cron.py tests/test_background_tasks.py tests/test_lanes.py` |
| 10 | MCP / Plugin / Hook | `src/agent/mcp/client.py`, `router.py`, `__init__.py`, `src/agent/plugins.py`, `src/agent/hooks.py` | 外部工具输出归一化、hook deny 行为、插件加载/错误封装 | `pytest tests/test_mcp.py tests/test_plugins.py tests/test_hooks.py` |
| 11 | Config / Prompt / CLI 入口 | `src/agent/config/loader.py`, `sections.py`, `__init__.py`, `src/agent/prompt.py`, `main.py`, `profiles.py`, `permissions.py`, `debuglog.py`, `errors.py`, `__init__.py` | 配置默认值、profile 合并、prompt 构造、权限策略、debug trace 事件格式 | `pytest tests/test_config.py tests/test_profiles.py tests/test_main.py tests/test_permissions.py` |
| 12 | 测试注释整理 | `tests/*.py` | 只给复杂回归测试加“保护什么行为”的注释，避免测试文件变长 | 全量 `pytest` |

## 分配建议

| Worker | 负责批次 | 注意事项 |
| --- | --- | --- |
| A | 1, 2 | 先稳住 ToolEnvelope 协议，其他模块的注释要引用这里的术语 |
| B | 3, 4, 11 | 主循环/provider/config 需要术语一致，尤其是 trace、reasoning、context |
| C | 5, 6 | CodeContext 和状态存储容易混淆，注释要强调“代码索引”和“记忆/会话”不是一回事 |
| D | 7, 8, 9, 10 | 外部集成和调度模块要重点解释边界、失败、异步生命周期 |

## 每批提交格式

每批只做注释，不改行为。建议提交信息：

```text
docs: annotate <module name>
```

每批提交前至少运行该批对应测试；第 12 批后运行全量：

```text
uv run pytest
python -m compileall -q src/agent
git diff --check
```

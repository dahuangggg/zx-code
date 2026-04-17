# agent-deep-dive

基于 `Python + asyncio + litellm + pydantic` 的本地 Coding Agent 实验仓库。

当前已经完成 `docs/ZX-code.md` 中第二阶段：单 Agent 核心 + 持久化运行时。

第一阶段跑通了下面这个闭环：

`user input -> model -> tool use -> tool_result -> final answer`

## 当前能力

- 本地单 Agent loop
- `litellm` 模型抽象
- 五个基础工具：
  - `bash`
  - `read_file`
  - `write_file`
  - `edit_file`
  - `grep`
- CLI 一次性执行模式
- CLI REPL 模式
- 最小流式输出
- 基础错误恢复：
  - 模型超时
  - 模型调用异常
  - 最大迭代次数限制
- 基础测试覆盖：
  - loop
  - tools
  - provider mock
- JSONL 会话持久化
- 历史重放
- 上下文裁剪与 compact
- 分层 system prompt builder
- `.memory/MEMORY.md` 记忆系统
- 持久 TodoManager
- `allow / deny / ask` 权限系统
- `CLI > 项目 > 用户` 三层配置

## 环境要求

- Python `3.11+`
- [uv](https://docs.astral.sh/uv/)

确认版本：

```bash
python3 --version
uv --version
```

## 安装依赖

在仓库根目录执行：

```bash
uv sync --dev
```

这会安装运行依赖和测试依赖，并创建虚拟环境。

## 配置模型环境变量

当前模型层通过 `litellm` 走统一接口，所以你用哪个供应商，就配置对应的 key。

例如：

```bash
export OPENAI_API_KEY="your-key"
```

或者：

```bash
export ANTHROPIC_API_KEY="your-key"
```

常见模型名示例：

```bash
openai/gpt-4o-mini
anthropic/claude-3-5-sonnet-latest
```

## 运行方式

推荐直接用 `uv run`。

一次性执行：

```bash
uv run agent "帮我看看这个仓库"
```

指定模型：

```bash
uv run agent --model openai/gpt-4o-mini "读取 docs/ZX-code.md"
```

关闭流式输出：

```bash
uv run agent --no-stream "总结当前目录"
```

限制最大轮数：

```bash
uv run agent --max-turns 6 "帮我列出当前项目结构"
```

指定会话：

```bash
uv run agent --session-id demo "继续上次任务"
```

打印 system prompt：

```bash
uv run agent --print-system-prompt
```

临时关闭 memory/todo：

```bash
uv run agent --no-memory --no-todos "只做一次临时分析"
```

进入 REPL：

```bash
uv run agent
```

也可以直接跑模块入口：

```bash
uv run python -m agent.main "帮我看看这个仓库"
```

## CLI 参数

```bash
uv run agent --help
```

当前支持：

- `--model`
- `--max-turns`
- `--session-id`
- `--data-dir`
- `--context-max-chars`
- `--no-stream`
- `--no-memory`
- `--no-todos`
- `--print-system-prompt`

## 运行测试

```bash
uv run pytest -q
```

## 项目结构

```text
.
├── README.md
├── pyproject.toml
├── docs/
│   ├── ZX-code.md
│   ├── phase-01-单Agent核心讲解.md
│   └── phase-02-持久化上下文权限记忆讲解.md
├── src/
│   └── agent/
│       ├── config.py
│       ├── context.py
│       ├── main.py
│       ├── loop.py
│       ├── memory.py
│       ├── models.py
│       ├── permissions.py
│       ├── prompt.py
│       ├── recovery.py
│       ├── sessions.py
│       ├── todo.py
│       ├── providers/
│       │   ├── base.py
│       │   └── litellm_client.py
│       └── tools/
│           ├── registry.py
│           ├── bash.py
│           ├── read_file.py
│           ├── write_file.py
│           ├── edit_file.py
│           ├── grep.py
│           ├── memory.py
│           └── todo.py
└── tests/
    ├── test_config.py
    ├── test_context.py
    ├── test_loop.py
    ├── test_memory_todo_prompt.py
    ├── test_permissions.py
    ├── test_provider_mock.py
    ├── test_sessions.py
    └── test_tools.py
```

## 已实现模块说明

`src/agent/loop.py`

- 负责 Agent 主循环
- 驱动模型调用
- 执行工具
- 回写 tool result
- 在无工具调用时返回最终答案

`src/agent/providers/litellm_client.py`

- 负责把内部消息格式转换成 `litellm` 可消费的请求
- 支持非流式和最小流式响应解析

`src/agent/tools/registry.py`

- 负责工具注册
- 负责输出工具 schema
- 负责执行工具并统一处理参数错误和运行错误

`src/agent/main.py`

- 负责 CLI 入口
- 支持单次执行和 REPL
- 负责组装配置、会话、上下文、记忆、todo、权限和 prompt builder

`src/agent/sessions.py`

- 使用 JSONL append-only 持久化消息
- 支持按 `session_id` 重建历史

`src/agent/context.py`

- 在模型调用前裁剪过长 tool result
- 在历史过长时 compact 旧消息

`src/agent/prompt.py`

- 使用 `SystemPromptBuilder` 分层构建 prompt
- 支持 memory、todo、runtime 注入

`src/agent/permissions.py`

- 在工具执行前判断 `allow / deny / ask`
- 危险 bash 命令默认需要确认

`src/agent/memory.py`

- 管理 `.memory/MEMORY.md`
- 使用 frontmatter + markdown 存储长期记忆

`src/agent/todo.py`

- 管理持久 todo
- 支持创建、更新、完成和 prompt 渲染

## 当前限制

当前还没有：

- 多通道网关
- 手机端 Telegram / 飞书接入
- DeliveryQueue
- Heartbeat / Cron
- MCP / 插件
- 并发车道和子代理

这些都在 `docs/ZX-code.md` 后续阶段里。

## 开发路线

实施路线见：

- `docs/ZX-code.md`
- `docs/12-Python实战技术选型.md`
- `docs/phase-01-单Agent核心讲解.md`
- `docs/phase-02-持久化上下文权限记忆讲解.md`

当前代码对应 `docs/ZX-code.md` 的第二阶段。

## 建议的下一步

比较顺的推进顺序是：

1. 定义 `InboundMessage`
2. 实现 `Channel` 抽象和 `CLIChannel`
3. 接入 Telegram 或飞书
4. 实现 `BindingTable`
5. 把手机消息路由到同一条 Agent brain

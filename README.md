# agent-deep-dive

基于 `Python + asyncio + litellm + pydantic` 的本地 Coding Agent 实验仓库。

当前已经完成 `ZX-code.md` 中第一阶段的最小实现，跑通了下面这个闭环：

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
uv run agent --model openai/gpt-4o-mini "读取 ZX-code.md"
```

关闭流式输出：

```bash
uv run agent --no-stream "总结当前目录"
```

限制最大轮数：

```bash
uv run agent --max-turns 6 "帮我列出当前项目结构"
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
- `--no-stream`

## 运行测试

```bash
uv run pytest -q
```

## 项目结构

```text
.
├── README.md
├── pyproject.toml
├── ZX-code.md
├── src/
│   └── agent/
│       ├── main.py
│       ├── loop.py
│       ├── models.py
│       ├── prompt.py
│       ├── recovery.py
│       ├── providers/
│       │   ├── base.py
│       │   └── litellm_client.py
│       └── tools/
│           ├── registry.py
│           ├── bash.py
│           ├── read_file.py
│           ├── write_file.py
│           ├── edit_file.py
│           └── grep.py
└── tests/
    ├── test_loop.py
    ├── test_provider_mock.py
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

## 当前限制

这一版还是第一阶段 MVP，目前还没有：

- 会话持久化
- 上下文压缩
- 权限系统
- 记忆系统
- Todo/Planning
- 多通道网关
- 手机端 Telegram / 飞书接入
- DeliveryQueue
- Heartbeat / Cron
- MCP / 插件
- 并发车道和子代理

这些都在 `ZX-code.md` 后续阶段里。

## 开发路线

实施路线见：

- `ZX-code.md`
- `12-Python实战技术选型.md`

当前代码对应 `ZX-code.md` 的第一阶段。

## 建议的下一步

比较顺的推进顺序是：

1. 做 `SessionStore`，把消息持久化下来
2. 做 `SystemPromptBuilder`
3. 做 `PermissionManager`
4. 做 `MEMORY.md` 和最小记忆注入
5. 再接 OpenClaw 风格的 `Channel + Gateway + BindingTable`

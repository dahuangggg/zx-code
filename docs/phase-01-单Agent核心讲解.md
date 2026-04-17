# 第一阶段讲解：单 Agent 核心

这份文档对应 `ZX-code.md` 的第一阶段，目标是把当前已经写出的代码讲清楚，方便你自己学，也方便你之后写进简历。

---

## 1. 这一阶段到底做了什么

第一阶段不是做“完整产品”，而是做一个最小但闭环的本地 Coding Agent。

闭环是：

`user input -> model -> tool use -> tool_result -> final answer`

也就是说，这一阶段至少要解决四件事：

1. 怎么把用户输入交给模型
2. 怎么把工具暴露给模型
3. 怎么执行工具并把结果回写给模型
4. 怎么在本地命令行跑起来

这正是现在代码里已经完成的部分。

---

## 2. 为什么第一阶段要这样拆

如果第一阶段把所有逻辑都塞进一个 `main.py`，短期当然更快，但后面会立刻遇到几个问题：

1. 第二阶段要加会话持久化和权限系统时，没有清晰插入点
2. 第三阶段要接 OpenClaw 风格的 `Channel / Gateway` 时，没有稳定的核心 loop 可复用
3. provider、tool、CLI、状态对象会互相耦合，测试非常难写

所以第一阶段虽然是 MVP，也必须先把 4 个边界拆出来：

1. `models`：数据契约
2. `providers`：模型调用适配层
3. `tools`：工具系统
4. `loop`：调度主循环

CLI 只是最外层入口，不应该承载业务核心。

---

## 3. 当前目录是怎么组织的

当前和第一阶段直接相关的目录如下：

```text
.
├── pyproject.toml
├── README.md
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── loop.py
│       ├── main.py
│       ├── models.py
│       ├── prompt.py
│       ├── recovery.py
│       ├── providers/
│       │   ├── __init__.py
│       │   ├── base.py
│       │   └── litellm_client.py
│       └── tools/
│           ├── __init__.py
│           ├── base.py
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

这个结构的核心思路是：

- `src/agent/` 放运行时代码
- `providers/` 放模型供应商适配
- `tools/` 放工具定义与注册
- `tests/` 对应验证核心闭环

这是一种很适合简历项目的组织方式，因为模块边界清楚，后面扩展阶段也能自然加进去。

---

## 4. 一次请求是怎么跑完整条链路的

你可以把当前代码理解成下面这条调用链：

```text
命令行
  -> main.py
    -> run_task()
      -> model_client.run_turn()
      -> 返回文本 / tool_calls
      -> tool_registry.execute()
      -> 生成 tool_result message
      -> 再次调用 model_client.run_turn()
      -> 最终答案
```

如果展开一点：

1. 用户在命令行输入任务
2. `main.py` 创建 `LiteLLMModelClient` 和 `ToolRegistry`
3. `main.py` 调用 `loop.py` 里的 `run_task`
4. `run_task` 把用户消息写进状态对象
5. `run_task` 调 `provider` 让模型执行一轮
6. 如果模型没有工具调用，直接结束
7. 如果模型返回工具调用，就交给 `ToolRegistry`
8. 工具执行结果转成 `tool` 消息
9. 再喂给模型继续下一轮
10. 直到模型不再请求工具

这个流程是整个 Agent 的骨架，后面第二、三、四阶段基本都是往这条骨架上挂能力。

---

## 5. 顶层配置文件：`pyproject.toml`

### 这个文件负责什么

它负责三件事：

1. 定义项目元信息
2. 定义依赖
3. 定义打包和测试行为

### 这里为什么重要

很多简历项目只停留在“有代码”，没有形成可安装、可测试、可运行的工程。

这个文件说明这个项目已经具备了基础工程化能力。

### 关键内容

- 依赖：
  - `pydantic`
  - `rich`
  - `typer`
  - `litellm`
- 测试依赖：
  - `pytest`
  - `pytest-asyncio`
- CLI 入口：
  - `agent = "agent.main:main"`
- Hatch 打包路径：
  - `packages = ["src/agent"]`

### 为什么需要 `packages = ["src/agent"]`

因为项目名叫 `agent-deep-dive`，但源码目录叫 `agent`。  
如果不显式声明，`hatchling` 会猜错包目录，`uv sync` 时就会失败。

这类细节很适合面试时讲，能体现你不是只会写功能，也会处理 Python 工程打包问题。

---

## 6. `src/agent/models.py`：数据契约层

这是第一阶段非常重要的文件。它解决的问题是：

不要让 loop、provider、tools 之间直接传裸 `dict`。

### 这里定义了什么

主要有 6 类对象：

1. `ToolCall`
2. `Message`
3. `ToolResult`
4. `ModelTurn`
5. `AgentConfig`
6. `AgentState`
7. `AgentRunResult`

### 每个对象分别干什么

`ToolCall`

- 表示模型发起的一次工具调用
- 包含 `id / name / arguments`

`Message`

- 表示一条对话消息
- 统一承载 `system / user / assistant / tool`
- 还提供了 `system()`、`user()`、`assistant()`、`tool()` 这些构造方法

这样做的好处是：调用方不需要到处手写消息结构，减少拼错字段名的概率。

`ToolResult`

- 表示工具执行后的返回结果
- 包含 `is_error`
- 可以通过 `to_message()` 转成模型可继续消费的 `tool message`

`ModelTurn`

- 表示模型完成一轮后的标准输出
- 包含：
  - 文本
  - 工具调用列表
  - 停止原因

这相当于 provider 层和 loop 层之间的契约。

`AgentConfig`

- 管运行参数：
  - `model`
  - `system_prompt`
  - `max_iterations`
  - `model_timeout_s`
  - `stream`

`AgentState`

- 管运行时状态：
  - 当前 prompt
  - turn 数
  - 消息列表
  - tool results

`AgentRunResult`

- 表示整个任务跑完后的最终结果

### 为什么这层要单独存在

因为后面所有模块都会依赖这些对象。

如果这里不统一，后面很快会出现：

- provider 输出一种格式
- tools 返回另一种格式
- loop 再手动拼第三种格式

那样到了第二阶段会很难维护。

---

## 7. `src/agent/providers/`：模型适配层

这一层的职责是：

把“当前代码里的统一消息格式”转换成“具体模型 SDK 能吃的格式”。

这样 loop 层就不直接依赖某一个模型供应商。

### 7.1 `providers/base.py`

这个文件只做一件事：

定义 `ModelClient` 协议。

它规定了一个 provider 至少要实现：

- `run_turn(...) -> ModelTurn`

这样做的价值是：

1. loop 只依赖抽象，不依赖具体实现
2. 测试时可以很容易塞一个 fake client
3. 以后可以加官方 OpenAI/Anthropic provider，而不用改 loop

### 7.2 `providers/litellm_client.py`

这是第一阶段真正工作的 provider。

它负责：

1. 把内部 `Message` 转换成 `litellm` 请求格式
2. 支持非流式响应
3. 支持最小流式响应
4. 解析 tool call
5. 把 SDK 响应归一化成 `ModelTurn`

### 这份代码是怎么组织的

它不是把所有解析逻辑塞进 `run_turn()`，而是拆了几个小函数：

- `_read_attr()`：兼容对象和字典两种访问方式
- `_extract_text()`：从不同 content 结构里提取文本
- `_parse_arguments()`：把 JSON 字符串转回参数字典
- `_normalize_tool_calls()`：把 SDK 的 tool call 结构转成内部 `ToolCall`
- `_maybe_await()`：兼容同步或异步的 stream handler

拆开之后的好处：

1. `run_turn()` 保持主流程清晰
2. 不同供应商格式差异被收敛在适配层
3. 单测更容易覆盖这些行为

### `run_turn()` 的主逻辑

主逻辑其实很简单：

1. 动态导入 `litellm`
2. 把内部消息转成请求 payload
3. 如果有 `stream_handler`，走流式分支
4. 否则走普通请求分支
5. 最终统一返回 `ModelTurn`

### 为什么 `litellm` 动态导入

因为这样可以让：

1. 测试里更容易 mock
2. 没装依赖时错误更可读
3. 当前工程在部分环境下仍能跑非 provider 相关逻辑

---

## 8. `src/agent/tools/`：工具系统

工具系统是第一阶段另一个核心模块。

### 8.1 为什么工具系统要单独拆目录

因为工具会持续膨胀。

如果工具定义和 loop 混在一起，第二阶段开始代码会迅速变乱。  
单独拆目录后，后面可以继续加：

- 权限判断
- MCP 工具
- 外部插件工具
- 技能动态注册工具

### 8.2 `tools/base.py`

这个文件定义了所有工具共享的基类 `Tool`。

每个工具都必须声明：

- `name`
- `description`
- `input_model`

并实现：

- `run(arguments)`

这里最关键的设计是：

`input_model` 使用 `pydantic`。

这带来两个直接好处：

1. `model_json_schema()` 可以直接生成给 LLM 的 tool schema
2. 工具参数会自动校验，不需要每个工具手写参数检查

### 8.3 `tools/registry.py`

这个文件解决的是“工具很多时怎么统一管理”。

它的职责是：

1. 注册工具
2. 按名字查找工具
3. 对外输出全部 schema
4. 统一执行工具
5. 统一处理错误

这个文件非常关键，因为它把错误收口了。

例如：

- 工具不存在
- 参数校验失败
- 工具运行时抛异常

都不会把 Agent 主循环直接打崩，而是被转成 `ToolResult(is_error=True)`。

这是一种非常适合 Agent 的设计，因为 Agent 世界里“失败也要继续对话”。

### 8.4 五个具体工具

#### `bash.py`

作用：

- 执行 shell 命令
- 返回：
  - 命令
  - 工作目录
  - 退出码
  - stdout
  - stderr

代码组织方式：

- `BashInput` 负责参数定义
- `BashTool` 负责真正执行
- 通过 `asyncio.create_subprocess_shell()` 运行命令
- 通过超时保护避免挂死

#### `read_file.py`

作用：

- 读取文件内容
- 支持按行范围读取

代码组织方式：

- `ReadFileInput` 定义 `path / start_line / end_line`
- 工具内部读取文本后切分成行
- 最终带行号返回

带行号这件事很重要，因为 Coding Agent 需要定位上下文。

#### `write_file.py`

作用：

- 写入文件
- 支持 append
- 支持自动创建父目录

这类工具通常是后续编辑链路的基础。

#### `edit_file.py`

作用：

- 替换文件中的文本

这里专门做了一个约束：

如果 `old_text` 匹配多个位置，但没有显式指定 `replace_all=true`，就报错。

这是一个很好的工程保护，避免模型误改多个位置。

#### `grep.py`

作用：

- 搜索文件内容

这里优先用 `rg`，没有再退回 `grep`。  
这符合 Coding Agent 的现实场景，因为代码检索里 `rg` 更快也更常用。

---

## 9. `src/agent/recovery.py`：错误恢复层

这层的职责很明确：

把模型调用的失败从“裸异常”变成“有语义的异常”。

它定义了：

- `AgentError`
- `ModelTimeoutError`
- `ModelInvocationError`
- `MaxIterationsExceededError`

以及：

- `run_model_turn_with_recovery()`

### 为什么单独拆这个文件

因为错误恢复是一个独立能力，不应该散在 loop 和 CLI 里。

如果不拆开，后续你加：

- retry
- fallback model
- overflow handling
- provider 分类错误

就会把 loop 搞得很脏。

所以现在虽然只有基础超时和异常包装，但这个文件是为后续阶段预留结构。

---

## 10. `src/agent/prompt.py`：默认系统提示词

这个文件目前很小，只放了 `DEFAULT_SYSTEM_PROMPT`。

虽然简单，但这个拆分是合理的，因为后续第二阶段会很自然地演进成：

- `SystemPromptBuilder`
- 工作区注入
- `IDENTITY.md / SOUL.md / MEMORY.md`

也就是说，这个文件现在不是为了“功能多”，而是为了保持扩展方向正确。

---

## 11. `src/agent/loop.py`：主循环

这是第一阶段最核心的文件。

### 这个文件负责什么

它负责整个 Agent 的任务执行闭环。

### `run_task()` 的职责

`run_task()` 是本阶段真正的核心函数。

它的逻辑顺序是：

1. 接收用户任务
2. 初始化 `AgentState`
3. 把用户消息写入 state
4. 在 `max_iterations` 限制内循环
5. 调用 provider 获取一轮模型输出
6. 把 assistant message 写回消息历史
7. 如果没有工具调用，返回最终结果
8. 如果有工具调用，逐个执行
9. 把工具结果写回 state
10. 进入下一轮

### 为什么 loop 不直接关心 `litellm`

因为 loop 只依赖 `ModelClient` 抽象。

这样可以保证 loop 的职责纯粹：

- 它是编排者
- 它不是 SDK 细节处理者

这也是面试时一个很好的讲点：

“我把模型供应商差异收敛在 provider 层，loop 只消费统一的 `ModelTurn` 契约。”

---

## 12. `src/agent/main.py`：CLI 入口

这个文件负责把工程真正变成“能跑的程序”。

### 这里实现了什么

1. 单次执行模式
2. REPL 模式
3. 流式输出
4. CLI 参数解析
5. 错误打印

### 代码怎么组织

它不是一个大函数，而是拆成几层：

- `_stream_printer()`：处理流式输出
- `_run_once()`：跑一次任务
- `_run_repl()`：交互模式
- `_run_cli()`：在单次执行和 REPL 之间分流
- `_build_argparse()`：降级 CLI
- `_build_typer_app()`：正常 CLI
- `main()`：最终入口

### 为什么同时保留 `typer` 和 `argparse`

因为当前环境里一开始没有装 `typer`。

所以这里用了一个很实用的做法：

- 优先使用 `typer`
- 缺失时自动回退到 `argparse`

这个设计不算最终形态，但对当前阶段很实用，因为它保证了代码在依赖未同步时也能有基础可执行能力。

---

## 13. `src/agent/__init__.py`

这个文件很小，只对外暴露了 `run_task`。

它的意义是：

- 给包提供清晰入口
- 减少外部代码直接从深层文件 import

后面如果需要给 SDK 化使用，这种导出方式会更干净。

---

## 14. 测试是怎么组织的

当前测试没有追求大而全，而是围绕第一阶段验收目标来写。

这是一种更合理的方式。

### `tests/test_tools.py`

验证：

- `write_file`
- `read_file`
- `edit_file`
- `grep`
- `bash`
- 参数错误是否可读

这组测试对应的是“基础工具能否工作”。

### `tests/test_loop.py`

验证：

- loop 是否能正确处理“模型先请求工具，再给最终答案”
- `max_iterations` 是否生效

这里用了 `ScriptedModelClient`，相当于一个假模型。  
这样可以把测试焦点放在 loop 行为，而不是外部模型服务。

### `tests/test_provider_mock.py`

验证：

- `LiteLLMModelClient` 能否解析普通响应
- `LiteLLMModelClient` 能否解析最小流式响应

这组测试对应的是 provider 适配层的契约是否成立。

### 测试设计上的亮点

这三类测试合在一起，说明这个项目不是“只写功能，不写验证”。

面试里你可以讲：

“我把测试拆成了 tool / orchestration loop / provider adapter 三层，这样出问题时定位会更直接。”

---

## 15. 第一阶段里最值得你学会讲的 5 个点

### 1. 为什么用 `pydantic`

不是为了“看起来高级”，而是因为：

- tool 参数需要校验
- schema 需要自动生成
- 状态对象需要统一数据契约

### 2. 为什么要有 provider 层

因为模型供应商是可替换的。  
如果 loop 直接写死 SDK，后面换 Anthropic / OpenAI / LiteLLM 会很痛苦。

### 3. 为什么要有 registry

因为工具系统会扩张。  
registry 可以把 schema 暴露、查找、执行、错误处理统一收口。

### 4. 为什么 loop 只做 orchestration

因为 loop 的职责就是编排：

- 调模型
- 跑工具
- 回写结果

而不是处理 provider 细节或 CLI 细节。

### 5. 为什么第一阶段就写测试

因为 Agent 项目最容易出现“看起来能跑，实际上行为不稳定”。

第一阶段就把核心契约测试补上，后面加能力时才不容易把基础闭环搞坏。

---

## 16. 你可以怎么把这一阶段写进简历

下面是比“做了一个 AI Agent”更像样的写法。

### 简历版本 1：偏工程实现

- 基于 `Python + asyncio + litellm + pydantic` 实现本地 Coding Agent MVP，完成 `LLM -> Tool Use -> Tool Result -> Final Answer` 的单 Agent 闭环
- 设计统一 `ModelClient` 与 `ToolRegistry` 抽象，解耦模型供应商适配、工具执行和主循环编排逻辑
- 实现 `bash / read_file / write_file / edit_file / grep` 五类核心工具，并通过 `pydantic` 自动生成 schema 与参数校验
- 编写 loop、tool、provider 三层测试，验证工具调用链路、流式响应解析和最大迭代保护

### 简历版本 2：偏系统设计

- 从零搭建可扩展的 Coding Agent 第一阶段架构，拆分 `models / providers / tools / loop / CLI` 五层边界，为后续会话持久化、权限系统和多通道网关预留扩展点
- 基于 `litellm` 封装统一模型适配层，支持普通响应与最小流式响应解析，降低上层对具体 LLM SDK 的耦合
- 将工具执行失败、参数校验失败和模型超时统一收口为结构化错误结果，提升 Agent 运行稳定性

### 面试时别只背结果，要会展开

如果面试官追问，你要能继续讲：

1. 为什么不把代码写在一个文件里
2. 为什么 `ModelTurn` 和 `ToolResult` 是独立对象
3. 为什么工具参数校验放在工具基类和 registry 之间
4. 为什么 provider 层要做归一化
5. 为什么测试分三层

---

## 17. 这一阶段还没做什么

第一阶段故意没做下面这些：

- Session 持久化
- 上下文压缩
- 权限系统
- 记忆注入
- 任务规划
- 网关和通道
- 手机端接入
- DeliveryQueue
- Heartbeat / Cron
- MCP
- 子代理

这不是遗漏，而是阶段控制。

第一阶段先把内核闭环打稳，后面每一层都可以沿着当前边界继续加。

---

## 18. 第二阶段你应该关注什么

第二阶段最值得学的，不是“加更多功能”，而是学会把运行时从单次任务升级成“连续可用的 Agent”。

重点会是：

1. `SessionStore`
2. `SystemPromptBuilder`
3. `PermissionManager`
4. `MEMORY.md`
5. 配置分层

到那时，第一阶段拆出来的 `models / providers / tools / loop` 才会真正体现价值。

---

## 19. 这份文档你该怎么用

最实际的用法是：

1. 对着文档把每个文件重新读一遍
2. 自己试着口头讲一次完整调用链
3. 按“为什么这样拆”而不是“这个函数干了什么”来复述
4. 把第 16 节的简历表达改成你自己的语言

如果你能把这份文档讲顺，第一阶段就不只是“做出来了”，而是真的变成了你的项目经验。

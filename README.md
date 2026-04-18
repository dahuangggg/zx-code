# agent-deep-dive

基于 `Python + asyncio + litellm + pydantic` 的本地 Coding Agent 实验仓库。

当前已经完成 `docs/ZX-code.md` 中第三阶段：单 Agent 核心 + 持久化运行时 + 多通道 Gateway。

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
- `allow / deny / ask` 权限系统，覆盖 bash 危险命令、`write_file` / `edit_file` 敏感路径和工作目录外写入
- 文件工具 symlink 检测，拒绝跟踪符号链接
- 持久化原子写入：session 加文件锁，memory / todo 使用 write-to-temp + rename
- 上下文截断安全边界：不在 assistant+tool_calls 和 tool result 之间切断
- Telegram 缓冲区上限保护，防止内存无限增长
- `CLI > 项目 > 用户` 三层配置
- `InboundMessage` 统一入站消息模型
- `Channel / ChannelManager` 通道抽象
- `CLIChannel` 本地通道
- `TelegramChannel` 标准库 HTTP 适配、offset 持久化、topic 归一化、长消息切块
- `FeishuChannel` webhook 事件解析、tenant token、飞书消息发送
- `BindingTable` 多级路由
- `Gateway` 统一调度入口
- `build_session_key()` 会话隔离
- `--watch` 通道监听模式

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

## Telegram 连接手册

第三阶段已经提供 Telegram 通道适配。当前实现使用 Telegram Bot API 的 `getUpdates` 拉取消息、`sendMessage` 回复消息。

当前支持两种模式：

1. 不加 `--watch`：拉取一条消息，回复一次，然后退出
2. 加 `--watch`：持续长轮询 Telegram，有消息就进入 Gateway

官方文档：

- Telegram Bot API: https://core.telegram.org/bots/api
- BotFather: https://t.me/BotFather

### 1. 创建 Telegram Bot

1. 在 Telegram 里打开 `@BotFather`
2. 发送 `/newbot`
3. 按提示输入 bot 名称和 username
4. 保存 BotFather 返回的 token

不要把 token 提交到 git。

### 2. 配置环境变量

模型 key：

```bash
export OPENAI_API_KEY="your-openai-key"
```

Telegram bot token：

```bash
export TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
```

### 3. 先给 bot 发一条消息

在 Telegram 手机端或桌面端打开你的 bot，先发送一条消息，例如：

```text
帮我总结这个仓库
```

如果是在群里使用：

1. 把 bot 加进群
2. 发送一条普通文本消息
3. 如果 bot 收不到群消息，去 BotFather 检查 privacy mode，或者在群里 `@你的bot用户名` 再发消息

### 4. 本地拉取一次消息并回复

在仓库根目录执行：

```bash
uv run agent \
  --channel telegram \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --account-id main-telegram-bot \
  --dm-scope per-account-channel-peer
```

这条命令会做这些事：

1. 调 Telegram `getUpdates`
2. 取一条 message 或 edited_message
3. 转成统一的 `InboundMessage`
4. 交给 `Gateway`
5. 生成 session key
6. 调同一条 Agent Brain
7. 用 Telegram `sendMessage` 回复到原 chat

### 5. 持续监听 Telegram

手机上连续使用时，加 `--watch`：

```bash
uv run agent \
  --channel telegram \
  --watch \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --account-id main-telegram-bot \
  --dm-scope per-account-channel-peer
```

这个进程会一直运行。手机给 bot 发消息后，消息会被归一化成 `InboundMessage`，再进入同一条 Agent Brain。

### 6. 避免重复消费旧消息

Telegram offset 会持久化到：

```text
.agent/channels/telegram/offset-<account_id>.txt
```

如果你要临时覆盖 offset，可以传：

```bash
uv run agent \
  --channel telegram \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --telegram-offset 123456789
```

`--telegram-offset` 应该设置为最后一次已处理 `update_id + 1`。第四阶段会把 offset 和投递状态放进可靠队列/状态文件。

如果这个 bot 之前配置过 webhook，`getUpdates` 可能不能正常工作。可以先删除 webhook：

```bash
curl "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=true"
```

### 7. 常用 Telegram 调试命令

只打印 prompt，不调模型、不发消息：

```bash
uv run agent \
  --channel telegram \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --no-memory \
  --no-todos \
  --print-system-prompt
```

强制把入口路由给某个 agent：

```bash
uv run agent \
  --channel telegram \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --force-agent-id coder
```

只允许指定 chat：

```bash
uv run agent \
  --channel telegram \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --telegram-allowed-chats "123456789,-1001234567890"
```

### 8. 当前 Telegram 能力和限制

- 支持私聊、群组和 forum topic
- 支持 `--telegram-allowed-chats` 白名单
- 支持 offset 持久化
- 支持长消息按 Telegram 4096 字符上限切块
- 出站消息还没有 `DeliveryQueue`
- 失败后不会自动重试

这些会在第四阶段处理。

## 飞书连接说明

第三阶段已经参考 `../claw0` 补上飞书真实通道骨架：webhook 接收事件、解析消息、获取 `tenant_access_token`、调用飞书发送消息 API。

当前文件是 [feishu.py](/Users/dahuangggg/Github/agent-deep-dive/src/agent/channels/feishu.py)，现在支持：

- `challenge` 校验响应
- `verification_token` 校验
- text / post / image / file 消息解析
- 群聊里按 `bot_open_id` 判断是否被 @
- p2p / group 映射到统一 `peer_id / guild_id`
- `tenant_access_token` 缓存
- `im/v1/messages` 文本回复
- 本地 webhook server，把飞书事件推进 Channel 队列

### 1. 创建飞书开放平台应用

飞书有两类常见机器人：

1. 自定义群机器人 webhook
2. 飞书开放平台应用 bot

这个项目要做的是“手机上直接和 Agent 对话”，所以应该走第二种：飞书开放平台应用 bot。

原因是：

- 自定义群机器人更适合单向推送
- Agent 需要接收入站消息、识别用户和群、再回复
- Gateway 需要 `sender_id / peer_id / guild_id / account_id`

官方文档入口：

- 飞书开放平台：https://open.feishu.cn/
- 发送消息 API：https://open.feishu.cn/document/server-docs/im-v1/message/create
- 事件订阅配置：https://open.feishu.cn/document/server-docs/event-subscription-guide/event-subscription-configure-/request-url-configuration-case

### 2. 配置环境变量

至少需要：

```bash
export FEISHU_APP_ID="cli_xxx"
export FEISHU_APP_SECRET="xxx"
export FEISHU_VERIFICATION_TOKEN="xxx"
export FEISHU_BOT_OPEN_ID="ou_xxx"
export FEISHU_BOT_ACCOUNT_ID="main-feishu-bot"
```

当前还没有实现飞书加密事件解密，所以事件订阅里的 Encrypt Key 先不要启用。收到 `encrypt` payload 时，本地 webhook 会返回不支持。

### 3. 暴露本地 webhook

飞书需要从公网访问你的本地服务。开发时可以用 ngrok、cloudflared 或其他内网穿透工具，把本地端口暴露出去。

本地监听端口示例是 `8787`，公网 URL 例如：

```text
https://your-tunnel.example/feishu
```

本项目的 webhook server 不限制 path，`/feishu`、`/webhook` 都可以。

### 4. 启动飞书监听

在仓库根目录执行：

```bash
uv run agent \
  --channel feishu \
  --watch \
  --account-id "$FEISHU_BOT_ACCOUNT_ID" \
  --dm-scope per-account-channel-peer \
  --feishu-app-id "$FEISHU_APP_ID" \
  --feishu-app-secret "$FEISHU_APP_SECRET" \
  --feishu-verification-token "$FEISHU_VERIFICATION_TOKEN" \
  --feishu-bot-open-id "$FEISHU_BOT_OPEN_ID" \
  --feishu-webhook-host 0.0.0.0 \
  --feishu-webhook-port 8787
```

然后在飞书开放平台事件订阅里，把 Request URL 配成你的公网 tunnel URL。

飞书事件进入后会走：

```text
Feishu webhook -> FeishuChannel.push_event()
  -> InboundMessage
  -> Gateway.handle_inbound()
  -> Agent Brain
  -> FeishuChannel.send()
  -> im/v1/messages
```

### 5. 当前飞书限制

- 暂不支持加密事件解密
- 暂不支持富文本回复
- 暂不下载图片或文件内容，只把 media key 放进 `InboundMessage.media`
- 出站消息还没有 `DeliveryQueue`，失败不会持久化重试

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
- `--channel`
- `--account-id`
- `--agent-id`
- `--default-agent-id`
- `--force-agent-id`
- `--dm-scope`
- `--telegram-token`
- `--telegram-offset`
- `--telegram-timeout`
- `--telegram-allowed-chats`
- `--telegram-text-coalesce`
- `--telegram-media-group-coalesce`
- `--feishu-app-id`
- `--feishu-app-secret`
- `--feishu-verification-token`
- `--feishu-encrypt-key`
- `--feishu-bot-open-id`
- `--feishu-is-lark`
- `--feishu-webhook-host`
- `--feishu-webhook-port`
- `--feishu-receive-timeout`
- `--watch`
- `--no-stream`
- `--no-memory`
- `--no-todos`
- `--print-system-prompt`

## 运行测试

```bash
uv run pytest -q
```

## 阅读顺序

第一次看这个项目，建议按这个顺序读：

1. `README.md`：了解当前能力、运行方式和项目结构
2. `DEVLOG.md`：了解每次阶段性更新改了什么、为什么改、怎么验证
3. `docs/ZX-code.md`：了解完整路线图
4. `docs/phase-01-单Agent核心讲解.md`：理解第一阶段单 Agent 核心
5. `docs/phase-02-持久化上下文权限记忆讲解.md`：理解第二阶段运行时状态
6. `docs/phase-03-通道网关与手机接入讲解.md`：理解第三阶段多通道 Gateway
7. `src/agent/main.py`：看 CLI 如何组装运行时和 Gateway
8. `src/agent/gateway.py`：看路由、会话隔离和统一派发
9. `src/agent/loop.py`：看 Agent 主循环
10. `tests/`：看每个能力如何被验证

## 文档维护规则

这个项目以后每次更新代码，都必须同步更新文档：

1. 更新 `README.md`，让新读者能看到最新能力、运行方式和项目结构
2. 更新 `DEVLOG.md`，记录本次改动、设计原因、验证方式和阅读入口
3. 如果完成了一个阶段，新增或更新 `docs/phase-xx-...讲解.md`
4. 如果 CLI 参数、目录结构、配置文件或测试方式变化，必须同步写进 README

## 项目结构

```text
.
├── README.md
├── DEVLOG.md
├── pyproject.toml
├── docs/
│   ├── README.md
│   ├── ZX-code.md
│   ├── phase-01-单Agent核心讲解.md
│   ├── phase-02-持久化上下文权限记忆讲解.md
│   └── phase-03-通道网关与手机接入讲解.md
├── src/
│   └── agent/
│       ├── channels/
│       │   ├── base.py
│       │   ├── cli.py
│       │   ├── telegram.py
│       │   └── feishu.py
│       ├── config.py
│       ├── context.py
│       ├── gateway.py
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
    ├── test_channels.py
    ├── test_config.py
    ├── test_context.py
    ├── test_gateway.py
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
- 负责把 CLI / Telegram / 飞书入口接入 Gateway
- 支持 `--watch` 持续监听通道

`src/agent/sessions.py`

- 使用 JSONL append-only 持久化消息
- 追加写使用 `fcntl.flock` 文件锁，防止并发损坏
- 支持按 `session_id` 重建历史

`src/agent/context.py`

- 在模型调用前裁剪过长 tool result
- 在历史过长时 compact 旧消息
- 截断时保证不在 assistant+tool_calls 和对应 tool result 之间切断

`src/agent/prompt.py`

- 使用 `SystemPromptBuilder` 分层构建 prompt
- 支持 memory、todo、runtime 注入

`src/agent/permissions.py`

- 在工具执行前判断 `allow / deny / ask`
- 危险 bash 命令默认需要确认
- `write_file` / `edit_file` 对敏感路径和工作目录外写入默认需要确认
- 检测符号链接路径

`src/agent/memory.py`

- 管理 `.memory/MEMORY.md`
- 使用 frontmatter + markdown 存储长期记忆
- 写入使用原子替换（write-to-temp + rename），防止中断丢数据

`src/agent/todo.py`

- 管理持久 todo
- 支持创建、更新、完成和 prompt 渲染
- 写入使用原子替换，防止中断丢数据

`src/agent/channels/base.py`

- 定义 `InboundMessage`
- 定义 `Channel`
- 定义 `ChannelManager`

`src/agent/channels/cli.py`

- 把本地 CLI 输入归一化为 `InboundMessage`
- 保存 CLI 出站消息，便于测试 Gateway

`src/agent/channels/telegram.py`

- 把 Telegram update 归一化为 `InboundMessage`
- 使用标准库 HTTP 调用 `getUpdates` 和 `sendMessage`
- 持久化 Telegram offset
- 支持 topic、allowed chats、文本合并、媒体组缓冲和长消息切块
- 缓冲区有 `max_buffer_size` 上限保护，防止内存无限增长

`src/agent/channels/feishu.py`

- 解析飞书 webhook 事件
- 响应飞书 challenge
- 获取并缓存 `tenant_access_token`
- 调飞书 `im/v1/messages` 发送文本消息
- 启动本地 webhook server，把事件放进通道 inbox

`src/agent/gateway.py`

- 实现 `build_session_key()`
- 实现 `BindingTable`
- 实现 `Gateway.handle_inbound()`
- 把不同通道统一路由到同一条 Agent Brain

## 当前限制

当前还没有：

- DeliveryQueue
- Heartbeat / Cron
- Telegram 可靠投递和失败重试
- 飞书加密事件解密
- 飞书富文本回复
- MCP / 插件
- 并发车道和子代理

这些都在 `docs/ZX-code.md` 后续阶段里。

## 开发路线

实施路线见：

- `docs/ZX-code.md`
- `docs/12-Python实战技术选型.md`
- `docs/phase-01-单Agent核心讲解.md`
- `docs/phase-02-持久化上下文权限记忆讲解.md`
- `docs/phase-03-通道网关与手机接入讲解.md`

当前代码对应 `docs/ZX-code.md` 的第三阶段。

## 建议的下一步

比较顺的推进顺序是：

1. 实现 `DeliveryQueue`
2. 实现 Telegram 出站消息可靠投递
3. 实现消息 chunk、失败目录和重试退避
4. 实现 `HeartbeatRunner`
5. 实现 `CronScheduler`

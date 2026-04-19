# agent-deep-dive

基于 `Python + asyncio + litellm + pydantic` 的本地 Coding Agent 实验仓库。

当前已经完成 `docs/ZX-code.md` 中第四阶段，并推进到第五阶段 5B：单 Agent 核心 + 持久化运行时 + 多通道 Gateway + 可靠投递 + Heartbeat/Cron + Priority Lane Runtime + Delivery Daemon。

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
- `DeliveryQueue` 写前日志式可靠投递
- `DeliveryRunner` 出站消息发送、失败重试和失败目录
- `DeliveryDaemon` 在 watch 模式中后台持续 drain 投递队列
- `chunk_message()` 按平台上限切分长消息
- `HeartbeatRunner` 主动心跳，用户活跃时不抢占对话
- `CronScheduler` 支持 `at / every / cron` 三类定时任务
- `LaneScheduler` 协作式优先级调度，`main > subagent > cron > heartbeat`
- Cron job 的 `last_fired_at / next_run_at` 会持久化到 `.agent/cron-state.json`

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

`--telegram-offset` 应该设置为最后一次已处理 `update_id + 1`。

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
- 出站消息会先进入 `DeliveryQueue` 再发送
- 发送失败会按指数退避重试，超过次数后进入失败目录

剩余的投递后台 daemon 和更细的限流恢复会在后续阶段处理。

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
- 出站消息会先进入 `DeliveryQueue`，失败会持久化重试

## 可靠投递、Heartbeat 和 Cron

第四阶段把“直接发送回复”改成了“先落盘，再发送”。

出站消息会写到：

```text
.agent/delivery/queued/
```

发送成功后移动到：

```text
.agent/delivery/sent/
```

连续失败后移动到：

```text
.agent/delivery/failed/
```

这让 Telegram / 飞书回复、Heartbeat 输出、Cron 输出都走同一个可靠投递路径。

### Delivery 常用参数

```bash
uv run agent \
  --channel telegram \
  --watch \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --delivery-max-attempts 5 \
  --delivery-base-delay 1 \
  --delivery-max-delay 300 \
  --delivery-jitter 1 \
  --delivery-daemon-interval 1
```

含义：

- `--delivery-max-attempts`：最多尝试次数
- `--delivery-base-delay`：第一次失败后的基础退避秒数
- `--delivery-max-delay`：最大退避秒数
- `--delivery-jitter`：随机抖动秒数，避免固定节奏重试
- `--delivery-daemon-interval`：watch 模式中后台投递 daemon 的轮询间隔

### Delivery Daemon

第五阶段 5B 把投递重试从“每轮顺手 drain”升级为后台 daemon。

在 `--watch` 模式下，系统会启动 `DeliveryDaemon`：

```text
DeliveryDaemon -> DeliveryRunner.deliver_ready_once() -> Channel.send()
```

规则：

- 用户回复仍然会立即尝试发送一次
- 发送失败后保留在 `queued/`，由后台 daemon 按退避时间继续重试
- Heartbeat 和 Cron 产生的主动输出进入 `DeliveryQueue` 后，也由后台 daemon 投递
- `DeliveryRunner` 内部有 async lock，避免同步发送和后台 daemon 同时发送同一条消息

### Heartbeat

Heartbeat 用于让 Agent 在用户空闲时主动检查是否有需要推送的内容。

```bash
uv run agent \
  --channel telegram \
  --watch \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --heartbeat \
  --heartbeat-to "123456789" \
  --heartbeat-interval 300 \
  --heartbeat-min-idle 60 \
  --heartbeat-prompt "检查项目状态；没有需要告诉用户的内容就只回复 HEARTBEAT_OK"
```

规则：

- 用户当前对话正在跑 Agent 时，Heartbeat 不执行
- 用户刚发过消息且未超过 `--heartbeat-min-idle`，Heartbeat 不执行
- Agent 回复 `HEARTBEAT_OK` 时不会推送
- 其他输出会进入 `DeliveryQueue`

### Cron

Cron 配置文件默认读取 `.zx-code/cron.json`，也可以用 `--cron-jobs` 指定。

示例：

```json
{
  "jobs": [
    {
      "id": "daily-summary",
      "kind": "cron",
      "schedule": "0 9 * * *",
      "prompt": "总结昨天到现在的项目变化，只有有价值的信息才推送。",
      "channel": "telegram",
      "to": "123456789",
      "account_id": "main-telegram-bot"
    },
    {
      "id": "five-min-check",
      "kind": "every",
      "schedule": "300",
      "prompt": "检查是否有需要提醒用户的内容。",
      "channel": "telegram",
      "to": "123456789"
    },
    {
      "id": "one-shot",
      "kind": "at",
      "schedule": "1770000000",
      "prompt": "到点提醒用户检查阶段四验收。",
      "channel": "telegram",
      "to": "123456789"
    }
  ]
}
```

启动时加：

```bash
uv run agent \
  --channel telegram \
  --watch \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --cron-jobs .zx-code/cron.json
```

`cron` 表达式优先使用 `croniter`；如果当前环境没有安装 `croniter`，会退回到内置的五字段简单解析，支持 `*`、`*/N`、单值和逗号列表。

Cron job 的运行状态会写入：

```text
.agent/cron-state.json
```

这份状态记录 `last_fired_at / next_run_at`。进程重启后，`every` 和 `cron` 类型任务会继续沿用上一次计算好的触发时间，避免 watch 进程重启后立刻重复触发。

## Priority Lane Runtime

第五阶段 5A 引入了协作式 lane 调度。所有 Agent turn 统一经过 `LaneScheduler`：

```text
main > subagent > cron > heartbeat
```

规则：

- `main`：用户主动发来的消息，优先级最高
- `subagent`：后续子代理任务会放到这一层
- `cron`：定时任务，只在更高优先级任务空闲后运行
- `heartbeat`：最低优先级，不能抢用户消息

这里选择的是协作式调度，不做抢占。原因是一次 LLM 调用无法在中途安全暂停；新的高优先级任务会在当前 turn 结束后优先执行。

## CLI 参数

```bash
uv run agent --help
```

当前支持：

- `--model`
- `--max-turns`
- `--session-id`
- `--data-dir`
- `--context-max-tokens`
- `--compact-model`
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
- `--delivery-max-attempts`
- `--delivery-base-delay`
- `--delivery-max-delay`
- `--delivery-jitter`
- `--delivery-daemon-interval`
- `--heartbeat`
- `--heartbeat-interval`
- `--heartbeat-min-idle`
- `--heartbeat-channel`
- `--heartbeat-to`
- `--heartbeat-prompt`
- `--heartbeat-sentinel`
- `--cron-jobs`
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
7. `docs/phase-04-可靠投递与主动调度讲解.md`：理解第四阶段可靠投递、Heartbeat 和 Cron
8. `docs/phase-05A-Priority-Lane与Cron状态持久化讲解.md`：理解第五阶段 5A 的协作式调度
9. `docs/phase-05B-Delivery-Daemon讲解.md`：理解第五阶段 5B 的后台投递 daemon
10. `src/agent/main.py`：看 CLI 如何组装运行时和 Gateway
11. `src/agent/gateway.py`：看路由、会话隔离和统一派发
12. `src/agent/delivery.py`：看可靠投递、锁和后台 daemon
13. `tests/`：看每个能力如何被验证

## 文档维护规则

这个项目以后每次更新代码，都必须同步更新文档：

1. 更新 `README.md`，让新读者能看到最新能力、运行方式和项目结构
2. 更新 `DEVLOG.md`，记录本次改动、设计原因、验证方式和阅读入口
3. 如果完成了一个阶段，新增或更新 `docs/phase-xx-...讲解.md`
4. 如果 CLI 参数、目录结构、配置文件或测试方式变化，必须同步写进 README

注意：`docs/` 是本地学习讲解材料，当前由 `.gitignore` 忽略，不加入 git。

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
│   ├── phase-03-通道网关与手机接入讲解.md
│   ├── phase-04-可靠投递与主动调度讲解.md
│   ├── phase-05A-Priority-Lane与Cron状态持久化讲解.md
│   └── phase-05B-Delivery-Daemon讲解.md
├── src/
│   └── agent/
│       ├── channels/
│       │   ├── base.py
│       │   ├── cli.py
│       │   ├── telegram.py
│       │   └── feishu.py
│       ├── config.py
│       ├── context.py
│       ├── cron.py
│       ├── delivery.py
│       ├── gateway.py
│       ├── heartbeat.py
│       ├── lanes.py
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
    ├── test_delivery.py
    ├── test_gateway.py
    ├── test_heartbeat_cron.py
    ├── test_lanes.py
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
- 在 watch loop 中驱动 Delivery、Heartbeat 和 Cron tick

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
- 回复先进入 `DeliveryQueue`，再由 `DeliveryRunner` 投递

`src/agent/delivery.py`

- 定义 `DeliveryEntry`
- 实现 `DeliveryQueue`
- 使用 `tmp + fsync + os.replace` 原子写入
- 实现 `DeliveryRunner`
- `DeliveryRunner` 使用 async lock 防止并发重复投递
- 实现 `DeliveryDaemon`，watch 模式后台持续投递 ready entries
- 实现指数退避、失败目录和 `chunk_message()`

`src/agent/heartbeat.py`

- 定义 `ActivityTracker`
- 定义 `HeartbeatConfig`
- 实现 `HeartbeatRunner`
- 用户活跃或 Agent 正在处理时跳过心跳

`src/agent/cron.py`

- 定义 `CronJob`
- 实现 `CronScheduler`
- 支持 `at / every / cron`
- 支持从 `.zx-code/cron.json` 加载任务
- 持久化 `last_fired_at / next_run_at` 到 `.agent/cron-state.json`

`src/agent/lanes.py`

- 定义 `LaneScheduler`
- 使用 `asyncio.PriorityQueue` 做协作式优先级调度
- 默认优先级为 `main > subagent > cron > heartbeat`
- 记录每个 lane job 的等待时间、执行时间和结果状态

## 当前限制

当前还没有：

- 飞书加密事件解密
- 飞书富文本回复
- MCP / 插件
- Subagent / Worktree Isolation
- 多 profile / 多 key / fallback model
- 完整 ResilienceRunner
- Lane 目前是 turn 级协作式调度，不做 LLM mid-turn 抢占

这些都在 `docs/ZX-code.md` 后续阶段里。

## 开发路线

实施路线见：

- `docs/ZX-code.md`
- `docs/12-Python实战技术选型.md`
- `docs/phase-01-单Agent核心讲解.md`
- `docs/phase-02-持久化上下文权限记忆讲解.md`
- `docs/phase-03-通道网关与手机接入讲解.md`
- `docs/phase-04-可靠投递与主动调度讲解.md`
- `docs/phase-05A-Priority-Lane与Cron状态持久化讲解.md`
- `docs/phase-05B-Delivery-Daemon讲解.md`

当前代码对应 `docs/ZX-code.md` 的第五阶段 5B。

## 建议的下一步

比较顺的推进顺序是：

1. 实现 Subagent，并把 subagent turn 接入 `subagent` lane
2. 补多 profile / 多 key / fallback model
3. 补飞书加密事件解密
4. 推进 MCP、worktree 和完整 ResilienceRunner
5. 第六阶段接入 TraceEvent，记录 delivery daemon 与 lane wait time

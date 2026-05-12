# agent-deep-dive

基于 `Python + asyncio + litellm + pydantic` 的本地 Coding Agent 实验仓库。

当前已经完成 `docs/ZX-code.md` 中第五阶段：单 Agent 核心 + 持久化运行时 + 多通道 Gateway + 可靠投递 + Heartbeat/Cron + Priority Lane Runtime + Delivery Daemon + Subagent + Profile Fallback + ResilienceRunner + MCP + Worktree Isolation + Plugin System。并按 `docs/12-Python实战技术选型.md` 补齐了除 `s15-s17 Agent Teams` 外的 Skill Loading、DAG Task System、通用 Background Task Manager 和若干兼容入口；新增了 ChromaDB 驱动的 CodeContext 代码库语义上下文层。

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
- 命名 memory record API，可把结构化记忆保存成 `.memory/<name>.md` 并更新索引
- 持久 TodoManager
- `SkillStore` 两层技能加载：prompt 只注入技能索引，`load_skill` 按需加载完整 markdown
- `TaskStore` 文件持久化 DAG 任务系统，支持 `blocked_by`、依赖解锁和 `task_create / task_complete / task_list`
- `SystemPromptBuilder` 注入当前日期、模型名、平台信息和真实工具索引
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
- `LaneScheduler` 支持当前 worker 内嵌套执行，避免主 Agent 同步等待子 Agent 时死锁
- Cron job 的 `last_fired_at / next_run_at` 会持久化到 `.agent/cron-state.json`
- `SubagentRunner` 支持独立子会话、递归深度限制和 `subagent` lane 调度
- `SubagentRunner.spawn_background()` 支持 `asyncio.create_task + Queue` 风格的后台子代理结果通知
- `subagent_run` 工具允许主 Agent 把聚焦任务交给子 Agent 执行，并返回子会话结果
- `BackgroundTaskManager` 提供通用后台任务运行和结果队列
- `ModelProfile` 支持多模型 / 多 key 配置
- `FallbackModelClient` 在 `rate_limit / auth / billing / timeout` 这类可恢复失败时自动切换备用 profile
- `classify_error()` 支持 `rate_limit / auth / timeout / overflow / billing / unknown` 失败分类
- `ResilienceRunner` 统一封装单次模型 turn 的 timeout、rate limit backoff、截断续写和 overflow compact
- `StdioMCPClient` 基于官方 `mcp` Python SDK 接入 stdio transport
- `MCPToolRouter` 会把 MCP server 工具发现并注册成 `mcp__server__tool`
- MCP 工具复用 `ToolRegistry` 和现有 `PermissionManager`
- `WorktreeManager` 支持按任务创建独立 git worktree 和 branch
- `worktree_create / worktree_cleanup` 工具允许 Agent 显式管理隔离工作区
- `PluginManager` 支持从 `plugin.json` 发现命令型插件工具
- 插件工具注册为 `plugin__plugin__tool`，同样经过统一权限系统
- `CodeContextIndexer` 支持同步索引任意代码库路径，默认当前工作目录
- `code_index / code_search / code_index_status / code_index_clear` 工具提供代码库语义索引、搜索、状态查询和本地索引清理
- `code_index` 支持 `background=true` 后台索引，`code_index_status` 返回 `indexing / indexed / indexfailed / not_found` 和进度百分比
- CodeContext 使用 ChromaDB `PersistentClient` 持久化到 `.agent/code-context/chroma`，单 collection 通过 `codebase_id/codebase_path` metadata 隔离多个仓库
- CodeContext 默认忽略 `.env`、`.git`、`node_modules`、`.agent` 等敏感或低价值路径，并读取 `.gitignore` / `.contextignore`
- Python 文件使用 AST-aware chunking，其他常见代码/文档文件使用 line-based chunking，并保留路径和行号
- CodeContext 使用文件级 `sha256` snapshot 支持 added / modified / removed 增量索引
- `code_search` 使用 Chroma vector search + 本地 BM25-like 关键词通道 + RRF 融合，并按文件/行区间去重
- System prompt 会提示模型在陌生代码库、架构边界和自然语言代码定位场景优先使用 `code_search`，但不会自动注入检索结果

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

恢复指定会话：

```bash
uv run agent --resume demo "继续上次任务"
```

默认情况下，每次打开 CLI 都会创建一个新的 session。`--session-id demo` 仍作为兼容别名保留，等价于 `--resume demo`。

打印 system prompt：

```bash
uv run agent --print-system-prompt
```

开启完整调试日志：

```bash
uv run agent --debug-log --debug-log-path .agent/debug.jsonl "帮我看看这个仓库"
```

临时关闭 memory/todo：

```bash
uv run agent --no-memory --no-todos "只做一次临时分析"
```

进入 REPL：

```bash
uv run agent
```

REPL 启动时会显示当前 model、session、mode 和 cwd。输入提示中的方括号显示当前文件夹名，例如 `zx-code [agent-deep-dive] >`。如果通过 `--resume <session-id>` 进入，会自动展示该 session 最近几条 user/assistant 对话。退出时会打印恢复当前 session 的命令，例如 `uv run agent --resume <session-id>`。

模型响应前会显示 `thinking` 动画；当 Agent 调用工具时，会输出工具名和关键参数摘要，工具结束后显示 `done` 或 `failed`，方便观察长任务进度。

可用命令：

- `/help`：查看 REPL 命令
- `/session`：查看当前 session id
- `/clear`：清屏并重绘状态面板
- `exit` / `quit`：退出

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

更细的限流恢复和平台错误分类会在后续阶段处理。

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
- `subagent`：子代理任务，优先级低于用户主动消息，高于主动任务
- `cron`：定时任务，只在更高优先级任务空闲后运行
- `heartbeat`：最低优先级，不能抢用户消息

这里选择的是协作式调度，不做抢占。原因是一次 LLM 调用无法在中途安全暂停；新的高优先级任务会在当前 turn 结束后优先执行。

`LaneScheduler` 还支持当前 worker 内的嵌套执行：主 Agent 在 `main` lane 中调用 `subagent_run` 时，子 Agent 会直接在当前执行链里跑完并记录为 `subagent` lane，避免“主任务等待子任务、子任务又排在同一个 worker 后面”的死锁。

## Subagent

第五阶段 5C 引入了 `SubagentRunner` 和 `subagent_run` 工具。

主 Agent 可以把一个聚焦任务交给子 Agent，例如“只阅读 gateway 相关文件并总结路由逻辑”。子 Agent 会使用独立 session id：

```text
<parent-session>:subagent:<label>:<random-id>
```

这样子 Agent 的消息历史不会直接写进主会话。主会话只会收到 `subagent_run` 的工具结果，里面包含子会话 id、任务、深度和最终文本。

默认递归深度是 `1`，也就是主 Agent 可以创建一层子 Agent，子 Agent 自己不会再拿到 `subagent_run` 工具，避免无限递归。

常用参数：

```bash
uv run agent --subagent-max-depth 1 "分析这个仓库的 Gateway 代码"
```

临时关闭子 Agent：

```bash
uv run agent --no-subagents "只用主 Agent 回答"
```

当前 Subagent 仍共享同一个项目工作目录和基础工具集合；它解决的是“独立上下文”和“运行时调度”问题，还不是 Git worktree 级别的文件隔离。

## Profile Fallback

第五阶段 5D 引入了模型 profile 和 fallback client。

最简单的临时用法是在 CLI 里给备用模型：

```bash
uv run agent \
  --model openai/gpt-4o-mini \
  --fallback-models "anthropic/claude-3-5-sonnet-latest,openai/gpt-4o" \
  "总结当前项目"
```

当主模型抛出 `rate_limit / auth / billing / timeout` 这类可恢复错误时，`FallbackModelClient` 会把当前 profile 放入短期 cooldown，然后尝试下一个 profile。未知错误不会盲目切换，避免把代码 bug、解析 bug 或参数错误伪装成模型供应商故障。

更适合长期运行的方式是在 `.zx-code/config.toml` 中配置多个 profile：

```toml
[agent]
model = "openai/gpt-4o-mini"

[[agent.model_profiles]]
name = "openai-primary"
model = "openai/gpt-4o-mini"
api_key_env = "OPENAI_API_KEY"

[[agent.model_profiles]]
name = "openai-backup"
model = "openai/gpt-4o-mini"
api_key_env = "OPENAI_BACKUP_API_KEY"

[[agent.model_profiles]]
name = "anthropic-backup"
model = "anthropic/claude-3-5-sonnet-latest"
api_key_env = "ANTHROPIC_API_KEY"
```

`api_key_env` 只保存环境变量名，不保存真实 key。运行时会读取环境变量，把值作为 `litellm` 的 `api_key` 参数传入。这样可以实现同模型多 key 轮换，也可以实现跨模型 fallback。

## Debug Log

开启后，Agent 会把完整运行轨迹写入 JSONL 文件，便于定位 prompt、模型 raw 返回、工具调用和权限判断问题。

```bash
uv run agent --debug-log --debug-log-path .agent/debug.jsonl "调试这次运行"
```

也可以写进 `.zx-code/config.toml`：

```toml
[agent]
debug_log_enabled = true
debug_log_path = ".agent/debug.jsonl"
```

当前记录的事件包括：

- `run.system_prompt`：实际发送给模型的 system prompt
- `run.user_message`：用户本轮请求
- `run.model_input`：ContextGuard 处理后发给模型的 messages 和本轮 active tool schemas
- `model.request`：LiteLLM request kwargs，`api_key/token/secret` 类字段会脱敏
- `model.response.raw` / `model.stream.raw_summary`：模型 SDK 原始返回，或流式输出聚合摘要
- `model.response.normalized`：项目内部归一化后的 `ModelTurn`
- `tool.call.requested` / `tool.call.result`：工具调用入参和结果
- `tool.permission`：权限决策
- `tool.hook.pre` / `tool.hook.post`：工具 hook 执行结果

调试日志包含 system prompt、用户输入、模型输出和工具结果，可能带有敏感代码或业务数据。默认关闭，需要显式开启。

## ResilienceRunner

第五阶段 5E 把单次模型 turn 的恢复逻辑收敛到 `ResilienceRunner`。

Agent loop 仍然只调用一个函数：

```python
run_model_turn_with_recovery(...)
```

这个函数现在会创建 `ResilienceRunner` 并委托给它执行。这样保持了 `loop.py` 的调用面不变，同时让恢复策略有了明确的类边界。

当前 `ResilienceRunner` 负责四类恢复：

1. `timeout`
   - 单次模型 turn 超过 `model_timeout_s` 后抛出 `ModelTimeoutError`

2. `rate_limit`
   - 在单 profile 内按 `RecoveryBudget` 做有限 backoff retry
   - 多 profile 情况下，外层 `FallbackModelClient` 会优先切换备用 profile

3. `length / max_tokens`
   - 如果模型回复被截断且没有工具调用，会自动追加一条 continue 提示，再请求一次

4. `overflow`
   - 如果模型报上下文过长，会调用 `ContextGuard.compact_history()` 压缩历史后重试

这相当于把第五阶段路线图里的三层恢复洋葱拆成了清晰边界：

```text
FallbackModelClient   -> profile / key / model fallback
ResilienceRunner      -> timeout / backoff / overflow compact / continuation
Agent loop            -> tool-use loop 与 max iterations
```

## MCP、Worktree 和 Plugin

第五阶段最后补齐了三个平台化扩展点。

### MCP

当前实现基于官方 `mcp` Python SDK 的 stdio transport：

```toml
[agent]

[[agent.mcp_servers]]
name = "filesystem"
command = "python"
args = ["./mcp_servers/filesystem_server.py"]
env = { TOKEN = "optional" }
```

运行时会启动 server，建立 `ClientSession`，执行 initialize，然后通过 SDK 完成：

```text
list_tools -> call_tool
```

发现到的 MCP 工具会注册成：

```text
mcp__filesystem__read_file
```

这些工具不是绕过安全系统直接执行，而是进入同一个 `ToolRegistry`，因此可以继续用 `.zx-code/permissions.toml` 控制：

```toml
[[rules]]
tool = "mcp__filesystem__*"
decision = "ask"
```

### Worktree Isolation

启用 worktree 工具：

```bash
uv run agent \
  --worktree-isolation \
  --worktree-dir .agent/worktrees \
  "给这个任务创建隔离工作区并修改代码"
```

或者在配置中开启：

```toml
[agent]
enable_worktree_isolation = true
worktree_dir = ".agent/worktrees"
```

启用后会注册两个工具：

```text
worktree_create
worktree_cleanup
```

`worktree_create` 会基于当前 git 仓库创建独立 branch 和 worktree。Agent 后续可以把返回的 `path` 传给 `bash(workdir=...)`，或对该路径下的文件执行读写，从而把并行任务的文件修改隔离出去。

### Plugin System

插件是本地目录中的 `plugin.json`：

```json
{
  "name": "demo",
  "tools": [
    {
      "name": "echo",
      "description": "Echo input",
      "command": "python echo.py",
      "input_schema": {
        "type": "object",
        "properties": {
          "text": { "type": "string" }
        },
        "required": ["text"]
      }
    }
  ]
}
```

配置插件目录：

```toml
[agent]
plugin_dirs = [".zx-code/plugins"]
```

发现到的工具会注册成：

```text
plugin__demo__echo
```

插件命令会收到 JSON 参数作为 stdin，stdout 作为工具结果返回。插件工具同样经过 `ToolRegistry` 和 `PermissionManager`。

## Skill Loading 和 DAG Task System

这次按 `docs/12-Python实战技术选型.md` 补齐了两块原来缺失的能力。

### Skill Loading

默认技能目录是：

```text
skills/
```

如果项目没有 `skills/`，但存在 `workspace/skills/`，运行时会自动使用后者。

`SystemPromptBuilder` 只把技能索引注入 prompt：

```text
## Skills
Available skills. Load the full markdown with load_skill when needed:
- review - Review code for regressions first.
```

完整技能正文通过工具按需加载：

```text
load_skill({ "name": "review" })
```

这样符合“两层加载”：常驻 prompt 只放名字和描述，真正用到时再把 markdown 拉进上下文。

### DAG Task System

Todo 负责“当前会话内的轻量清单”，Task System 负责“跨压缩、跨重启、可表达依赖的任务 DAG”。

默认任务目录是：

```text
.tasks/
```

每个任务一个 JSON 文件，字段包括：

```json
{
  "id": "task-1234abcd",
  "title": "run verification",
  "status": "blocked",
  "blocked_by": ["task-parent"]
}
```

注册给 Agent 的工具：

```text
task_create
task_complete
task_list
```

当上游任务完成时，`TaskStore.complete()` 会检查所有 blocked task；如果它们的 `blocked_by` 都已完成，就自动解锁为 `pending`。

## CLI 参数

```bash
uv run agent --help
```

当前支持：

- `--model`
- `--fallback-models`
- `--max-turns`
- `--resume`
- `--session-id`
- `--data-dir`
- `--context-max-tokens`
- `--compact-model`
- `--skills-dir`
- `--tasks-dir`
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
- `--subagent-max-depth`
- `--worktree-dir`
- `--watch`
- `--no-stream`
- `--no-memory`
- `--no-skills`
- `--no-todos`
- `--no-tasks`
- `--no-subagents`
- `--worktree-isolation`
- `--debug-log`
- `--debug-log-path`
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
10. `docs/phase-05C-Subagent讲解.md`：理解第五阶段 5C 的子代理运行时
11. `docs/phase-05D-Profile与Fallback讲解.md`：理解第五阶段 5D 的模型 profile 和 fallback
12. `docs/phase-05E-ResilienceRunner讲解.md`：理解第五阶段 5E 的模型 turn 恢复器
13. `docs/phase-05F-MCP-Worktree-Plugin讲解.md`：理解第五阶段最后的 MCP、worktree 和 plugin
14. `docs/phase-05G-12技术选型对齐讲解.md`：理解 Skill Loading、DAG Task、BackgroundTaskManager
15. `src/agent/main.py`：看 CLI 如何组装运行时和 Gateway
16. `src/agent/gateway.py`：看路由、会话隔离和统一派发
17. `src/agent/skills.py`：看技能索引和按需加载
18. `src/agent/tasks.py`：看 DAG 任务持久化和依赖解锁
19. `src/agent/background.py`：看通用后台任务结果队列
20. `src/agent/subagent.py`：看子代理如何隔离会话并限制递归
21. `src/agent/profiles.py`：看模型 profile 和 fallback client
22. `src/agent/recovery.py`：看 `ResilienceRunner` 和错误分类
23. `src/agent/mcp/`：看 MCP stdio client 和工具路由
24. `src/agent/worktree.py`：看 git worktree 隔离
25. `src/agent/plugins.py`：看插件 manifest 和命令工具
26. `src/agent/delivery.py`：看可靠投递、锁和后台 daemon
27. `tests/`：看每个能力如何被验证

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
│   ├── phase-05B-Delivery-Daemon讲解.md
│   ├── phase-05C-Subagent讲解.md
│   ├── phase-05D-Profile与Fallback讲解.md
│   ├── phase-05E-ResilienceRunner讲解.md
│   ├── phase-05F-MCP-Worktree-Plugin讲解.md
│   └── phase-05G-12技术选型对齐讲解.md
├── src/
│   └── agent/
│       ├── background.py
│       ├── channels/
│       │   ├── base.py
│       │   ├── cli.py
│       │   ├── telegram.py
│       │   └── feishu.py
│       ├── config.py
│       ├── context.py
│       ├── compact.py
│       ├── cron.py
│       ├── delivery.py
│       ├── gateway.py
│       ├── heartbeat.py
│       ├── lanes.py
│       ├── main.py
│       ├── loop.py
│       ├── memory.py
│       ├── mcp_client.py
│       ├── mcp/
│       │   ├── client.py
│       │   └── router.py
│       ├── models.py
│       ├── permissions.py
│       ├── planning.py
│       ├── plugins.py
│       ├── prompt.py
│       ├── profiles.py
│       ├── recovery.py
│       ├── sessions.py
│       ├── skills.py
│       ├── subagent.py
│       ├── tasks.py
│       ├── todo.py
│       ├── worktree.py
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
│           ├── skill.py
│           ├── subagent.py
│           ├── tasks.py
│           ├── todo.py
│           └── worktree.py
└── tests/
    ├── test_channels.py
    ├── test_background_tasks.py
    ├── test_config.py
    ├── test_context.py
    ├── test_delivery.py
    ├── test_gateway.py
    ├── test_heartbeat_cron.py
    ├── test_lanes.py
    ├── test_loop.py
    ├── test_memory_todo_prompt.py
    ├── test_mcp.py
    ├── test_permissions.py
    ├── test_plugins.py
    ├── test_profiles.py
    ├── test_provider_mock.py
    ├── test_recovery.py
    ├── test_resilience_runner.py
    ├── test_sessions.py
    ├── test_skill_loading.py
    ├── test_subagent.py
    ├── test_task_dag.py
    ├── test_tools.py
    └── test_worktree.py
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
- MCP、worktree、plugin 工具最终都会进入这里，统一走权限检查

`src/agent/main.py`

- 负责 CLI 入口
- 支持单次执行和 REPL
- 负责组装配置、会话、上下文、记忆、todo、权限和 prompt builder
- 负责把 CLI / Telegram / 飞书入口接入 Gateway
- 支持 `--watch` 持续监听通道
- 在 watch loop 中驱动 Delivery、Heartbeat 和 Cron tick
- 构造 `SubagentRunner`，并控制子 Agent 递归深度
- 注册 MCP 工具、worktree 工具和 plugin 工具

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
- 支持 project instructions、skills、memory、tasks、todo、runtime 注入
- prompt 只注入技能索引，不直接塞入完整技能正文
- Runtime section 包含当前日期、模型、平台和 Python 版本
- Tools section 从实际 `ToolRegistry.schemas()` 渲染工具名和描述；完整参数 schema 通过 `tool_search` 按需激活

`src/agent/profiles.py`

- 定义 `ModelProfile`
- 从 `api_key_env` 读取运行时 key，不把真实 key 写入配置
- 定义 `ProfileManager`，管理 profile cooldown
- 定义 `FallbackModelClient`，在可恢复模型失败时切换备用 profile

`src/agent/permissions.py`

- 在工具执行前判断 `allow / deny / ask`
- 危险 bash 命令默认需要确认
- `write_file` / `edit_file` 对敏感路径和工作目录外写入默认需要确认
- 检测符号链接路径

`src/agent/memory.py`

- 管理 `.memory/MEMORY.md`
- 使用 frontmatter + markdown 存储长期记忆
- 支持 `MemoryRecord` 命名记忆，把单条结构化记忆保存为 `.memory/<name>.md`
- `MEMORY.md` 可作为索引注入 prompt
- 写入使用原子替换（write-to-temp + rename），防止中断丢数据

`src/agent/skills.py`

- 定义 `SkillStore`
- 从 `skills/` 或 `workspace/skills/` 读取 markdown 技能
- 解析 frontmatter 中的 `description`
- 给 prompt 渲染轻量技能索引
- 完整正文通过 `load_skill` 工具按需读取

`src/agent/tasks.py`

- 定义 `TaskStore`
- 每个任务一个 JSON 文件，默认保存在 `.tasks/`
- 使用 `blocked_by` 表达 DAG 依赖
- 上游任务完成后自动解锁下游 pending 任务
- 可渲染当前 DAG 任务状态给 prompt

`src/agent/background.py`

- 定义 `BackgroundTaskManager`
- 使用 `asyncio.create_task` 启动后台协程
- 用 `asyncio.Queue` 返回成功或失败结果

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
- 支持当前 worker 内嵌套执行，防止同步子 Agent 调用死锁

`src/agent/recovery.py`

- 定义 Agent 运行时错误类型
- `classify_error()` 将模型错误归类为 `rate_limit / auth / timeout / overflow / billing / unknown`
- 定义 `ResilienceRunner`，统一处理单次模型 turn 的超时、截断续写、rate limit backoff 和 overflow compact
- `run_model_turn_with_recovery()` 保持旧入口兼容，内部委托给 `ResilienceRunner`

`src/agent/mcp/client.py`

- 定义 `MCPServerConfig`
- 定义 `MCPToolDefinition`
- 实现 `StdioMCPClient`
- 基于官方 `mcp` Python SDK 创建 stdio client 和 `ClientSession`
- 支持通过 SDK `list_tools()` 和 `call_tool()`

`src/agent/mcp/router.py`

- 定义 `MCPToolRouter`
- 把 MCP server 工具名转成 `mcp__server__tool`
- 定义 `MCPProxyTool`
- MCP 工具调用最终进入现有 `ToolRegistry` 和 `PermissionManager`

`src/agent/subagent.py`

- 定义 `SubagentRunner`
- 为每次子 Agent 调用生成独立 child session id
- 控制 `subagent_max_depth`，避免无限递归
- 可选接入 `LaneScheduler`，把子任务记录到 `subagent` lane
- 提供 `spawn_background()`，用 `asyncio.create_task + Queue` 返回后台子 Agent 结果

`src/agent/tools/subagent.py`

- 定义 `subagent_run` 工具
- 校验 `task` 和 `label`
- 调用 `SubagentRunner.run()`
- 把子会话 id、执行深度和最终文本返回给主 Agent

`src/agent/worktree.py`

- 定义 `WorktreeManager`
- 使用 `git worktree add -b` 创建独立分支和工作区
- 使用 `git worktree remove --force` 清理工作区
- 定义 `WorktreeLease`，记录 task id、branch 和 path

`src/agent/tools/worktree.py`

- 定义 `worktree_create`
- 定义 `worktree_cleanup`
- 让 Agent 可以通过工具显式管理隔离工作区

`src/agent/plugins.py`

- 定义 `PluginManifest`
- 定义 `PluginToolConfig`
- 定义 `PluginManager`
- 从插件目录读取 `plugin.json`
- 把插件命令注册成 `plugin__plugin__tool`

## 当前限制

当前还没有：

- 飞书加密事件解密
- 飞书富文本回复
- Lane 目前是 turn 级协作式调度，不做 LLM mid-turn 抢占
- Subagent 当前不会自动分配 worktree；需要显式使用 `worktree_create`
- ResilienceRunner 当前聚焦单次模型 turn，还没有把恢复事件持久化成 TraceEvent
- MCP 当前只接入 stdio transport，还没有 SSE/HTTP transport
- Plugin 当前是本地命令型插件，还没有远程 marketplace、签名校验或安装器
- `s15-s17 Agent Teams` 按当前计划暂未实现

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
- `docs/phase-05C-Subagent讲解.md`
- `docs/phase-05D-Profile与Fallback讲解.md`
- `docs/phase-05E-ResilienceRunner讲解.md`
- `docs/phase-05F-MCP-Worktree-Plugin讲解.md`
- `docs/phase-05G-12技术选型对齐讲解.md`

当前代码对应 `docs/ZX-code.md` 的第五阶段完成态，并补齐 `docs/12-Python实战技术选型.md` 中除 Agent Teams 外的关键实现点。

## 建议的下一步

比较顺的推进顺序是：

1. 进入第六阶段 TraceEvent / TraceWriter / TraceReader
2. 接入 `agent trace show <run_id>`
3. 做 Eval Harness 和 case runner
4. 做 Budget Optimizer，用 trace + eval 数据证明策略有效
5. 可并行补飞书加密事件解密和 MCP transport 扩展

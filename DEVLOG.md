# Devlog

这个文件记录每次阶段性代码更新后的开发日志。

维护规则：

1. 每次更新代码后，都要更新 `README.md`
2. 每次更新代码后，都要在 `DEVLOG.md` 追加一条记录
3. 每完成一个阶段，都要在 `docs/` 下新增对应的阶段讲解文档
4. Devlog 要写清楚本次改了什么、为什么这么改、怎么验证、读者下一步应该看哪里

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

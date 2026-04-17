---

Harness = Agent Brain + Runtime + Gateway + Delivery + Permissions

Model = LLM

---

# ZX-code 实施路线图

## 核心目标

做一个以 Python 为主的 Coding Agent，同时吸收 `Codex / Claude Code / Pi-Mono / OpenClaw` 的长处，但不照抄任何一个仓库。

这份路线图只保留真正要实现的东西：

1. 先做一个能在本地 CLI 稳定工作的单 Agent
2. 再补上下文、记忆、权限、持久化
3. 再加入 OpenClaw 风格的通道、网关、路由、投递
4. 最后补主动行为、并发、MCP、插件、隔离

## 这次明确纳入的 OpenClaw 能力

从 `../claw0` 和 OpenClaw 的思路里，明确吸收下面几项：

1. `InboundMessage` 统一入站消息模型
2. `Channel` 抽象层，先接 CLI，再接 Telegram / 飞书
3. `BindingTable + dm_scope` 路由与会话隔离
4. `DeliveryQueue` 预写消息队列，保证消息不丢
5. `Heartbeat + Cron` 主动型 Agent 运行时
6. `Named Lanes` 命名车道并发模型
7. 分层 system prompt 和工作区文件驱动的人格/记忆/技能注入

## 关于“手机上直接操作”

可以，建议路径不是先做移动 App，而是先做通道接入：

1. 第三阶段接入 `Telegram` 或 `飞书`
2. 用户在手机上直接给 bot 发消息
3. 网关把消息归一化为 `InboundMessage`
4. 同一个 Agent Brain 处理消息
5. 回复通过 `DeliveryQueue` 安全投递回手机

也就是说，完成第三阶段后，就应该能“手机上直接用”，不需要单独开发 iOS / Android 客户端。

## 建议目录

```bash
agent-cli/
├── pyproject.toml
├── src/
│   └── agent/
│       ├── main.py
│       ├── models.py
│       ├── loop.py
│       ├── prompt.py
│       ├── recovery.py
│       ├── permissions.py
│       ├── sessions.py
│       ├── memory.py
│       ├── todo.py
│       ├── delivery.py
│       ├── heartbeat.py
│       ├── cron.py
│       ├── lanes.py
│       ├── gateway.py
│       ├── channels/
│       │   ├── base.py
│       │   ├── cli.py
│       │   ├── telegram.py
│       │   └── feishu.py
│       ├── tools/
│       │   ├── base.py
│       │   ├── registry.py
│       │   ├── bash.py
│       │   ├── read_file.py
│       │   ├── write_file.py
│       │   ├── edit_file.py
│       │   └── grep.py
│       ├── providers/
│       │   ├── base.py
│       │   └── litellm_client.py
│       ├── mcp/
│       │   ├── client.py
│       │   ├── router.py
│       │   └── permission_gate.py
│       └── worktree.py
├── workspace/
│   ├── IDENTITY.md
│   ├── SOUL.md
│   ├── TOOLS.md
│   ├── MEMORY.md
│   ├── HEARTBEAT.md
│   ├── CRON.json
│   └── skills/
├── tests/
└── README.md
```

## 第一阶段：单 Agent 核心

### 阶段目标

做出一个稳定的本地 CLI Agent MVP，完成最小闭环：

`user input -> model -> tool use -> tool_result -> final answer`

### 本阶段应完成的任务

1. 建立 Python 项目骨架
2. 实现 `Agent Loop`
3. 实现 `ModelClient` 抽象与首个 provider
4. 实现 `ToolRegistry`
5. 接入基础工具：`bash / read_file / write_file / edit_file / grep`
6. 提供 CLI：REPL 模式 + 一次性执行模式
7. 提供最小流式输出
8. 做基础错误恢复：超时、模型报错、max iterations
9. 补基础自动化测试

### 使用的方法

- 语言：Python 3.11+
- 异步：`asyncio`
- 模型层：`litellm` 统一抽象
- CLI：`typer`
- 输出：`rich`
- 数据模型：`pydantic v2`
- 测试：`pytest + pytest-asyncio`

### 验收要求

1. 用户输入一个真实任务，Agent 能完成至少一轮工具调用
2. 五个基础工具能被模型正确调用
3. 工具参数错误时，返回可读错误，而不是直接崩
4. CLI 既支持 REPL，也支持 `agent "task here"` 这类一次性调用
5. 有 loop、tools、provider mock 三类基础测试

### 参考伪代码

```python
from pydantic import BaseModel
from typing import Protocol


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict


class ModelTurn(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = []
    stop_reason: str = "end_turn"


class ModelClient(Protocol):
    async def run_turn(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict],
    ) -> ModelTurn: ...


async def agent_loop(state, tool_registry, model_client):
    while state.turn_count < state.max_turns:
        state.turn_count += 1

        turn = await model_client.run_turn(
            system_prompt=state.system_prompt,
            messages=state.messages,
            tools=tool_registry.schemas(),
        )

        state.messages.append({
            "role": "assistant",
            "content": turn.text,
        })

        if not turn.tool_calls:
            return turn.text

        tool_results = []
        for call in turn.tool_calls:
            result = await tool_registry.execute(call.name, call.arguments)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": result.content,
                "is_error": result.is_error,
            })

        state.messages.append({
            "role": "user",
            "content": tool_results,
        })
```

---

## 第二阶段：持久化、上下文、权限、记忆

### 阶段目标

把单次运行的 Agent 升级成可连续使用的本地助手，让会话、上下文和安全边界稳定下来。

### 本阶段应完成的任务

1. 实现 `SessionStore`，使用 JSONL 持久化消息
2. 实现历史重放，把 JSONL 还原为模型可消费的 `messages`
3. 实现 `ContextGuard`，处理上下文过长
4. 实现 `SystemPromptBuilder`
5. 实现 `.memory/` + `MEMORY.md` 记忆系统
6. 实现 `PermissionManager`
7. 实现 `TodoManager`
8. 实现三层配置：CLI > 项目 > 用户
9. 补会话、权限、记忆、prompt builder 的测试

### 使用的方法

- Session：JSONL append-only
- Context：正常调用 -> 截断大工具结果 -> LLM 摘要压缩
- Prompt：分层 builder，不直接字符串乱拼
- Memory：frontmatter + markdown
- Permission：`allow / deny / ask`
- Config：TOML + Pydantic schema

### 验收要求

1. 重启进程后能恢复上一次会话
2. 历史过长时不会直接炸掉，能自动截断或压缩
3. system prompt 可以清楚打印出各个 section
4. 危险命令默认 ask 或 deny
5. todo 能创建、更新、完成，且不会在普通轮次中丢失
6. memory 会在新会话中继续生效，但不会覆盖代码事实

### 参考伪代码

```python
class SessionStore:
    def append(self, session_id: str, record: dict) -> None:
        path = self._path_for(session_id)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def rebuild_messages(self, session_id: str) -> list[dict]:
        messages = []
        for record in self._read_records(session_id):
            rtype = record["type"]
            if rtype == "user":
                messages.append({"role": "user", "content": record["content"]})
            elif rtype == "assistant":
                messages.append({"role": "assistant", "content": record["content"]})
            elif rtype == "tool_use":
                if messages and messages[-1]["role"] == "assistant":
                    messages[-1]["content"].append(record["block"])
            elif rtype == "tool_result":
                if messages and messages[-1]["role"] == "user":
                    messages[-1]["content"].append(record["block"])
        return messages


async def guarded_model_call(client, system_prompt, messages, tools):
    current = messages
    for stage in ("normal", "truncate", "compact"):
        try:
            return await client.run_turn(system_prompt, current, tools)
        except ContextOverflowError:
            if stage == "normal":
                current = truncate_large_tool_results(current)
            elif stage == "truncate":
                current = await compact_history(current)
            else:
                raise
```

```python
class SystemPromptBuilder:
    def build(self, state) -> str:
        parts = [
            self.identity_block(state),
            self.tool_block(state),
            self.todo_block(state),
            self.memory_block(state),
            self.project_block(state),
            self.runtime_block(state),
        ]
        return "\n\n".join(p for p in parts if p.strip())
```

---

## 第三阶段：多通道、网关、手机可操作

### 阶段目标

把本地 CLI Agent 升级成一个可通过手机直接使用的 Agent Gateway。

这一阶段完成后，应该能通过 Telegram 或飞书直接和 Agent 对话。

### 本阶段应完成的任务

1. 定义统一的 `InboundMessage`
2. 实现 `Channel` 抽象基类
3. 实现 `CLIChannel`
4. 实现 `TelegramChannel`
5. 预留 `FeishuChannel`
6. 实现 `ChannelManager`
7. 实现 `BindingTable`
8. 实现 `Gateway`
9. 实现 `build_session_key(dm_scope=...)`
10. 支持手动切换 agent / 强制路由
11. 补通道与路由测试

### 使用的方法

- 通道层：每个平台都只做 `receive / send`
- 网关层：所有通道先归一化成 `InboundMessage`
- 路由层：5 级绑定
  - Tier 1: `peer_id`
  - Tier 2: `guild_id`
  - Tier 3: `account_id`
  - Tier 4: `channel`
  - Tier 5: `default`
- 会话隔离：`dm_scope`
- 手机操作：优先 Telegram，后补飞书

### 验收要求

1. CLI、Telegram 两个入口都能走同一条 Agent Brain 管线
2. 手机上发消息，能收到正确回复
3. 不同用户、不同群组、不同通道可以正确隔离会话
4. 路由绑定能做到“最具体规则优先”
5. 切换 agent 或关闭强制路由后，行为符合预期

### 参考伪代码

```python
from dataclasses import dataclass, field
from abc import ABC, abstractmethod


@dataclass
class InboundMessage:
    text: str
    sender_id: str
    channel: str
    account_id: str
    peer_id: str
    is_group: bool = False
    media: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class Channel(ABC):
    name: str = "unknown"

    @abstractmethod
    async def receive(self) -> InboundMessage | None: ...

    @abstractmethod
    async def send(self, to: str, text: str, **kwargs) -> bool: ...


def build_session_key(agent_id: str, channel: str, account_id: str, peer_id: str, dm_scope: str):
    if dm_scope == "per-account-channel-peer":
        return f"agent:{agent_id}:{channel}:{account_id}:direct:{peer_id}"
    if dm_scope == "per-channel-peer":
        return f"agent:{agent_id}:{channel}:direct:{peer_id}"
    if dm_scope == "per-peer":
        return f"agent:{agent_id}:direct:{peer_id}"
    return f"agent:{agent_id}:main"


class BindingTable:
    def resolve(self, channel="", account_id="", guild_id="", peer_id=""):
        for binding in self.bindings:
            if binding.matches(channel, account_id, guild_id, peer_id):
                return binding.agent_id
        return self.default_agent_id


async def handle_inbound(inbound: InboundMessage, gateway):
    agent_id = gateway.binding_table.resolve(
        channel=inbound.channel,
        account_id=inbound.account_id,
        peer_id=inbound.peer_id,
    )
    session_key = build_session_key(
        agent_id=agent_id,
        channel=inbound.channel,
        account_id=inbound.account_id,
        peer_id=inbound.peer_id,
        dm_scope=gateway.agent_config(agent_id).dm_scope,
    )
    reply = await gateway.run_agent_turn(agent_id, session_key, inbound.text)
    await gateway.channel_manager.get(inbound.channel).send(inbound.peer_id, reply)
```

---

## 第四阶段：主动行为与可靠投递

### 阶段目标

让 Agent 不只“被动回复”，还能够主动运行、定时执行，并把结果可靠地送回用户。

这阶段也是手机使用体验真正稳定下来的关键。

### 本阶段应完成的任务

1. 实现 `DeliveryQueue`
2. 实现原子写入：`tmp + fsync + os.replace`
3. 实现 `DeliveryRunner`
4. 实现消息退避重试与失败目录
5. 实现按平台上限的 `chunk_message`
6. 实现 `HeartbeatRunner`
7. 实现 `CronScheduler`
8. 心跳、定时任务统一复用同一条 agent turn 管线
9. 补 delivery、heartbeat、cron 的集成测试

### 使用的方法

- 投递：先写磁盘，再发送
- 重试：指数退避 + jitter
- 心跳：后台轮询 + 用户优先
- Cron：支持 `at / every / cron`
- 主动输出：统一进入 `DeliveryQueue`

### 验收要求

1. 即使发送时进程崩溃，待投递消息也不会丢
2. Telegram 长消息能够自动切块
3. 心跳不会抢占用户当前对话
4. 定时任务可以在未来某个时间自动执行
5. 心跳和 cron 的输出能稳定推送到手机端
6. 连续失败会进入重试和失败目录，而不是静默丢失

### 参考伪代码

```python
class DeliveryQueue:
    def enqueue(self, channel: str, to: str, text: str) -> str:
        delivery_id = uuid.uuid4().hex[:12]
        entry = {
            "id": delivery_id,
            "channel": channel,
            "to": to,
            "text": text,
            "retry_count": 0,
            "next_retry_at": 0.0,
        }
        self._atomic_write(entry)
        return delivery_id

    def _atomic_write(self, entry: dict) -> None:
        tmp_path = self.queue_dir / f".tmp.{entry['id']}.json"
        final_path = self.queue_dir / f"{entry['id']}.json"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, final_path)
```

```python
class HeartbeatRunner:
    def tick(self):
        if not self.should_run():
            return
        if self.user_lane_is_busy():
            return

        text = self.run_single_turn_from_heartbeat_prompt()
        if text and text.strip() != "HEARTBEAT_OK":
            self.delivery_queue.enqueue(
                channel=self.target_channel,
                to=self.target_peer,
                text=text,
            )
```

```python
class CronScheduler:
    def tick(self, now: float):
        for job in self.jobs:
            if not job.enabled:
                continue
            if self.is_due(job, now):
                self.enqueue_job(job)
```

---

## 第五阶段：生产级加固与平台化

### 阶段目标

把系统从“可用的 Agent Gateway”推进到“可扩展、可恢复、可并行的运行平台”。

### 本阶段应完成的任务

1. 实现 `ResilienceRunner`
2. 支持多 profile / 多 key / fallback model
3. 实现失败分类：`rate_limit / auth / timeout / overflow / billing / unknown`
4. 实现 `Named Lanes`
5. 实现 `Subagent`
6. 实现 `MCP` 客户端与工具路由
7. 实现 `Hooks`
8. 实现 `Worktree Isolation`
9. 视时间推进 `Plugin System`
10. 补并发、恢复、MCP、隔离测试

### 使用的方法

- 弹性：3 层重试洋葱
  - Layer 1: 认证与 profile 轮换
  - Layer 2: overflow 截断与压缩
  - Layer 3: tool-use loop
- 并发：按 lane 串行化，不用全局大锁
- Subagent：独立消息上下文，禁止无限递归
- MCP：先做 stdio JSON-RPC
- Hooks：外部脚本 + trust gate
- Worktree：按任务分配独立 git worktree

### 验收要求

1. 主 key 限流或失效时，系统能自动轮换到备用 key
2. overflow 时会先截断、再压缩，而不是直接失败
3. `main / cron / heartbeat` 至少三个 lane 独立运行
4. 用户消息优先级高于 heartbeat 和 cron
5. subagent 不会污染主会话，也不会无限递归
6. MCP 工具可被发现并通过统一权限系统调用
7. 并行任务可在 worktree 中隔离执行

### 参考伪代码

```python
class ResilienceRunner:
    async def run(self, system_prompt, messages, tools):
        for profile in self.profile_manager.available_profiles():
            current_messages = list(messages)
            for compact_attempt in range(3):
                try:
                    return await self._run_attempt(
                        profile=profile,
                        system_prompt=system_prompt,
                        messages=current_messages,
                        tools=tools,
                    )
                except OverflowError:
                    current_messages = truncate_tool_results(current_messages)
                    current_messages = await compact_history(current_messages)
                    continue
                except RateLimitError:
                    self.profile_manager.cooldown(profile, seconds=120)
                    break
                except AuthError:
                    self.profile_manager.cooldown(profile, seconds=300)
                    break
        raise RuntimeError("all profiles exhausted")
```

```python
class LaneQueue:
    def __init__(self, name: str, max_concurrency: int = 1):
        self.name = name
        self.max_concurrency = max_concurrency
        self._deque = deque()
        self._active = 0
        self._generation = 0
        self._condition = threading.Condition()

    def enqueue(self, fn):
        future = concurrent.futures.Future()
        with self._condition:
            self._deque.append((fn, future, self._generation))
            self._pump()
        return future

    def _pump(self):
        while self._active < self.max_concurrency and self._deque:
            fn, future, gen = self._deque.popleft()
            self._active += 1
            threading.Thread(target=self._run_task, args=(fn, future, gen), daemon=True).start()
```

```python
class MCPToolRouter:
    async def call_tool(self, full_name: str, arguments: dict):
        # mcp__filesystem__read_file -> ("filesystem", "read_file")
        server_name, tool_name = self.parse(full_name)
        gate_decision = self.permission_gate.check(server_name, tool_name, arguments)
        if gate_decision == "deny":
            raise PermissionError(full_name)
        return await self.clients[server_name].call_tool(tool_name, arguments)
```

---

## 实施顺序总结

### 第 1 周

完成第一阶段，拿到一个能本地跑起来的 CLI Coding Agent。

### 第 2 周

完成第二阶段，拿到一个有记忆、会话、权限、todo 的单 Agent。

### 第 3 周

完成第三阶段，接通 Telegram，实现在手机上直接操作。

### 第 4 周

完成第四阶段，补齐 delivery / heartbeat / cron，让 Agent 能可靠地主动触达用户。

### 第 5 周及以后

完成第五阶段，补 MCP、subagent、worktree、lanes、resilience，进入平台化能力。

## 最终判断标准

这个项目成功，不是因为模块名很多，而是因为最后能做到下面四件事：

1. 在本地 CLI 稳定完成 coding task
2. 重启后不丢上下文、不丢关键状态
3. 能通过手机上的 Telegram 或飞书直接使用
4. 长时间运行后仍然具备可靠投递、限流恢复和并发秩序

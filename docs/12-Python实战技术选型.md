# 12 — Python 实战：每个模块的技术选型与实现指南

> 目标：从零用 Python 实现一个生产级 Coding Agent，每个模块给出 2-3 个方案对比，
> 选出最优解并说明原因。适合写进简历展示工程判断力。

---

## 〇、全局架构决策（先定这些，后面模块才好选）

### 0.1 LLM SDK

| 方案 | 优点 | 缺点 |
|------|------|------|
| `anthropic` 官方 SDK | 流式原生支持、tool_use 一等公民、extended thinking | 只能调 Claude |
| `openai` 官方 SDK | 生态最大、文档最全 | tool_use 格式和 Anthropic 不同 |
| `litellm` | 100+ 模型统一接口、一行切换供应商 | 多一层抽象、更新可能滞后、流式偶有 bug |

**选择：`litellm`**

```
理由：
1. 简历亮点 — 展示"供应商无关"的架构能力，比绑定单家更有说服力
2. 实际价值 — 开发时用便宜模型（GPT-4o-mini），演示时切 Claude Opus
3. litellm 的 completion() 接口统一了 tool_use 格式，上层代码不感知供应商差异
4. 如果 litellm 有 bug，随时可以降级到官方 SDK（接口兼容）

安装: pip install litellm
```

### 0.2 异步框架

| 方案 | 优点 | 缺点 |
|------|------|------|
| 同步 `while True` | 最简单、零学习成本 | 阻塞 I/O、无法后台任务 |
| `asyncio` | 标准库、生态最大、并发 I/O | 需要 async/await 全链路 |
| `trio` | 结构化并发、更安全 | 生态小、第三方库支持少 |

**选择：`asyncio`**

```
理由：
1. 标准库，无额外依赖
2. litellm/anthropic/openai SDK 都有 async 版本
3. 后台任务(s13)、多代理(s15)天然需要并发
4. 简历上写"基于 asyncio 的异步 Agent 架构"比 while True 专业得多
```

### 0.3 CLI 框架

| 方案 | 优点 | 缺点 |
|------|------|------|
| `argparse` | 标准库、零依赖 | 代码冗长、无自动补全 |
| `click` | 装饰器风格、成熟稳定 | 不支持类型提示自动推断 |
| `typer` | 基于类型提示自动生成 CLI、自动补全 | 依赖 click |

**选择：`typer`**

```
理由：
1. 类型提示 = 参数定义，代码最简洁
2. 自动生成 --help，自动类型校验
3. 简历上体现"现代 Python 工程实践"
```

### 0.4 数据模型

| 方案 | 优点 | 缺点 |
|------|------|------|
| `dict` | 零开销、灵活 | 无验证、无补全、易写错 key |
| `dataclass` | 标准库、轻量 | 无自动验证、无 JSON Schema 生成 |
| `pydantic v2` | 自动验证、JSON Schema、序列化 | 多一个依赖、学习成本 |

**选择：`pydantic v2`**

```
理由：
1. 工具输入验证(s02)、配置管理(s10)、任务记录(s12)全都需要验证
2. model_json_schema() 一行生成 JSON Schema → 直接喂给 LLM 的 tool 定义
3. 和 typer/FastAPI 生态无缝集成
4. 简历关键词：pydantic 是 Python AI 工程的标配
```

### 0.5 项目结构

```
my-agent/
├── pyproject.toml          # 项目配置（用 uv 管理）
├── src/
│   └── agent/
│       ├── __init__.py
│       ├── main.py          # CLI 入口 (typer)
│       ├── loop.py          # s01: Agent Loop
│       ├── tools/           # s02: 工具系统
│       │   ├── __init__.py
│       │   ├── registry.py  # 工具注册表
│       │   ├── bash.py
│       │   ├── read.py
│       │   ├── write.py
│       │   ├── edit.py
│       │   └── grep.py
│       ├── planning.py      # s03: TodoWrite
│       ├── subagent.py      # s04: Subagents
│       ├── skills.py        # s05: Skill Loading
│       ├── compact.py       # s06: Context Compact
│       ├── permissions.py   # s07: Permission System
│       ├── hooks.py         # s08: Hook System
│       ├── memory.py        # s09: Memory System
│       ├── prompt.py        # s10: System Prompt Builder
│       ├── recovery.py      # s11: Error Recovery
│       ├── tasks.py         # s12: Task System
│       ├── background.py    # s13: Background Tasks
│       ├── cron.py          # s14: Cron Scheduler
│       ├── team.py          # s15-s17: Teams
│       ├── worktree.py      # s18: Worktree Isolation
│       ├── mcp_client.py    # s19: MCP
│       └── models.py        # 公共 pydantic 模型
├── skills/                  # 技能 markdown 文件
├── .tasks/                  # 任务持久化
├── .memory/                 # 记忆持久化
├── tests/
└── CLAUDE.md
```

---

## 一、s01 Agent Loop — 核心循环

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 同步 `while True` | 30 行搞定、最易理解 | 流式阻塞、无法后台任务 |
| B. `asyncio` + `async for` | 流式原生、可并发 | 需要 async 全链路 |
| C. `asyncio` + `async generator` | 和 Claude Code 一样的拉取式 | 稍复杂 |

**选择：B — `asyncio` + `async for` 消费流**

```
理由：
1. async generator(C) 在 Python 里调试困难（yield 语义复杂）
2. async for 消费 litellm 的流足够了，不需要自己写 generator
3. 比同步循环多了：流式输出、后台任务、中断支持
4. 后续模块(s13 后台、s15 团队)天然需要 asyncio
```

**伪代码**：

```python
# loop.py
import asyncio
from pydantic import BaseModel
from litellm import acompletion

class LoopState(BaseModel):
    messages: list[dict]
    turn_count: int = 0
    should_continue: bool = True

async def agent_loop(state: LoopState, tools: list[dict]):
    while state.should_continue:
        state.turn_count += 1

        # 流式调用 LLM
        response = await acompletion(
            model="anthropic/claude-sonnet-4-20250514",
            messages=state.messages,
            tools=tools,
            stream=True,
        )

        assistant_content = []
        async for chunk in response:
            delta = chunk.choices[0].delta
            # 实时输出文本
            if delta.content:
                print(delta.content, end="", flush=True)
                assistant_content.append(delta.content)
            # 收集工具调用
            if delta.tool_calls:
                # ... 累积 tool_call chunks
                pass

        # 提取工具调用
        tool_calls = extract_tool_calls(response)

        if not tool_calls:
            state.should_continue = False
            break

        # 执行工具并回写结果
        state.messages.append({"role": "assistant", "tool_calls": tool_calls})
        for call in tool_calls:
            result = await execute_tool(call)
            state.messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": result,
            })
```

---

## 二、s02 Tool Use — 工具系统

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 字典 `{name: func}` | 最简单 | 无验证、无元数据 |
| B. 装饰器注册 `@tool` | Pythonic、自动收集 | 自定义逻辑有限 |
| C. Pydantic 模型 + 注册表类 | 类型安全、自动 JSON Schema | 代码稍多 |

**选择：C — Pydantic 模型 + 注册表**

```
理由：
1. Pydantic 的 model_json_schema() 自动生成 LLM 需要的工具 schema
2. 输入验证自动完成（模型生成了错误参数？pydantic 直接报错）
3. 注册表类方便后续扩展（MCP 工具、技能工具动态注册）
4. 简历亮点：展示"类型驱动的工具系统设计"
```

**伪代码**：

```python
# tools/registry.py
from pydantic import BaseModel
from typing import Callable, Any
from abc import ABC, abstractmethod

class ToolResult(BaseModel):
    content: str
    is_error: bool = False

class BaseTool(ABC):
    name: str
    description: str
    parameters_model: type[BaseModel]  # Pydantic 模型定义参数

    def schema(self) -> dict:
        """自动生成 LLM tool schema"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_model.model_json_schema(),
            }
        }

    def validate_input(self, raw: dict) -> BaseModel:
        return self.parameters_model.model_validate(raw)

    @abstractmethod
    async def execute(self, params: BaseModel) -> ToolResult: ...

    def is_concurrency_safe(self, params: BaseModel) -> bool:
        return False  # 默认不安全，子类覆盖

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool):
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all_schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    async def execute(self, name: str, raw_input: dict) -> ToolResult:
        tool = self._tools[name]
        params = tool.validate_input(raw_input)  # 自动验证
        return await tool.execute(params)

# tools/read.py — 示例工具
class ReadFileParams(BaseModel):
    file_path: str
    offset: int = 0
    limit: int = 2000

class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read file contents"
    parameters_model = ReadFileParams

    async def execute(self, params: ReadFileParams) -> ToolResult:
        path = self._safe_path(params.file_path)  # 路径沙箱
        lines = open(path).readlines()
        selected = lines[params.offset : params.offset + params.limit]
        return ToolResult(content="".join(selected))

    def is_concurrency_safe(self, params) -> bool:
        return True  # 读操作永远安全

    def _safe_path(self, p: str) -> str:
        resolved = os.path.abspath(p)
        if not resolved.startswith(WORKSPACE):
            raise PermissionError(f"路径越界: {resolved}")
        return resolved
```

---

## 三、s03 TodoWrite — 会话内规划

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 纯内存 list | 最快 | 压缩后丢失 |
| B. JSON 文件持久化 | 可恢复、可查看 | I/O 开销 |
| C. SQLite | 结构化查询 | 过重 |

**选择：B — JSON 文件**

```
理由：
1. 清单需要跨 compact 存活（内存不够）
2. JSON 可以人工查看和调试（比 SQLite 透明）
3. 和 s12 Task System 保持一致（都用文件持久化）
4. 性能不是瓶颈——清单更新频率极低
```

---

## 四、s04 Subagent — 子代理

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. `asyncio.create_task` | 轻量、共享事件循环 | 共享内存，隔离差 |
| B. `multiprocessing.Process` | 真正进程隔离 | 通信复杂、overhead 大 |
| C. 新的 `agent_loop()` 调用（函数级隔离）| 简单、messages 天然隔离 | 串行阻塞主循环 |

**选择：A + C 结合 — `asyncio.create_task` 包装独立的 `agent_loop()` 调用**

```
理由：
1. 子代理的核心是 messages 隔离（空的 messages 列表），不需要进程隔离
2. asyncio.create_task 让子代理不阻塞主循环（后台运行 ✓）
3. 函数级隔离足够——子代理用独立的 LoopState，和父代理没有共享状态
4. 结合 asyncio.Queue 做结果通知

简历表述："基于 asyncio 协程的轻量级子代理隔离，
          上下文独立但共享工具注册表"
```

**伪代码**：

```python
# subagent.py
async def run_subagent(
    task: str,
    tools: ToolRegistry,
    model: str = "anthropic/claude-sonnet-4-20250514",
) -> str:
    """在独立 context 中运行子代理，只返回摘要"""
    child_state = LoopState(
        messages=[{"role": "user", "content": task}]
    )
    await agent_loop(child_state, tools.all_schemas())

    # 只提取最终回复
    last = child_state.messages[-1]
    return last.get("content", "")

# 后台运行
async def spawn_background_agent(task: str, tools: ToolRegistry):
    result_queue = asyncio.Queue()

    async def _run():
        result = await run_subagent(task, tools)
        await result_queue.put(result)

    asyncio.create_task(_run())
    return result_queue  # 主循环可以 await queue.get()
```

---

## 五、s05 Skill Loading — 技能加载

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 全部塞 system prompt | 最简单 | 浪费 token |
| B. 两层加载（名字 + 按需加载）| 节省 token | 需要一个工具 |
| C. 嵌入向量检索（RAG） | 自动匹配相关技能 | 复杂度高、准确率不稳定 |

**选择：B — 两层加载**

```
理由：
1. 和 learn-claude-code / Claude Code 的方案一致，验证过的最佳实践
2. 实现简单——只需一个 load_skill 工具 + skills/ 目录里的 markdown 文件
3. RAG 看起来高级但对于 <50 个技能来说是过度工程
4. 简历上可以写"按需加载减少 90% token 消耗"
```

---

## 六、s06 Context Compact — 上下文压缩

### 6.1 Token 计数

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. `len(text) / 4` 估算 | 零依赖 | 误差大（中文 1 字=1-2 token）|
| B. `tiktoken` | OpenAI 模型精确 | 不支持 Claude 的 tokenizer |
| C. `anthropic` SDK 的 count_tokens | Claude 精确 | 需要 API 调用 |
| D. `litellm.token_counter()` | 自动选择正确 tokenizer | 依赖 litellm |

**选择：D — `litellm.token_counter()`**

```
理由：
1. 因为全局选了 litellm，它自动根据模型选择正确的 tokenizer
2. 不需要关心"Claude 用什么 tokenizer"的问题
3. 本地计算，无 API 调用开销
```

### 6.2 压缩策略

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 滑动窗口（丢最旧的）| 最简单、确定性 | 丢失重要上下文 |
| B. LLM 摘要 | 保留语义 | 需要额外 API 调用、有成本 |
| C. 混合（旧的摘要 + 近的保留）| 平衡成本和质量 | 实现稍复杂 |

**选择：C — 混合策略**

```
理由：
1. 纯滑动窗口会丢失"30 轮前模型做了什么决策"
2. 全量 LLM 摘要太贵（每次 compact 消耗 token）
3. 混合策略：旧的用 LLM 压缩成摘要，近 5 轮保留原文
4. 压缩时用便宜模型（gpt-4o-mini），写代码时用贵模型（claude-opus）

简历表述："分层上下文压缩——远程摘要 + 近场保留，
          用廉价模型做压缩降低 70% 成本"
```

**伪代码**：

```python
# compact.py
from litellm import acompletion, token_counter

COMPACT_THRESHOLD = 0.8  # context window 的 80%
KEEP_RECENT = 6          # 保留最近 6 条消息

async def maybe_compact(
    messages: list[dict],
    model: str,
    context_window: int,
) -> list[dict]:
    total = token_counter(model=model, messages=messages)
    if total < context_window * COMPACT_THRESHOLD:
        return messages  # 还没到阈值

    # 分割
    system_msgs = [m for m in messages if m["role"] == "system"]
    recent = messages[-KEEP_RECENT:]
    old = messages[len(system_msgs):-KEEP_RECENT]

    # 用便宜模型做摘要
    summary_resp = await acompletion(
        model="gpt-4o-mini",  # 便宜！
        messages=[{
            "role": "user",
            "content": f"将以下对话压缩为简短摘要，保留：文件路径、决策、错误、待办项。\n\n{format_messages(old)}"
        }],
    )
    summary = summary_resp.choices[0].message.content

    return [
        *system_msgs,
        {"role": "user", "content": f"[Previous conversation summary]\n{summary}"},
        *recent,
    ]
```

---

## 七、s07 Permission System — 权限系统

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 硬编码黑名单 | 最简单 | 不灵活 |
| B. 配置文件规则 + glob 匹配 | 灵活、可扩展 | 需要解析规则 |
| C. OS 沙箱（Docker/subprocess）| 最安全 | 太重、跨平台难 |

**选择：B — 配置文件规则 + `fnmatch` 匹配**

```
理由：
1. 和 Claude Code 的思路一致（规则层 > OS 沙箱），适合 Python 生态
2. fnmatch 是标准库，glob 匹配零依赖
3. 配置文件规则可以按项目定制
4. 简历表述："多层规则引擎权限系统，支持 deny/allow/ask 三态决策"
```

**伪代码**：

```python
# permissions.py
from pydantic import BaseModel
from enum import Enum
import fnmatch

class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"

class PermissionRule(BaseModel):
    tool: str = "*"          # 工具名 glob
    path: str | None = None  # 路径 glob
    command: str | None = None  # 命令 glob
    decision: Decision

class PermissionEngine:
    def __init__(self, rules: list[PermissionRule]):
        # 排序：deny 优先 > ask > allow
        self.deny_rules = [r for r in rules if r.decision == Decision.DENY]
        self.allow_rules = [r for r in rules if r.decision == Decision.ALLOW]
        self.ask_rules = [r for r in rules if r.decision == Decision.ASK]

    def check(self, tool_name: str, input: dict) -> Decision:
        # 阶段 1: 黑名单
        for rule in self.deny_rules:
            if self._matches(rule, tool_name, input):
                return Decision.DENY

        # 阶段 2: 白名单
        for rule in self.allow_rules:
            if self._matches(rule, tool_name, input):
                return Decision.ALLOW

        # 阶段 3: 默认 ask
        return Decision.ASK

    def _matches(self, rule: PermissionRule, tool: str, input: dict) -> bool:
        if not fnmatch.fnmatch(tool, rule.tool):
            return False
        if rule.path and not fnmatch.fnmatch(input.get("file_path", ""), rule.path):
            return False
        if rule.command and not fnmatch.fnmatch(input.get("command", ""), rule.command):
            return False
        return True

# 配置文件 permissions.yaml:
# rules:
#   - tool: "bash"
#     command: "rm *"
#     decision: deny
#   - tool: "read_file"
#     decision: allow
#   - tool: "bash"
#     command: "git push*"
#     decision: ask
```

---

## 八、s08 Hook System — 钩子系统

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. Python 回调函数 | 类型安全、最快 | 不能跨语言 |
| B. `subprocess` 调用外部脚本 | 跨语言、用户可用任何语言写 | 慢、需要解析输出 |
| C. Python 插件 (`importlib`) | 灵活、可以修改内部状态 | 安全风险大 |

**选择：B — `subprocess` + JSON 协议**

```
理由：
1. 和 Claude Code 的方案一致（shell 脚本钩子）
2. 用户可以用 bash/python/node 任何语言写钩子
3. JSON stdin/stdout 协议比退出码更丰富（传结构化数据）
4. 进程隔离——钩子脚本崩溃不影响主进程
5. 简历表述："进程隔离的钩子系统，JSON 协议通信"
```

**伪代码**：

```python
# hooks.py
import subprocess, json

class HookRunner:
    def __init__(self, config: dict):
        # config 来自 hooks.yaml
        self.hooks = config  # {"pre_tool_use": [{"command": "..."}], ...}

    async def run(self, event: str, payload: dict) -> dict | None:
        for hook in self.hooks.get(event, []):
            result = await self._exec(hook["command"], payload)
            if result and result.get("decision") == "deny":
                return result  # 阻止
        return None

    async def _exec(self, command: str, payload: dict) -> dict | None:
        proc = await asyncio.create_subprocess_exec(
            *command.split(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(
            proc.communicate(json.dumps(payload).encode()),
            timeout=10,  # 10 秒超时
        )
        if stdout.strip():
            return json.loads(stdout)
        return None
```

---

## 九、s09 Memory System — 记忆系统

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. SQLite | 结构化查询 | 不可读、不好 diff |
| B. YAML 前置元数据 + Markdown 文件 | 可读、可 git 跟踪 | 需要解析 |
| C. JSON 文件 | 程序友好 | 人类不好读 |

**选择：B — YAML 前置元数据 + Markdown（和 learn-claude-code / Claude Code 一致）**

```
理由：
1. 人类可读——打开文件就能看到记忆内容
2. 可以 git 管理——记忆变更有版本历史
3. YAML 前置元数据提供结构化字段（name/type/description）
4. Python 用 python-frontmatter 库解析，3 行代码
5. 简历表述："基于文件系统的持久记忆，四类记忆分类，
              跨会话知识积累"

安装: pip install python-frontmatter
```

**伪代码**：

```python
# memory.py
import frontmatter
from pathlib import Path
from pydantic import BaseModel

class MemoryRecord(BaseModel):
    name: str
    description: str
    type: str  # user / feedback / project / reference
    content: str

MEMORY_DIR = Path(".memory")

def load_index() -> str:
    """加载 MEMORY.md 索引，注入 system prompt"""
    index_path = MEMORY_DIR / "MEMORY.md"
    if index_path.exists():
        text = index_path.read_text()
        # 截断到 200 行（和 Claude Code 一致）
        lines = text.splitlines()[:200]
        return "\n".join(lines)
    return ""

def save_memory(record: MemoryRecord):
    """保存一条记忆"""
    file_path = MEMORY_DIR / f"{record.name}.md"
    post = frontmatter.Post(
        content=record.content,
        name=record.name,
        description=record.description,
        type=record.type,
    )
    file_path.write_text(frontmatter.dumps(post))
    _update_index(record)

def _update_index(record: MemoryRecord):
    index_path = MEMORY_DIR / "MEMORY.md"
    line = f"- [{record.name}]({record.name}.md) — {record.description}\n"
    with open(index_path, "a") as f:
        f.write(line)
```

---

## 十、s10 System Prompt — 提示词管线

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 字符串拼接 `f""` | 最简单 | 不可维护、不可测试 |
| B. Jinja2 模板 | 强大、条件渲染 | 过度工程、模板语法学习成本 |
| C. Builder 模式（函数管线）| 可测试、可组合、清晰 | 代码稍多 |

**选择：C — Builder 模式**

```
理由：
1. 每个 section 是独立函数，可以单独测试
2. 顺序可控——稳定内容在前（利用 prompt cache），动态内容在后
### 静态部分 (每次都相同)
- 核心指令
- 工具列表  
- 技能元数据
- 记忆内容
- CLAUDE.md配置

### 动态部分 (每次都变化)
- 当前日期
- 工作目录
- 模型信息
- 平台信息

3. 后续加新 section（记忆、技能、运行时上下文）只需加一个函数
4. 简历表述："管线式 System Prompt 构建器，
              缓存感知的内容排序策略"
```

**伪代码**：

```python
# prompt.py
from dataclasses import dataclass, field

@dataclass
class PromptBuilder:
    sections: list[str] = field(default_factory=list)

    def add(self, content: str) -> "PromptBuilder":
        if content.strip():
            self.sections.append(content)
        return self

    def build(self) -> str:
        return "\n\n".join(self.sections)

def build_system_prompt(state) -> str:
    return (
        PromptBuilder()
        # 稳定内容在前（prompt cache 友好）
        .add(CORE_IDENTITY)
        .add(format_tools(state.tools))
        .add(format_skill_names(state.skills))
        # 半稳定内容
        .add(load_memory_index())
        .add(load_claude_md(state.cwd))
        # 动态内容在后
        .add(format_environment(state))
        .build()
    )
```

---

## 十一、s11 Error Recovery — 错误恢复

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 裸 `try/except` | 最简单 | 无退避、无预算 |
| B. `tenacity` 库 | 装饰器式重试、指数退避 | 对 LLM 场景不够定制 |
| C. 自定义重试 + 分类恢复 | 完全控制 | 代码多 |

**选择：C — 自定义重试，借鉴 `tenacity` 的退避算法**

```
理由：
1. LLM 的错误需要分类处理（context 溢出 vs 网络错误 vs max_tokens 截断）
2. tenacity 是通用重试库，不理解"压缩后重试"这种 LLM 特有逻辑
3. 自己写可以集成 compact(s06) 作为恢复策略
4. 保留 tenacity 的指数退避算法（不重复造轮子）
```

**伪代码**：

```python
# recovery.py
import asyncio, random

class RecoveryBudget:
    def __init__(self, max_retries: int = 3):
        self.max = max_retries
        self.attempts = {"continuation": 0, "compaction": 0, "backoff": 0}

    def can_retry(self, category: str) -> bool:
        return self.attempts[category] < self.max

    def record(self, category: str):
        self.attempts[category] += 1

async def resilient_llm_call(messages, tools, model, budget: RecoveryBudget):
    while True:
        try:
            return await acompletion(model=model, messages=messages, tools=tools, stream=True)

        except Exception as e:
            error_type = classify_error(e)

            if error_type == "max_tokens" and budget.can_retry("continuation"):
                budget.record("continuation")
                messages.append({"role": "user", "content": "[请继续，你的回复被截断了]"})
                continue

            elif error_type == "context_overflow" and budget.can_retry("compaction"):
                budget.record("compaction")
                messages = await maybe_compact(messages, model, force=True)
                continue

            elif error_type == "rate_limit" and budget.can_retry("backoff"):
                budget.record("backoff")
                delay = min(2 ** budget.attempts["backoff"] + random.random(), 60)
                await asyncio.sleep(delay)
                continue

            raise  # 预算用尽，抛出
```

---

## 十二、s12 Task System — 任务系统

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 内存 dict | 最快 | compact 后丢失 |
| B. JSON 文件 + DAG | 持久、可调试 | I/O |
| C. SQLite + 图查询 | 高效查询依赖关系 | 过重 |

**选择：B — JSON 文件 + 简单 DAG（和 learn-claude-code / Claude Code 一致）**

```
理由：
1. 任务文件活过 compact、重启、子代理切换
2. 人类可读——直接打开 .tasks/task-001.json 看状态
3. DAG 用 blocked_by 字段表示，上游完成时遍历解锁下游
4. 任务数量通常 < 50，不需要数据库

简历表述："文件持久化的 DAG 任务编排引擎，
          支持依赖解锁和跨压缩状态恢复"
```

---

## 十三、s13 Background Tasks — 后台任务

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. `threading.Thread` | 简单、GIL 下仍可 I/O 并发 | 和 asyncio 混用复杂 |
| B. `asyncio.create_task` | 和主循环同一事件循环 | 需要 async 全链路 |
| C. `multiprocessing` | 真并行 | 通信复杂 |

**选择：B — `asyncio.create_task` + `asyncio.Queue` 通知**

```
理由：
1. 全项目已经是 asyncio，没必要引入 threading
2. Queue 是 asyncio 原生的线程安全通知机制
3. create_task 创建的协程和主循环共享事件循环，通信零开销
4. 和 s04 子代理方案一致——后台任务本质就是后台运行的子代理
```

---

## 十四、s14 Cron Scheduler — 定时调度

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. `APScheduler` | 功能全面、生产级 | 重依赖 |
| B. `croniter` + asyncio 循环 | 轻量、只做 cron 解析 | 需要自己写调度循环 |
| C. 系统 crontab | 零代码 | 无法集成到 agent 内部 |

**选择：B — `croniter` + asyncio 后台循环**

```
理由：
1. croniter 只做一件事：解析 cron 表达式并计算下次触发时间
2. 调度循环用 asyncio.create_task，和 s13 统一
3. APScheduler 太重了——我们只需要 cron 解析 + 定时检查
4. 系统 crontab 无法和 agent 内部状态交互

安装: pip install croniter
```

**伪代码**：

```python
# cron.py
from croniter import croniter
from datetime import datetime

class CronScheduler:
    def __init__(self, notification_queue: asyncio.Queue):
        self.schedules: list[dict] = []
        self.queue = notification_queue

    def add(self, cron_expr: str, prompt: str):
        self.schedules.append({
            "cron": cron_expr,
            "prompt": prompt,
            "last_fired": None,
        })

    async def run_forever(self):
        while True:
            now = datetime.now()
            for s in self.schedules:
                cron = croniter(s["cron"], now)
                prev = cron.get_prev(datetime)
                if s["last_fired"] is None or prev > s["last_fired"]:
                    s["last_fired"] = now
                    await self.queue.put(s["prompt"])
            await asyncio.sleep(60)
```

---

## 十五、s15-s17 Agent Teams — 多代理协作

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. Redis pub/sub | 跨进程、持久化 | 需要 Redis 服务 |
| B. `asyncio.Queue` + JSONL 文件 | 轻量、进程内够用 | 不能跨机器 |
| C. ZeroMQ | 高性能、多模式 | 学习成本高 |

**选择：B — `asyncio.Queue` 运行时 + JSONL 文件持久化**

```
理由：
1. 单机 agent 不需要 Redis/ZMQ 这种分布式方案
2. asyncio.Queue 做运行时通信，JSONL 做持久化回看
3. 和 learn-claude-code 的 MessageBus 设计一致
4. JSONL 一行一消息，append-only，并发安全

简历表述："基于消息总线的多代理协作框架，
          支持请求-响应协议、优雅关闭、自主任务认领"
```

---

## 十六、s18 Worktree Isolation — 工作区隔离

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. Git worktree | 轻量、git 原生、方便合并 | 依赖 git |
| B. Docker 容器 | 完全隔离 | 重量级、启动慢 |
| C. 临时目录拷贝 | 最简单 | 浪费磁盘、不好合并 |

**选择：A — Git worktree**

```
理由：
1. coding agent 的工作目录几乎一定是 git 仓库
2. worktree 创建/销毁只要 1 秒（比 Docker 快 10 倍）
3. 独立分支 → 合并用 git merge，工具链成熟
4. 和 Claude Code 的方案一致
```

---

## 十七、s19 MCP — 外部工具协议

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. 自己实现 MCP 协议 | 完全控制 | 协议复杂、工作量大 |
| B. `mcp` Python SDK | 官方实现、标准兼容 | 需要学习 SDK |
| C. 不做 MCP，只用 CLI | 最简单 | 失去结构化工具发现 |

**选择：B — `mcp` 官方 Python SDK**

```
理由：
1. Anthropic 维护的官方 SDK，协议兼容性有保障
2. 自己实现 MCP 协议是大量重复工作（JSON-RPC, stdio, SSE 传输层）
3. 简历上写"MCP 协议集成"比"调 CLI 命令"有技术深度
4. 生态价值——已有大量 MCP 服务器可以直接连

安装: pip install mcp
```

---

## 十八、完整依赖清单

```toml
# pyproject.toml
[project]
name = "my-coding-agent"
requires-python = ">=3.11"
dependencies = [
    # 核心
    "litellm>=1.40",          # LLM 统一接口
    "pydantic>=2.0",          # 数据模型 + 验证
    "typer>=0.12",            # CLI 框架
    "rich>=13.0",             # 终端 UI (彩色输出/进度条/表格)

    # 功能模块
    "python-frontmatter>=1.0", # s09: 记忆文件解析
    "croniter>=1.4",           # s14: cron 表达式解析
    "mcp>=1.0",                # s19: MCP 协议 SDK
    "pyyaml>=6.0",             # 配置文件解析

    # 可选
    "prompt-toolkit>=3.0",     # 交互式输入 (自动补全/历史)
]
```

```
总依赖: 8 个核心 + 1 个可选
无 Redis、无 Docker SDK、无数据库驱动
→ pip install . 五秒搞定
```

---

## 十九、简历项目描述建议

```
项目名称: Python Coding Agent Framework

一句话: 从零实现的生产级 AI 编程助手框架，覆盖 Agent 核心循环、
       工具系统、权限引擎、上下文管理、多代理协作等 19 个子系统。

技术栈:
  Python 3.11+ / asyncio / Pydantic v2 / litellm / MCP

亮点:
  • 基于 asyncio 的流式 Agent 循环，支持实时输出和中断
  • Pydantic 驱动的类型安全工具系统，自动生成 JSON Schema
  • 多层规则引擎权限系统（deny/allow/ask），fnmatch 模式匹配
  • 分层上下文压缩（远程摘要 + 近场保留），廉价模型做压缩降低 70% 成本
  • 基于 asyncio 协程的轻量子代理，上下文隔离 + 共享工具注册表
  • 文件持久化 DAG 任务编排，跨压缩/重启状态恢复
  • 进程隔离钩子系统，JSON 协议通信
  • 四类跨会话记忆 + 管线式 System Prompt 构建器
  • Git worktree 多代理文件隔离
  • MCP 协议集成，支持外部工具服务发现与调用

参考学习:
  Anthropic Claude Code / OpenAI Codex CLI / Pi-Mono
```

---

## 二十、实现优先级（推荐顺序）

```
第一周（能跑起来）:
  ✅ s01 Agent Loop       — 核心循环
  ✅ s02 Tool Use          — 4 个基础工具
  ✅ s07 Permission        — 基础权限
  ✅ s11 Error Recovery    — 基础重试

第二周（能用起来）:
  ✅ s06 Context Compact   — 上下文压缩
  ✅ s03 TodoWrite         — 会话规划
  ✅ s10 System Prompt     — 提示词管线
  ✅ s09 Memory            — 跨会话记忆

第三周（能秀出来）:
  ✅ s04 Subagent          — 子代理
  ✅ s05 Skill Loading     — 技能加载
  ✅ s08 Hook System       — 钩子系统
  ✅ s12 Task System       — 任务 DAG

第四周（进阶）:
  ✅ s13 Background Tasks  — 后台执行
  ✅ s14 Cron              — 定时调度
  ✅ s18 Worktree          — Git 隔离
  ✅ s19 MCP               — 外部工具

第五周（如果有时间）:
  ✅ s15-s17 Teams         — 多代理协作
```

每完成一周就可以写进简历——第一周就已经是一个能工作的 Agent 了。

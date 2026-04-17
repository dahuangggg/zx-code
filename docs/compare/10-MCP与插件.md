# 10 — MCP 与插件系统

> 对应 learn-claude-code: **s19 MCP & Plugin**

---

## 一、问题引入

Agent 的内置工具是有限的。当你需要访问数据库、调用 API、操作 CI/CD 时怎么办？

learn-claude-code 教了两个机制：
- **MCP（Model Context Protocol）**：连接外部工具服务的标准协议
- **Plugin**：打包好的功能扩展

```python
# learn-claude-code s19 的核心思想

# 1. MCP 服务器提供工具
class MCPServer:
    def list_tools(self) -> list:
        return [
            {"name": "query_db", "description": "查询数据库"},
            {"name": "deploy", "description": "部署到生产环境"},
        ]
    
    def call_tool(self, name, arguments) -> str:
        if name == "query_db":
            return db.execute(arguments["sql"])
        # ...

# 2. Agent 通过统一路由调用
def dispatch_tool(name, arguments):
    if name in NATIVE_TOOLS:
        return NATIVE_TOOLS[name](**arguments)
    
    # 工具名格式: mcp__{server}__{tool}
    if name.startswith("mcp__"):
        server, tool = parse_mcp_name(name)
        return mcp_clients[server].call_tool(tool, arguments)
    
    raise UnknownTool(name)
```

---

## 二、MCP 在三个仓库中的实现

### 2.1 Codex — MCP 客户端 + 工具代理

```rust
// codex-rs/mcp-server/ + codex-rs/codex-mcp/

// Codex 同时是 MCP 客户端和 MCP 服务器：

// 作为客户端（连接外部 MCP 服务器）:
// 1. 从配置文件加载 MCP 服务器列表
// 2. 启动时连接每个服务器
// 3. 枚举它们的工具列表
// 4. 工具名变为 mcp__{server}__{tool}
// 5. 调用时路由到对应服务器

// 配置:
// ~/.codex/config.toml
// [mcp]
// enable_tools = true
// 
// [[mcp.servers]]
// name = "github"
// command = "mcp-server-github"
// args = ["--token", "xxx"]

// 作为服务器（把 Codex 能力暴露给其他 MCP 客户端）:
// → 其他工具可以通过 MCP 协议调用 Codex
// → Codex 变成了一个"编码能力供应商"

// 认证支持:
// → OAuth 流程集成
// → API key 管理
// → 自动刷新 token
```

### 2.2 Pi-Mono — 不支持 MCP

```typescript
// Pi-Mono 明确不支持 MCP

// 设计哲学（来自 AGENTS.md）:
// "Build CLI tools with READMEs as skills instead of MCP"
// "构建 CLI 工具 + README 作为技能，而不是用 MCP"

// Pi-Mono 的替代方案:
// 1. 用 bash 工具调用任何 CLI
//    → agent 可以运行 `aws`, `docker`, `kubectl` 等命令
//    → 不需要 MCP 协议包装

// 2. 用 Extension API 注册自定义工具
//    → 比 MCP 更直接、更灵活
//    → 不需要独立的服务器进程

// 3. 用 Skills 注入 CLI 使用说明
//    → 教模型怎么用 `aws s3 cp` 而不是封装成 MCP 工具

// Pi-Mono 的观点:
// MCP 增加了复杂度（需要启动/管理服务器进程）
// 大多数外部能力可以通过 CLI 命令获得
// Extension API 比 MCP 更适合 TypeScript 生态
```

### 2.3 Claude Code — MCP 生态最完整

```typescript
// src/services/mcp/ — Claude Code 的 MCP 实现最完整

// 架构:
// ┌─────────────────────────────────────────┐
// │ Claude Code                              │
// │  ┌───────────────────────────────────┐  │
// │  │ MCPConnectionManager              │  │
// │  │  ├── client.ts (119KB!)           │  │
// │  │  ├── config.ts (配置加载)         │  │
// │  │  └── auth.ts (89KB, OAuth)        │  │
// │  └───────────────────────────────────┘  │
// │         ↕ MCP 协议                       │
// │  ┌──────────┐ ┌──────────┐ ┌──────────┐│
// │  │ GitHub   │ │ Slack    │ │ Database ││
// │  │ MCP      │ │ MCP      │ │ MCP      ││
// │  │ Server   │ │ Server   │ │ Server   ││
// │  └──────────┘ └──────────┘ └──────────┘│
// └─────────────────────────────────────────┘

// MCP 工具在 Claude Code 中的生命周期:

// 1. 配置 MCP 服务器
// ~/.claude/settings.json 或 .claude/settings.json:
// {
//     "mcpServers": {
//         "github": {
//             "command": "mcp-server-github",
//             "args": ["--token", "xxx"]
//         },
//         "postgres": {
//             "command": "mcp-server-postgres",
//             "env": { "DATABASE_URL": "..." }
//         }
//     }
// }

// 2. 启动时连接
async function initMCP(config: MCPConfig) {
    for (const [name, server] of Object.entries(config.mcpServers)) {
        // 启动服务器进程
        const client = await MCPClient.connect(server);
        
        // 枚举工具
        const tools = await client.listTools();
        
        // 注册到工具系统
        for (const tool of tools) {
            registerTool({
                name: `mcp__${name}__${tool.name}`,
                description: tool.description,
                inputSchema: tool.inputSchema,
                execute: (input) => client.callTool(tool.name, input),
            });
        }
        
        // 枚举资源（可选）
        const resources = await client.listResources();
    }
}

// 3. 工具调用时路由
// 模型生成 tool_use: { name: "mcp__github__create_issue", ... }
// → MCPTool 解析服务器名和工具名
// → 路由到对应的 MCP 客户端
// → 返回结果

// 4. OAuth 认证（Claude Code 独有）
// 某些 MCP 服务器需要 OAuth 认证
// Claude Code 内置了完整的 OAuth 流程:
// - 浏览器跳转授权
// - Token 存储和刷新
// - 支持 claude.ai 的 MCP 服务器

// Claude Code 特有功能:
// - MCP 官方注册表（内置服务器列表）
// - Claude.ai MCP（技能/提示词集成）
// - 项目级 MCP 配置（不同项目用不同服务器）
// - 权限控制（MCP 工具也走权限检查流程）
```

---

## 三、Plugin 系统对比

### 3.1 Codex — 无独立插件系统

```
Codex 没有"插件"概念。扩展方式:
1. Skills 目录 → 注入知识
2. MCP 服务器 → 添加工具
3. ExecPolicy → 自定义规则
4. 配置文件 → 调整行为

没有 npm 包或独立的插件安装机制。
```

### 3.2 Pi-Mono — Pi Packages（最灵活的插件系统）

```typescript
// Pi-Mono 有完整的包管理系统:

// 安装方式:
// pi install npm:@foo/pi-deploy
// pi install git:github.com/user/pi-tools

// 包可以提供:
// ├── extensions/   → 扩展（工具、命令、快捷键、UI）
// ├── skills/       → 技能（领域知识）
// ├── prompts/      → 提示词模板
// └── themes/       → 主题（UI 样式）

// 包清单 (pi-manifest.json):
{
    "name": "@foo/pi-deploy",
    "version": "1.0.0",
    "pi": {
        "extensions": ["./extensions/deploy.ts"],
        "skills": ["./skills/kubernetes.md"],
        "prompts": ["./prompts/deploy-checklist.md"],
        "themes": ["./themes/dark.json"]
    }
}

// 加载优先级:
// 1. 项目目录 .pi/
// 2. 全局目录 ~/.pi/agent/
// 3. 已安装的 Pi Packages
// → 项目级 > 全局 > 包

// 和 npm 生态的集成:
// - 用 npm/pnpm 管理依赖
// - 可以引用 npm 包中的代码
// - 支持 TypeScript 运行时编译 (JITI)
```

### 3.3 Claude Code — Plugins + Skills + MCP 三层

```
Claude Code 有三种扩展机制，各有分工:

Skills（最轻量）:
  └── 给模型注入知识
  └── 不执行代码
  └── 通过 SkillTool 在子代理中使用
  └── 例: commit, review-pr, pdf

Plugins（中等）:
  └── 注册自定义工具、命令、组件
  └── 从 ~/.claude/plugins/ 或 npm 加载
  └── 有版本管理和缓存
  └── 执行 TypeScript/JavaScript 代码

MCP（最重）:
  └── 连接外部服务器进程
  └── 标准协议（跨语言、跨平台）
  └── 有认证（OAuth）
  └── 适合数据库、API 网关等重型集成

选择指南:
  "教模型怎么做" → Skill
  "给 Agent 加功能" → Plugin
  "连接外部系统" → MCP
```

---

## 四、MCP 工具的统一路由

learn-claude-code 教的核心模式：原生工具和外部工具走同一个路由：

```python
# learn-claude-code s19 的统一路由
def dispatch(name, arguments):
    # 原生工具
    if name in native_tools:
        return native_tools[name](arguments)
    
    # MCP 工具 (mcp__{server}__{tool})
    if name.startswith("mcp__"):
        parts = name.split("__")
        server = parts[1]
        tool = parts[2]
        return mcp_clients[server].call(tool, arguments)
    
    raise UnknownTool(name)
```

### 三个仓库的实现

```
Codex:
  ToolRegistry::resolve(name)
    ├── tools.get(name)          # 内置
    ├── mcp_tools.find(name)     # MCP
    └── skill_tools.find(name)   # Skills
  → 分层查找，内置优先

Pi-Mono:
  // 所有工具扁平注册，name 唯一
  tools.get(name)  
  → 不区分来源，先注册的先用
  → 扩展注册的工具和内置工具平等

Claude Code:
  // 类似 Codex 的分层
  if (isNativeTool(name)) → 内置工具
  if (isMCPTool(name))    → MCPTool 代理
  if (isSkillTool(name))  → SkillTool 子代理
  if (isPluginTool(name)) → Plugin 工具
  → 分层路由，权限检查对所有工具一视同仁
```

**关键洞察**：三个仓库都保证 **MCP 工具和内置工具走同样的权限检查**。不能因为是外部工具就绕过安全控制。

---

## 五、关键差异总结

| 维度 | Codex | Pi-Mono | Claude Code |
|------|-------|---------|-------------|
| **MCP 支持** | 客户端 + 服务器 | 不支持 | 客户端（最完整）|
| **MCP 认证** | API key + OAuth | N/A | OAuth 全流程 |
| **插件系统** | 无 | Pi Packages | Plugins |
| **包管理** | 无 | npm/git | npm |
| **工具来源** | 内置 + MCP + Skills | 内置 + Extension | 内置 + MCP + Skills + Plugins |
| **路由方式** | 分层查找 | 扁平注册 | 分层路由 |
| **权限统一** | 是 | 是（通过钩子）| 是 |

---

## 六、Pi-Mono "不用 MCP" 的思考

Pi-Mono 的选择值得深入讨论：

```
Pi-Mono 的论点:
  "大多数外部能力可以通过 CLI 实现"

  MCP 方案:
    1. 写一个 MCP 服务器（几百行代码）
    2. 配置连接（服务器地址、认证）
    3. 维护服务器进程
    4. 通过 MCP 协议调用

  CLI + Skill 方案:
    1. 安装 CLI 工具（aws, kubectl, docker...）
    2. 写一个 Skill 教模型怎么用
    3. 模型通过 bash 工具调用 CLI

  后者更简单，且利用了已有的 CLI 生态。

  但 MCP 的优势:
    - 结构化输入/输出（不需要解析命令行输出）
    - 标准化认证（OAuth 统一管理）
    - 工具发现（自动枚举可用操作）
    - 跨语言（MCP 服务器可以用任何语言写）
```

---

## 七、给你的思考题

1. **什么场景下 MCP 比 CLI 更合适？**
   - 提示：当输出是结构化数据（JSON）而不是文本时
   - 当需要复杂认证（OAuth、API key 轮换）时
   - 当 CLI 不存在或不方便安装时

2. **为什么 Claude Code 的 MCP 客户端代码有 119KB？**
   - 提示：OAuth 认证（89KB），加上工具发现、连接管理、错误处理、资源订阅
   - MCP 协议的完整实现是很复杂的

3. **如果你要设计一个 Agent，你会先支持 MCP 还是先支持 CLI？**
   - CLI 更简单，覆盖面更广（几乎所有工具都有 CLI）
   - MCP 更标准化，适合企业级集成

# 可复现演示 / Reproducible demo

## 无 API Key 的离线演示

```bash
uv sync --locked --extra dev
uv run python examples/offline_demo.py --output .zx-code/artifacts/offline-demo
```

输出目录必须尚不存在，避免覆盖已有演示。再次运行时换一个目录。
演示用固定模型响应驱动真实 Agent 主循环、文件工具、权限判断、SessionStore 和 trace。
它会读取临时示例文件、将 `helo` 改为 `hello`、拒绝 shell 调用，随后重新创建
SessionStore 并验证历史消息恢复。每一步都有断言；失败时进程以非零状态退出。

生成文件：`greeting.py`、`change.diff`、`summary.json`、`sessions/*.jsonl`、
`traces/*.jsonl` 和可在浏览器打开的 `traces/*.html`。

**此演示验证运行时链路，不证明真实模型的自主解题能力、生产安全性或用户采用情况。**
编辑结果包含 diff；项目尚未提供通用的一键 undo 或操作系统级沙箱。

## 真实模型演示脚本

在不含敏感信息的测试仓库里配置你有权使用的模型和 API Key，执行：

```bash
uv run zx-code --session-id greeting-demo --trace --trace-html --max-turns 8 \
  "读取示例代码，修复一个拼写错误，展示修改，并运行对应测试"
uv run zx-code --resume greeting-demo "说明刚才修改了什么"
```

这会实际调用配置的模型并产生相应费用。录屏应展示：任务、实际工具结果、代码 diff、
测试结果和恢复的会话。发布前检查录像和 trace 中的文件内容、路径及凭据。
不要把上述脚本当成已完成的真实模型评测。

## English

Run the offline command above to exercise real tools and persisted sessions with a scripted model.
No API key is needed. Inspect the generated diff, JSON summary and HTML trace. Fixed model
responses make this a runtime demonstration, not an autonomous coding benchmark.

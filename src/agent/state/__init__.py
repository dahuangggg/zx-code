"""agent.state — 跨会话持久化状态存储。

模块说明：
  memory.py        — ``MemoryStore``：读写 YAML frontmatter + Markdown 记忆文件，注入 system prompt
  memory_search.py — ``HybridMemorySearch``：TF-IDF + 向量 + 融合 + 时间衰减 + MMR 五阶段搜索管道
  sessions.py      — ``SessionStore``：将消息历史持久化为 JSONL 文件，支持跨重启恢复
  skills.py        — ``SkillStore``：两层技能加载（索引在 prompt，正文按需通过 load_skill 工具获取）
  tasks.py         — ``TaskStore``：基于文件的 DAG 任务编排，支持 blocked_by 依赖和跨压缩状态恢复
  todo.py          — ``TodoManager``：会话内 todo 列表（JSON 文件持久化），注入 system prompt 底部
"""

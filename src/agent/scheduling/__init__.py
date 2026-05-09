"""agent.scheduling — 时间驱动与后台并发执行。

模块说明：
  activity.py   — ``ActivityTracker``：追踪每个会话的活跃状态，供心跳检测"用户是否空闲"
  background.py — ``BackgroundTaskManager``：asyncio.create_task 封装，带 Queue 结果通知
  cron.py       — ``CronScheduler``：三种调度模式（at / every / cron 表达式），依赖 croniter
  heartbeat.py  — ``HeartbeatRunner``：周期性运行 agent 并将非 sentinel 回复推送给用户
  lanes.py      — ``LaneScheduler``：优先级队列调度器，防止子代理/cron 抢占主对话
"""

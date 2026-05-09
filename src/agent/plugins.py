"""plugins — 外部插件工具系统。

插件通过目录中的 ``plugin.json`` 清单文件声明工具，
每个工具的执行方式为：以 arguments JSON 写入 stdin，从 stdout 读取结果。

插件目录发现逻辑（``PluginManager``）：
  - 扫描所有 ``plugin_roots`` 下的 ``*/plugin.json`` 文件
  - 每个清单可声明多个工具（``tools`` 数组）

工具命名规则：``plugin__<plugin_name>__<tool_name>``
（非字母数字字符替换为下划线，确保工具名合法）

使用场景：
  - 不改动框架源码，通过外部脚本扩展 agent 的能力
  - 可用任何语言实现插件（bash、python、go 等）
"""

from __future__ import annotations


import asyncio
import json
import shlex
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.models import ToolResult
from agent.tools.base import Tool


class PluginToolConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    command: str
    input_schema: dict[str, Any] = Field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    timeout_s: float = 30.0


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    tools: list[PluginToolConfig] = Field(default_factory=list)


class PluginCommandTool(Tool):
    name: str
    description: str
    input_model = object  # type: ignore[assignment]

    def __init__(
        self,
        *,
        plugin_name: str,
        config: PluginToolConfig,
        plugin_dir: Path,
    ) -> None:
        self.plugin_name = plugin_name
        self.tool_name = config.name
        self.name = f"plugin__{_safe_part(plugin_name)}__{_safe_part(config.name)}"
        self.description = config.description or f"Plugin tool {plugin_name}.{config.name}"
        self.command = config.command
        self.input_schema = config.input_schema
        self.timeout_s = config.timeout_s
        self.plugin_dir = plugin_dir

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    async def execute(self, arguments: dict[str, Any], call_id: str) -> ToolResult:
        process = await asyncio.create_subprocess_exec(
            *shlex.split(self.command),
            cwd=self.plugin_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(json.dumps(arguments).encode("utf-8")),
                timeout=self.timeout_s,
            )
        except TimeoutError as exc:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
            raise TimeoutError(
                f"plugin command timed out after {self.timeout_s:.1f}s: {self.name}"
            ) from exc
        if process.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", errors="replace").strip())

        content = stdout.decode("utf-8", errors="replace").strip()
        return ToolResult(call_id=call_id, name=self.name, content=content)

    async def run(self, arguments: Any) -> str | dict[str, Any]:
        raise NotImplementedError("PluginCommandTool overrides execute")


class PluginManager:
    def __init__(self, plugin_roots: list[str | Path]) -> None:
        self.plugin_roots = [Path(root).expanduser().resolve() for root in plugin_roots]

    def load_tools(self) -> list[PluginCommandTool]:
        tools: list[PluginCommandTool] = []
        for plugin_dir, manifest in self.discover():
            for tool_config in manifest.tools:
                tools.append(
                    PluginCommandTool(
                        plugin_name=manifest.name,
                        config=tool_config,
                        plugin_dir=plugin_dir,
                    )
                )
        return tools

    def discover(self) -> list[tuple[Path, PluginManifest]]:
        manifests: list[tuple[Path, PluginManifest]] = []
        for root in self.plugin_roots:
            if not root.exists():
                continue
            for manifest_path in sorted(root.glob("*/plugin.json")):
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifests.append(
                    (
                        manifest_path.parent,
                        PluginManifest.model_validate(raw),
                    )
                )
        return manifests


def _safe_part(value: str) -> str:
    safe = []
    for char in value:
        if char.isalnum() or char == "_":
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe).strip("_") or "plugin"

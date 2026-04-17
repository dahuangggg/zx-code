from agent.tools.bash import BashTool
from agent.tools.edit_file import EditFileTool
from agent.tools.grep import GrepTool
from agent.tools.read_file import ReadFileTool
from agent.tools.registry import ToolRegistry
from agent.tools.write_file import WriteFileTool


def build_default_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(BashTool())
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(GrepTool())
    return registry


__all__ = ["ToolRegistry", "build_default_registry"]


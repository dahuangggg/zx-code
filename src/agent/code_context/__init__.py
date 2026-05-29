"""CodeContext 公共门面导出。

本包将 ``CodeContextIndexer`` 作为稳定入口暴露出去，内部扫描、快照、索引和搜索服务
仍可独立替换。
"""

from agent.code_context.indexer import CodeContextIndexer

__all__ = ["CodeContextIndexer"]

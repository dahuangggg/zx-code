"""CodeContext 的 Chroma 持久化适配层。

ChromaCodeStore 只负责向量库读写和 metadata 转换。增量判断、文件扫描、chunk id
生成、稀疏索引和结果融合都在 indexer/ranker 中完成。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

from agent.code_context.models import CodeChunk, CodeSearchResult


class ChromaCodeStore:
    """以 codebase_id 隔离多代码库的 Chroma collection。"""

    def __init__(
        self,
        *,
        path: str | Path,
        collection_name: str = "agent_code_context",
        embedding_function: Any | None = None,
    ) -> None:
        self.path = Path(path).expanduser()
        self.collection_name = collection_name
        self.embedding_function = _wrap_embedding_function(embedding_function)
        self._client = chromadb.PersistentClient(path=str(self.path))
        kwargs: dict[str, Any] = {"name": self.collection_name}
        if self.embedding_function is not None:
            kwargs["embedding_function"] = self.embedding_function
        self._collection = self._client.get_or_create_collection(**kwargs)

    def upsert_chunks(self, chunks: list[CodeChunk]) -> None:
        """把 chunk 内容和 metadata 写入 Chroma。"""
        if not chunks:
            return
        self._collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            metadatas=[_metadata(chunk) for chunk in chunks],
        )

    def delete_ids(self, ids: list[str]) -> int:
        """按 chunk id 删除向量，返回请求删除的有效 id 数量。"""
        safe_ids = [item for item in ids if item]
        if not safe_ids:
            return 0
        self._collection.delete(ids=safe_ids)
        return len(safe_ids)

    def delete_codebase(self, codebase_id: str) -> int:
        """删除某个代码库所有向量。"""
        ids = self.ids_for_codebase(codebase_id)
        return self.delete_ids(ids)

    def ids_for_codebase(self, codebase_id: str) -> list[str]:
        """查询某个代码库在 collection 中的所有 chunk id。"""
        result = self._collection.get(where={"codebase_id": codebase_id})
        return list(result.get("ids", []))

    def documents_for_codebase(self, codebase_id: str) -> list[CodeSearchResult]:
        """导出某个代码库的所有文档，供 SparseKeywordIndex 重建。"""
        result = self._collection.get(
            where={"codebase_id": codebase_id},
            include=["documents", "metadatas"],
        )
        documents = result.get("documents") or []
        metadatas = result.get("metadatas") or []
        output: list[CodeSearchResult] = []
        for document, metadata in zip(documents, metadatas, strict=False):
            if metadata is None:
                continue
            output.append(_to_search_result(document, metadata, score=0.0))
        return output

    def search(
        self,
        *,
        codebase_id: str,
        query: str,
        top_k: int,
    ) -> list[CodeSearchResult]:
        """执行向量检索，并把 Chroma distance 转成越大越好的 score。"""
        if top_k <= 0:
            return []
        result = self._collection.query(
            query_texts=[query],
            n_results=top_k,
            where={"codebase_id": codebase_id},
            include=["documents", "metadatas", "distances"],
        )
        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        results: list[CodeSearchResult] = []
        for document, metadata, distance in zip(documents, metadatas, distances, strict=False):
            if metadata is None:
                continue
            results.append(_to_search_result(document, metadata, score=round(1.0 / (1.0 + float(distance or 0.0)), 4)))
        return results


def _metadata(chunk: CodeChunk) -> dict[str, str | int]:
    """把 CodeChunk 转成 Chroma metadata 支持的标量 dict。"""
    return {
        "codebase_id": chunk.codebase_id,
        "codebase_path": chunk.codebase_path,
        "relative_path": chunk.relative_path,
        "language": chunk.language,
        "start_line": chunk.start_line,
        "end_line": chunk.end_line,
        "file_hash": chunk.file_hash,
        "chunk_index": chunk.chunk_index,
        "symbol": chunk.symbol,
    }


def _to_search_result(document: Any, metadata: dict[str, Any], *, score: float) -> CodeSearchResult:
    """把 Chroma document/metadata 转回内部搜索结果模型。"""
    return CodeSearchResult(
        relative_path=str(metadata.get("relative_path", "")),
        start_line=int(metadata.get("start_line", 1)),
        end_line=int(metadata.get("end_line", 1)),
        language=str(metadata.get("language", "text")),
        score=score,
        content=str(document or ""),
    )


def _wrap_embedding_function(embedding_function: Any | None) -> Any | None:
    """兼容不同版本 Chroma 对 embedding function 接口的要求。"""
    if embedding_function is None:
        return None
    if hasattr(embedding_function, "name"):
        return embedding_function

    class _CompatibleEmbeddingFunction:
        """把简单 callable 包装成 Chroma 期望的 embedding function 形状。"""

        def __call__(self, input):
            return embedding_function(input)

        def embed_documents(self, input):
            return embedding_function(input)

        def embed_query(self, input):
            return embedding_function(input)

        @staticmethod
        def name() -> str:
            return "default"

    return _CompatibleEmbeddingFunction()

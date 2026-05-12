from __future__ import annotations

from pathlib import Path
from typing import Any

import chromadb

from agent.code_context.models import CodeChunk, CodeSearchResult


class ChromaCodeStore:
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
        if not chunks:
            return
        self._collection.upsert(
            ids=[chunk.id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            metadatas=[_metadata(chunk) for chunk in chunks],
        )

    def delete_ids(self, ids: list[str]) -> int:
        safe_ids = [item for item in ids if item]
        if not safe_ids:
            return 0
        self._collection.delete(ids=safe_ids)
        return len(safe_ids)

    def delete_codebase(self, codebase_id: str) -> int:
        ids = self.ids_for_codebase(codebase_id)
        return self.delete_ids(ids)

    def ids_for_codebase(self, codebase_id: str) -> list[str]:
        result = self._collection.get(where={"codebase_id": codebase_id})
        return list(result.get("ids", []))

    def documents_for_codebase(self, codebase_id: str) -> list[CodeSearchResult]:
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
    return CodeSearchResult(
        relative_path=str(metadata.get("relative_path", "")),
        start_line=int(metadata.get("start_line", 1)),
        end_line=int(metadata.get("end_line", 1)),
        language=str(metadata.get("language", "text")),
        score=score,
        content=str(document or ""),
    )


def _wrap_embedding_function(embedding_function: Any | None) -> Any | None:
    if embedding_function is None:
        return None
    if hasattr(embedding_function, "name"):
        return embedding_function

    class _CompatibleEmbeddingFunction:
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

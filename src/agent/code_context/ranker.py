"""CodeContext 搜索排序与融合。

keyword_search() 提供内存 BM25 风格检索；rrf_fuse() 用 Reciprocal Rank Fusion
融合向量结果和关键词结果，避免直接比较两种不同分数尺度。
"""
from __future__ import annotations

import math

from agent.code_context.models import CodeSearchResult
from agent.code_context.tokenization import tokenize_code_text


def keyword_search(
    query: str,
    documents: list[CodeSearchResult],
    *,
    limit: int,
) -> list[CodeSearchResult]:
    """对传入文档集合执行关键词检索，返回带 keyword_rank 的结果。"""
    query_terms = _tokenize(query)
    if not query_terms or not documents:
        return []

    doc_terms = [_tokenize(document.content) for document in documents]
    doc_count = len(documents)
    df: dict[str, int] = {}
    for terms in doc_terms:
        for term in set(terms):
            df[term] = df.get(term, 0) + 1

    avg_len = sum(len(terms) for terms in doc_terms) / max(doc_count, 1)
    scored: list[tuple[float, CodeSearchResult]] = []
    for document, terms in zip(documents, doc_terms, strict=False):
        if not terms:
            continue
        frequencies: dict[str, int] = {}
        for term in terms:
            frequencies[term] = frequencies.get(term, 0) + 1
        score = 0.0
        for term in query_terms:
            tf = frequencies.get(term, 0)
            if tf == 0:
                continue
            idf = math.log(1 + (doc_count - df.get(term, 0) + 0.5) / (df.get(term, 0) + 0.5))
            denom = tf + 1.2 * (1 - 0.75 + 0.75 * (len(terms) / max(avg_len, 1)))
            score += idf * ((tf * 2.2) / denom)
        if score > 0:
            scored.append((score, document.model_copy(update={"score": round(score, 4)})))

    ordered = sorted(scored, key=lambda item: item[0], reverse=True)[:limit]
    return [
        document.model_copy(update={"matched_by": ["keyword"], "keyword_rank": rank})
        for rank, (_, document) in enumerate(ordered, start=1)
    ]


def rrf_fuse(
    ranked_lists: list[list[CodeSearchResult]],
    *,
    limit: int,
    k: int = 60,
    weights: list[float] | None = None,
) -> list[CodeSearchResult]:
    """用 RRF 融合多个已排序结果列表，并合并同一代码范围的命中来源。"""
    by_key: dict[tuple[str, int, int], CodeSearchResult] = {}
    scores: dict[tuple[str, int, int], float] = {}
    for index, ranked in enumerate(ranked_lists):
        weight = weights[index] if weights and index < len(weights) else 1.0
        for rank, result in enumerate(ranked, start=1):
            key = _result_key(result)
            existing = by_key.get(key)
            by_key[key] = result if existing is None else _merge_results(existing, result)
            scores[key] = scores.get(key, 0.0) + weight / (k + rank)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        by_key[key].model_copy(update={"score": round(score, 4)})
        for key, score in ordered[:limit]
    ]


def _result_key(result: CodeSearchResult) -> tuple[str, int, int]:
    """同一路径和同一行范围视为同一个检索结果。"""
    return (result.relative_path, result.start_line, result.end_line)


def _merge_results(left: CodeSearchResult, right: CodeSearchResult) -> CodeSearchResult:
    """合并向量/关键词重复命中的元数据，保留最好的 rank。"""
    matched_by = [*left.matched_by]
    for source in right.matched_by:
        if source not in matched_by:
            matched_by.append(source)
    return left.model_copy(
        update={
            "matched_by": matched_by,
            "vector_rank": min(
                rank for rank in (left.vector_rank, right.vector_rank) if rank
            )
            if left.vector_rank or right.vector_rank
            else 0,
            "keyword_rank": min(
                rank for rank in (left.keyword_rank, right.keyword_rank) if rank
            )
            if left.keyword_rank or right.keyword_rank
            else 0,
        }
    )


def _tokenize(text: str) -> list[str]:
    """排序阶段使用的轻量 token 化。"""
    return tokenize_code_text(text)

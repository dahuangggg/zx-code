from __future__ import annotations

import math
import re

from agent.code_context.models import CodeSearchResult


def keyword_search(
    query: str,
    documents: list[CodeSearchResult],
    *,
    limit: int,
) -> list[CodeSearchResult]:
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

    return [document for _, document in sorted(scored, key=lambda item: item[0], reverse=True)[:limit]]


def rrf_fuse(
    ranked_lists: list[list[CodeSearchResult]],
    *,
    limit: int,
    k: int = 60,
) -> list[CodeSearchResult]:
    by_key: dict[tuple[str, int, int], CodeSearchResult] = {}
    scores: dict[tuple[str, int, int], float] = {}
    for ranked in ranked_lists:
        for rank, result in enumerate(ranked, start=1):
            key = _result_key(result)
            by_key.setdefault(key, result)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    return [
        by_key[key].model_copy(update={"score": round(score, 4)})
        for key, score in ordered[:limit]
    ]


def _result_key(result: CodeSearchResult) -> tuple[str, int, int]:
    return (result.relative_path, result.start_line, result.end_line)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())

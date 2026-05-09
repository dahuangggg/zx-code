"""state.memory_search — 混合记忆搜索管道（g06）。

五阶段搜索管道，增强 s09 MemoryStore 的检索能力：

  1. TF-IDF 关键词通道 — 精确词汇匹配（文件名、编号、专有名词）
  2. 哈希投影向量通道 — 语义相似匹配（零外部依赖占位，可替换为真实 embedding）
  3. 加权融合 — 向量 0.7 + 关键词 0.3，合并双通道结果
  4. 时间衰减 — 从路径中提取 YYYY-MM-DD，近期记忆得分更高
  5. MMR 重排序 — 最大边际相关度，保证结果多样性

使用方式：

    searcher = HybridMemorySearch()
    chunks = load_memory_chunks(memory_dir)
    results = searcher.search("用户角色和偏好", chunks, top_k=3)
    # [{"path": "user_role.md", "score": 0.82, "snippet": "..."}]

升级路径：将 ``_embed()`` 替换为真实 embedding API 调用，其余管道不变：

    response = openai.embeddings.create(input=text, model="text-embedding-3-small")
    return response.data[0].embedding
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# 内存片段加载
# ---------------------------------------------------------------------------

def load_memory_chunks(memory_dir: Path | str) -> list[dict[str, Any]]:
    """从记忆目录加载所有 .md 文件，返回 chunk 列表。

    每个 chunk 格式：``{"text": ..., "path": ...}``
    跳过空文件和无法读取的文件。
    """
    chunks: list[dict[str, Any]] = []
    directory = Path(memory_dir)
    if not directory.exists():
        return chunks
    for path in sorted(directory.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            chunks.append({"text": text, "path": path.name})
    return chunks


# ---------------------------------------------------------------------------
# HybridMemorySearch
# ---------------------------------------------------------------------------

class HybridMemorySearch:
    """五阶段混合记忆搜索管道：TF-IDF + 向量 + 合并 + 时间衰减 + MMR。"""

    # ── 1. TF-IDF 关键词通道 ──────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        tokens = re.findall(r"[a-z0-9\u4e00-\u9fff]+", text.lower())
        return [t for t in tokens if len(t) > 1 or "\u4e00" <= t <= "\u9fff"]

    def keyword_search(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """TF-IDF 余弦相似度检索。返回按得分降序排列的 top_k 结果。"""
        q_tokens = self._tokenize(query)
        if not q_tokens or not chunks:
            return []

        c_tokens = [self._tokenize(c["text"]) for c in chunks]
        n = len(chunks)

        # 文档频率
        df: dict[str, int] = {}
        for ts in c_tokens:
            for t in set(ts):
                df[t] = df.get(t, 0) + 1

        def tfidf(ts: list[str]) -> dict[str, float]:
            tf: dict[str, int] = {}
            for t in ts:
                tf[t] = tf.get(t, 0) + 1
            return {
                t: count * (math.log((n + 1) / (df.get(t, 0) + 1)) + 1)
                for t, count in tf.items()
            }

        def cosine(a: dict[str, float], b: dict[str, float]) -> float:
            common = set(a) & set(b)
            if not common:
                return 0.0
            dot = sum(a[k] * b[k] for k in common)
            norm_a = math.sqrt(sum(v * v for v in a.values()))
            norm_b = math.sqrt(sum(v * v for v in b.values()))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)

        q_vec = tfidf(q_tokens)
        scored = [
            {"chunk": chunks[i], "score": cosine(q_vec, tfidf(c_tokens[i]))}
            for i in range(n)
            if c_tokens[i]
        ]
        scored = [s for s in scored if s["score"] > 0]
        return sorted(scored, key=lambda x: x["score"], reverse=True)[:top_k]

    # ── 2. 向量通道（哈希投影，可替换为真实 embedding）──────────────────

    @staticmethod
    def _embed(text: str, dim: int = 64) -> list[float]:
        """哈希投影占位 embedding（零外部依赖）。

        生产替换方式：
            response = openai.embeddings.create(input=text, model="text-embedding-3-small")
            return response.data[0].embedding
        """
        tokens = HybridMemorySearch._tokenize(text)
        vec = [0.0] * dim
        for t in tokens:
            h = hash(t)
            for i in range(dim):
                vec[i] += 1.0 if (h >> (i % 62)) & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def vector_search(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        """向量余弦相似度检索。"""
        if not chunks:
            return []
        q_vec = self._embed(query)
        scored = []
        for c in chunks:
            c_vec = self._embed(c["text"])
            dot = sum(a * b for a, b in zip(q_vec, c_vec))
            if dot > 0:
                scored.append({"chunk": c, "score": dot})
        return sorted(scored, key=lambda x: x["score"], reverse=True)[:top_k]

    # ── 3. 加权融合 ───────────────────────────────────────────────────────

    @staticmethod
    def merge(
        vector_results: list[dict[str, Any]],
        keyword_results: list[dict[str, Any]],
        v_weight: float = 0.7,
        k_weight: float = 0.3,
    ) -> list[dict[str, Any]]:
        """按路径去重并加权合并双通道得分。"""
        merged: dict[str, dict[str, Any]] = {}
        for r in vector_results:
            key = r["chunk"]["path"]
            merged[key] = {"chunk": r["chunk"], "score": r["score"] * v_weight}
        for r in keyword_results:
            key = r["chunk"]["path"]
            if key in merged:
                merged[key]["score"] += r["score"] * k_weight
            else:
                merged[key] = {"chunk": r["chunk"], "score": r["score"] * k_weight}
        return sorted(merged.values(), key=lambda x: x["score"], reverse=True)

    # ── 4. 时间衰减 ────────────────────────────────────────────────────────

    @staticmethod
    def temporal_decay(
        results: list[dict[str, Any]],
        decay_rate: float = 0.01,
    ) -> list[dict[str, Any]]:
        """从 chunk 路径中提取日期，按天数施加指数衰减。"""
        now = datetime.now(timezone.utc)
        for r in results:
            age_days = 0.0
            m = re.search(r"(\d{4}-\d{2}-\d{2})", r["chunk"].get("path", ""))
            if m:
                try:
                    d = datetime.strptime(m.group(1), "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    age_days = (now - d).total_seconds() / 86400
                except ValueError:
                    pass
            r["score"] *= math.exp(-decay_rate * max(age_days, 0))
        return results

    # ── 5. MMR 重排序（最大边际相关度）────────────────────────────────────

    @staticmethod
    def mmr_rerank(
        results: list[dict[str, Any]],
        lam: float = 0.7,
    ) -> list[dict[str, Any]]:
        """贪心 MMR：每步选择相关性高且与已选结果差异大的候选。"""
        if len(results) <= 1:
            return results
        tokenized = [
            set(HybridMemorySearch._tokenize(r["chunk"]["text"])) for r in results
        ]
        selected: list[int] = []
        remaining = list(range(len(results)))
        reranked: list[dict[str, Any]] = []

        while remaining:
            best_idx, best_score = -1, float("-inf")
            for i in remaining:
                rel = results[i]["score"]
                if selected:
                    max_sim = max(
                        (
                            len(tokenized[i] & tokenized[j])
                            / len(tokenized[i] | tokenized[j])
                            if tokenized[i] | tokenized[j]
                            else 0.0
                        )
                        for j in selected
                    )
                else:
                    max_sim = 0.0
                score = lam * rel - (1 - lam) * max_sim
                if score > best_score:
                    best_score, best_idx = score, i
            selected.append(best_idx)
            remaining.remove(best_idx)
            reranked.append(results[best_idx])
        return reranked

    # ── 完整管道 ────────────────────────────────────────────────────────────

    def search(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """执行完整五阶段搜索管道，返回 top_k 结果。

        返回格式：
            [{"path": "file.md", "score": 0.82, "snippet": "...前 200 字符"}]
        """
        kw = self.keyword_search(query, chunks, top_k=10)
        vec = self.vector_search(query, chunks, top_k=10)
        merged = self.merge(vec, kw)
        decayed = self.temporal_decay(merged)
        reranked = self.mmr_rerank(decayed)
        return [
            {
                "path": r["chunk"]["path"],
                "score": round(r["score"], 4),
                "snippet": r["chunk"]["text"][:200],
            }
            for r in reranked[:top_k]
        ]

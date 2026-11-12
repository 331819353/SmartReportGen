"""混合检索：pgvector 余弦相似度 + 关键词（jieba 分词 + IDF 加权，分字段权重），RRF 融合。"""
from __future__ import annotations

import logging

from . import db
from .embedding import EmbeddingError, embed_query, to_pgvector
from .textproc import tokenize

log = logging.getLogger(__name__)
RRF_K = 60
FIELD_W = {"a": 3.0, "b": 1.5, "c": 1.0}

_TABLES = {"dataset": "dataset.dataset", "style_rule": "style_rule.style_rule"}


def _keyword(table: str, tokens: list[str], where: str, params: dict, limit: int) -> list[dict]:
    if not tokens:
        return []
    sql = f"""
    WITH q AS (SELECT DISTINCT unnest(%(toks)s::text[]) AS tok),
    base AS (SELECT * FROM {table} t WHERE t.enabled {where}),
    n AS (SELECT greatest(count(*), 1)::float AS n FROM base),
    df AS (SELECT q.tok, (SELECT count(*) FROM base d WHERE q.tok = ANY(d.kw_all))::float AS df FROM q),
    idf AS (SELECT df.tok, ln(1 + (n.n - df.df + 0.5) / (df.df + 0.5)) AS w FROM df, n WHERE df.df > 0)
    SELECT d.id,
           sum(idf.w * ((CASE WHEN idf.tok = ANY(d.kw_a) THEN {FIELD_W['a']} ELSE 0 END)
                      + (CASE WHEN idf.tok = ANY(d.kw_b) THEN {FIELD_W['b']} ELSE 0 END)
                      + (CASE WHEN idf.tok = ANY(d.kw_c) THEN {FIELD_W['c']} ELSE 0 END))) AS score,
           array_agg(idf.tok ORDER BY idf.w DESC) AS matched
    FROM base d JOIN idf ON idf.tok = ANY(d.kw_all)
    GROUP BY d.id ORDER BY score DESC LIMIT %(limit)s
    """
    return db.fetchall(sql, {**params, "toks": tokens, "limit": limit})


def _vector(table: str, vec: list[float], where: str, params: dict, limit: int) -> list[dict]:
    sql = f"""
    SELECT t.id, 1 - (t.embedding <=> %(vec)s::vector) AS score
    FROM {table} t WHERE t.enabled AND t.embedding IS NOT NULL {where}
    ORDER BY t.embedding <=> %(vec)s::vector LIMIT %(limit)s
    """
    return db.fetchall(sql, {**params, "vec": to_pgvector(vec), "limit": limit})


def hybrid(kind: str, query: str, top_k: int = 5, where: str = "", params: dict | None = None) -> dict:
    table = _TABLES[kind]
    params = params or {}
    tokens = tokenize(query)
    cand = max(top_k * 4, 20)
    warnings: list[str] = []
    kw = _keyword(table, tokens, where, params, cand)
    try:
        vec_rows = _vector(table, embed_query(query), where, params, cand)
    except EmbeddingError as e:
        log.warning("向量检索失败，降级为关键词检索: %s", e)
        warnings.append(f"向量检索不可用，已降级为纯关键词检索：{e}")
        vec_rows = []

    fused: dict[int, dict] = {}
    for rank, r in enumerate(vec_rows, 1):
        f = fused.setdefault(r["id"], {"id": r["id"], "rrf": 0.0})
        f.update(vector_score=round(float(r["score"]), 4), vector_rank=rank)
        f["rrf"] += 1.0 / (RRF_K + rank)
    for rank, r in enumerate(kw, 1):
        f = fused.setdefault(r["id"], {"id": r["id"], "rrf": 0.0})
        f.update(keyword_score=round(float(r["score"]), 4), keyword_rank=rank, matched_terms=r["matched"])
        f["rrf"] += 1.0 / (RRF_K + rank)
    # relevance：原始分数归一化后的加权和（0~1），用于判断置信度；排序仍以 RRF 为准
    kmax = max((r.get("keyword_score") or 0) for r in fused.values()) if fused else 0
    has_vec = bool(vec_rows)
    for r in fused.values():
        kn = (r.get("keyword_score") or 0) / kmax if kmax else 0
        vn = max(0.0, r.get("vector_score") or 0)
        # 没有任何候选命中关键词（纯口语化 / 同义表达）时，只按语义相似度评估，避免相关度被减半
        r["relevance"] = round((0.5 * vn + 0.5 * kn) if has_vec and kmax else (vn if has_vec else kn), 4)
    ranked = sorted(fused.values(), key=lambda x: x["rrf"], reverse=True)[:top_k]
    for r in ranked:
        r["rrf"] = round(r["rrf"], 5)
    return {"query_tokens": tokens, "hits": ranked, "warnings": warnings}

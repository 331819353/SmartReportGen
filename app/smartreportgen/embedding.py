"""文本向量化。

- openai：调用任意 OpenAI 兼容 /embeddings 接口（推荐 bge-m3 / text-embedding-v3 等中文模型）
- hash：离线特征哈希（分词 + 字二元组），无语义理解能力，仅作为无模型时的兜底
"""
from __future__ import annotations

import hashlib
import math
from functools import lru_cache

import httpx

from .config import settings
from .textproc import char_ngrams, tokenize


class EmbeddingError(RuntimeError):
    pass


def _hash_embed(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    feats = [("w", t) for t in tokenize(text)] + [("c", g) for g in char_ngrams(text)]
    if not feats:
        return vec
    counts: dict[tuple[str, str], int] = {}
    for f in feats:
        counts[f] = counts.get(f, 0) + 1
    for (kind, tok), cnt in counts.items():
        h = hashlib.blake2b(f"{kind}:{tok}".encode(), digest_size=8).digest()
        idx = int.from_bytes(h[:4], "little") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        weight = (1.0 + math.log(cnt)) * (1.5 if kind == "w" else 1.0)
        vec[idx] += sign * weight
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _openai_embed(texts: list[str]) -> list[list[float]]:
    if not settings.embedding_base_url or not settings.embedding_model:
        raise EmbeddingError("EMBEDDING_PROVIDER=openai 时必须配置 EMBEDDING_BASE_URL 与 EMBEDDING_MODEL")
    headers = {"Content-Type": "application/json"}
    if settings.embedding_api_key:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
    body: dict = {"model": settings.embedding_model, "input": texts}
    if settings.embedding_send_dimensions:
        body["dimensions"] = settings.embedding_dim
    try:
        r = httpx.post(
            f"{settings.embedding_base_url}/embeddings",
            json=body,
            headers=headers,
            timeout=settings.embedding_timeout,
        )
        r.raise_for_status()
        data = r.json()["data"]
    except Exception as e:  # noqa: BLE001
        raise EmbeddingError(f"向量接口调用失败: {e}") from e
    vecs = [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]
    for v in vecs:
        if len(v) != settings.embedding_dim:
            raise EmbeddingError(
                f"向量维度不匹配：模型返回 {len(v)} 维，EMBEDDING_DIM={settings.embedding_dim}"
            )
    return vecs


def embed_many(texts: list[str]) -> list[list[float]]:
    if settings.embedding_provider == "openai":
        out: list[list[float]] = []
        for i in range(0, len(texts), 32):
            out.extend(_openai_embed(texts[i : i + 32]))
        return out
    return [_hash_embed(t, settings.embedding_dim) for t in texts]


@lru_cache(maxsize=512)
def _embed_cached(text: str) -> tuple[float, ...]:
    return tuple(embed_many([text])[0])


def embed(text: str) -> list[float]:
    return list(_embed_cached(text))


def embed_query(text: str) -> list[float]:
    """检索查询向量：指令式模型在查询侧加指令前缀，文档侧不加。"""
    if settings.embedding_provider == "openai" and settings.embedding_query_instruction:
        return embed(settings.embedding_query_instruction + text)
    return embed(text)


def to_pgvector(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def info() -> dict:
    return {
        "provider": settings.embedding_provider,
        "model": settings.embedding_model if settings.embedding_provider == "openai" else "hash-v1（离线兜底，无语义）",
        "dim": settings.embedding_dim,
        "signature": settings.embedding_signature,
        "query_instruction": bool(settings.embedding_query_instruction),
        "base_url": settings.embedding_base_url if settings.embedding_provider == "openai" else "",
    }

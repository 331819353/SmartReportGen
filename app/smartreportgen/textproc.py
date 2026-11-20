"""中文分词（jieba）与关键词提取。"""
from __future__ import annotations

import logging
import re
import threading

import jieba

jieba.setLogLevel(logging.WARNING)
_lock = threading.Lock()
_custom_words: set[str] = set()

STOPWORDS = set(
    """的 了 和 与 及 或 等 是 在 对 把 被 从 按 为 以 及其 并 并且 而 但 也 都 就 还 要 会 能 可以 可 让 给
    帮 帮我 我 我们 你 您 请 一下 一个 一份 一些 需要 想要 想 希望 生成 做 份 进行 相关 情况 分析 报告 数据 看看 看 了解
    关于 基于 根据 包括 包含 以及 主要 什么 如何 怎么 怎样 哪些 哪个 这个 那个 这些 那些 其 中 上 下 内 各 每 个 所有 整体
    the a an of to and or for in on with by""".split()
)
_TOKEN_OK = re.compile(r"^[\w一-鿿]+$")
_CJK = re.compile(r"[一-鿿]")
# 只扩展可互换的业务检索词，避免把同比、环比或销售额等不同口径混为同义词。
_SCENARIO_TERMS = (
    ("营收", "营业收入"), ("市占率", "市场占有率", "市场份额"),
    ("同比", "去年同期"), ("环比", "上期对比"),
    ("客户留存", "用户留存"), ("服务水平协议", "sla"),
    ("准时交付率", "按时交付率"), ("库存周转", "存货周转"),
)
_SCENARIO_PATTERNS = [(re.compile(r"(?<![a-z0-9])(?:" + "|".join(terms) + r")(?![a-z0-9])"), terms)
                      for terms in _SCENARIO_TERMS]


def add_words(words) -> None:
    """把业务场景名、标签加入词典，保证其作为完整词被切出。"""
    with _lock:
        for w in words:
            w = (w or "").strip()
            if len(w) >= 2 and w not in _custom_words:
                jieba.add_word(w, freq=2000)
                _custom_words.add(w)


def tokenize(text: str) -> list[str]:
    """搜索模式分词，去停用词/标点/单字，返回去重后保持顺序的词列表。"""
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for t in jieba.lcut_for_search(text.lower()):
        t = t.strip()
        if not t or t in STOPWORDS or not _TOKEN_OK.match(t):
            continue
        if len(t) < 2 and not t.isdigit():
            continue
        if t.isdigit() and len(t) < 2:
            continue
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def char_ngrams(text: str, n: int = 2) -> list[str]:
    cjk = "".join(_CJK.findall(text or ""))
    return [cjk[i : i + n] for i in range(max(0, len(cjk) - n + 1))]


def scenario_tokens(text: str) -> list[str]:
    """离线场景检索补充常用同义词及其分词，直接匹配已建索引的历史文本。"""
    normalized = text.lower()
    tokens = tokenize(text)
    for pattern, terms in _SCENARIO_PATTERNS:
        if pattern.search(normalized):
            for term in terms:
                tokens.extend((term, *tokenize(term)))
    return list(dict.fromkeys(tokens))

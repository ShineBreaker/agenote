# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""BM25 排序测试：中英混合分词、稀有词区分度、tf 饱和、边界行为。"""

from __future__ import annotations

from agenote.ranking import BM25, tokenize


# ── tokenize ───────────────────────────────────────────────────────────────────


def test_tokenize_cjk_ngrams():
    """中文段产出 1/2/3-gram：「构建」→ 构建/构/建。"""
    tokens = tokenize("构建")
    assert "构建" in tokens
    assert "构" in tokens and "建" in tokens


def test_tokenize_latin_words_and_strip_punct():
    tokens = tokenize("Hello, WORLD! it's")
    assert tokens == ["hello", "world", "it's"]


def test_tokenize_nfkc_casefold():
    """全角字母/大写归一后同 token。"""
    assert tokenize("Ｇｕｉｘ") == tokenize("guix")


def test_tokenize_mixed_text():
    """中英混排：各段独立切分，不产生中英混合 token。"""
    tokens = tokenize("用 Waybar 配置 Bar")
    assert "waybar" in tokens
    assert "配置" in tokens
    assert "bar" in tokens
    assert not any("用" in t and len(t) > 2 for t in tokens)


def test_tokenize_empty():
    assert tokenize("") == []
    assert tokenize("!!！??") == []


# ── BM25 ───────────────────────────────────────────────────────────────────────


def test_bm25_rare_term_outranks_common():
    """稀有词命中的文档应排在只含常见词的文档前（IDF 区分度）。"""
    docs = {
        "common": tokenize("配置 配置 配置 系统 系统"),
        "rare": tokenize("waybar 样式配置"),
        "filler": tokenize("系统 配置"),
    }
    bm25 = BM25(docs)
    q = tokenize("waybar")
    scores = {k: bm25.score(k, q) for k in docs}
    assert scores["rare"] > scores["common"]
    assert scores["common"] == 0.0  # 未含查询词


def test_bm25_tf_saturation():
    """同词重复 50 次的得分不应是 5 次的 10 倍（Okapi 饱和）。"""
    docs = {
        "low": tokenize("guix") * 5 + tokenize("别的词"),
        "high": tokenize("guix") * 50 + tokenize("别的词"),
        "pad1": tokenize("无关内容一"),
        "pad2": tokenize("无关内容二"),
    }
    bm25 = BM25(docs)
    q = tokenize("guix")
    low = bm25.score("low", q)
    high = bm25.score("high", q)
    assert high > low
    assert high < low * 3  # 10 倍 tf 只换来 <3 倍得分


def test_bm25_idf_positive():
    """平滑 IDF 恒正：极常见词也不产生负贡献。"""
    docs = {f"d{i}": tokenize("配置") for i in range(10)}
    bm25 = BM25(docs)
    assert bm25.idf("配置") > 0


def test_bm25_unknown_key_and_empty_query():
    docs = {"a": tokenize("内容")}
    bm25 = BM25(docs)
    assert bm25.score("nope", tokenize("内容")) == 0.0
    assert bm25.score("a", []) == 0.0


def test_bm25_multi_term_accumulates():
    docs = {
        "both": tokenize("waybar 样式"),
        "one": tokenize("waybar"),
        "none": tokenize("完全无关"),
    }
    bm25 = BM25(docs)
    q = tokenize("waybar 样式")
    assert bm25.score("both", q) > bm25.score("one", q) > bm25.score("none", q)

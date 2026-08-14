# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""候选 2 拆分回归：cards/search/curator 三模块落位与核心算法。"""

from __future__ import annotations

import agenote.cards as cards
import agenote.curator as curator
import agenote.search as search


def test_module_boundaries():
    """函数按 ADR-0002 落位：CRUD 在 cards，检索在 search，策展在 curator。"""
    for fn in ("cmd_add", "cmd_get", "cmd_list", "cmd_update", "cmd_touch",
               "cmd_merge", "cmd_connect", "cmd_inbox", "cmd_stats",
               "cmd_fields", "cmd_tags"):
        assert hasattr(cards, fn), fn
    for fn in ("cmd_search", "_cross_domain_search", "_query_terms",
               "_iter_search_targets", "_merge_ranges", "_range_score",
               "_line_contains_any", "_make_search_snippet"):
        assert hasattr(search, fn), fn
    for fn in ("cmd_archive", "cmd_restore", "cmd_deduplicate", "cmd_review",
               "cmd_curate", "_jaccard_similarity", "_mark_auto_stale"):
        assert hasattr(curator, fn), fn
    # 迁出的不再留在 cards
    assert not hasattr(cards, "cmd_search")
    assert not hasattr(cards, "cmd_curate")
    assert not hasattr(cards, "_jaccard_similarity")
    # core 不再持有搜索辅助
    import agenote.core as core
    for fn in ("_query_terms", "_iter_search_targets", "_merge_ranges",
               "_range_score", "_line_contains_any"):
        assert not hasattr(core, fn), fn


def test_health_reuses_curator_jaccard():
    """health 与 curator 共享同一 Jaccard 实现（消除跨职责 seam）。"""
    import agenote.health as health

    assert health._jaccard_similarity is curator._jaccard_similarity


def test_query_terms_split_and_dedup():
    assert search._query_terms("a/b c，d d") == ["a", "b", "c", "d"]
    assert search._query_terms("  ") == []
    assert search._query_terms("唯一短语") == ["唯一短语"]


def test_merge_ranges_and_line_contains():
    assert search._merge_ranges([(1, 3), (3, 5), (8, 9)]) == [(1, 5), (8, 9)]
    assert search._merge_ranges([]) == []
    assert search._line_contains_any("Hello World", ["world"], False)
    assert not search._line_contains_any("Hello World", ["world"], True)


def test_range_score():
    lines = ["alpha beta", "gamma", "alpha"]
    assert search._range_score(lines, 0, 2, ["alpha"], False) == 1 * 100 + 2
    assert search._range_score(lines, 1, 1, ["alpha"], False) == 0


def test_jaccard_similarity():
    assert curator._jaccard_similarity("fix bug", "Fix BUG") == 1.0
    assert curator._jaccard_similarity("a b", "c d") == 0.0
    assert curator._jaccard_similarity("", "x") == 0.0
    # 交集 1 / 并集 3
    assert abs(curator._jaccard_similarity("a b", "b c") - 1 / 3) < 1e-9


def test_make_search_snippet():
    content = "l1\nl2\nhit here\nl4\nl5"
    snippet = search._make_search_snippet({"content": content}, ["hit"], False)
    assert snippet.splitlines() == ["l1", "l2", "hit here", "l4", "l5"]
    no_hit = search._make_search_snippet({"content": content}, ["zzz"], False)
    assert no_hit == ""

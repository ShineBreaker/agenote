# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""S9 补测：distill 聚类判定 / dream 评分·分词·窗口 / curator 去重阈值（纯函数面）。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import agenote.curator as curator
import agenote.distill as distill
import agenote.dream as dream


def _card(cid, cat="tooling", tech="pytest", type_="debug", usage=0):
    return {
        "id": cid, "title": f"t-{cid}", "category": cat, "tech": tech,
        "type": type_, "usage_count": usage, "created": "2026-09-01",
    }


# ── distill ──────────────────────────────────────────────────────────────

def test_cluster_key_fallback():
    assert distill._cluster_key({"category": "a"}) == ("a", "a")
    assert distill._cluster_key({}) == ("general", "general")


def test_skill_name_kebab():
    assert distill._to_skill_name("general", "general") == "general"
    assert distill._to_skill_name("工具链", "kb-sync") == "kb-sync-工具链"


def test_gather_needs_min_cluster_size():
    assert distill._gather_candidates([_card("C1", type_="ascended")]) == []
    got = distill._gather_candidates(
        [_card("C1", type_="ascended"), _card("C2", type_="ascended")]
    )
    assert len(got) == 1 and got[0][0].card_count == 2
    assert {c["id"] for c in got[0][1]} == {"C1", "C2"}


def test_gather_usage_count_path():
    n = distill.MIN_USAGE_FOR_ASCEND
    cards = [_card("C1", usage=n), _card("C2", usage=n), _card("C3", usage=n - 1)]
    got = distill._gather_candidates(cards)
    assert len(got) == 1 and got[0][0].card_ids == ["C1", "C2"]


def test_gather_splits_by_tech_and_sorts_desc():
    cards = [
        _card("A1", cat="a", tech="x", type_="ascended"),
        _card("A2", cat="a", tech="x", type_="ascended"),
        _card("A3", cat="a", tech="x", type_="ascended"),
        _card("B1", cat="a", tech="y", type_="ascended"),
        _card("B2", cat="a", tech="y", type_="ascended"),
        _card("C1", cat="a", tech="z", type_="ascended"),  # 孤 cluster，不成候选
    ]
    got = distill._gather_candidates(cards)
    assert [c.card_count for c, _ in got] == [3, 2]


# ── curator 去重阈值 ─────────────────────────────────────────────────────

def test_deduplicate_threshold(monkeypatch, capsys):
    cards = [
        _card("A", cat="c", tech="t") | {"title": "完全相同的标题"},
        _card("B", cat="c", tech="t") | {"title": "完全相同的标题"},
        _card("C", cat="c", tech="t") | {"title": "毫不相干的另一件事"},
    ]
    monkeypatch.setattr(curator, "_load_index", lambda ctx: {"cards": cards})
    curator.cmd_deduplicate(argparse.Namespace(threshold=None, json=True), ctx=None)
    pairs = json.loads(capsys.readouterr().out)
    assert [(p["id_a"], p["id_b"]) for p in pairs] == [("A", "B")]
    assert pairs[0]["similarity"] == 1.0

    # 全不相同 → 零对（零产物即成功，不报错）
    monkeypatch.setattr(
        curator, "_load_index", lambda ctx: {"cards": cards[2:3] + [_card("D")]},
    )
    curator.cmd_deduplicate(argparse.Namespace(threshold=None, json=False), ctx=None)
    assert "未检测到重复卡片" in capsys.readouterr().out


def test_jaccard_unit():
    assert curator._jaccard_similarity("a b", "a b") == 1.0
    assert curator._jaccard_similarity("a b", "c d") == 0.0
    assert curator._jaccard_similarity("", "x") == 0.0


# ── dream ────────────────────────────────────────────────────────────────

def test_term_quality_morphology_order():
    plain = dream._term_quality_score("kuber", 5, 100)
    assert dream._term_quality_score("kb-sync", 5, 100) > plain  # 标识符 bonus
    assert dream._term_quality_score("评估", 5, 100) < plain  # CJK 二字降权
    assert dream._term_quality_score("x", 0, 100) == 0.0  # df=0 防御


def test_tokenize_ascii_stopwords_and_cjk():
    toks = dream._tokenize("host-spawn workflow with the config 的 测试配置文件")
    assert "host-spawn" in toks
    assert "the" not in toks and "config" not in toks  # 停用词/领域词过滤
    assert "的" not in toks


def _fact(fid, term, ts=""):
    body = f"Working notes on {term} workflow and debugging. " * 8 + f"{term} fix. "
    assert len(body) >= 300
    return {
        "id": fid, "title": f"notes about {term} {fid}", "content": body,
        "timestamp": ts, "category": "general", "source": "opencode",
    }


def test_gather_window_filters_old_keeps_no_ts(monkeypatch):
    monkeypatch.setattr(dream, "_kb_covered_titles", lambda: set())
    now_ms = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    facts = [_fact(f"new{i}", "kb-sync-probe", now_ms) for i in range(6)]
    facts.append(_fact("nots", "kb-sync-probe"))  # 无 ts 默认保留
    facts += [_fact(f"old{i}", "kb-sync-ancient", "1577836800000") for i in range(6)]
    sliced, total, used, snap = dream._gather_candidates(facts, window_days=90)
    terms = [c.term for c in sliced]
    assert "kb-sync-probe" in terms
    assert "kb-sync-ancient" not in terms
    assert used == 7
    assert len(snap) == 8
    scores = [c.score for c in sliced]
    assert scores == sorted(scores, reverse=True)

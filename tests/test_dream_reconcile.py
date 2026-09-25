# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""S4 dream 持久游标 + S5 reconcile 水位/orphan 回归测试。"""

from __future__ import annotations

import json
from unittest.mock import patch

import agenote.dream as dream
import agenote.reconcile as reconcile


def _redirect(tmp_path, monkeypatch):
    """把 agent 域与 reconcile 索引重定向到 tmp（_kb_titles/dream 覆盖检查同步走空）。

    atomic_write 按 core.KB_ROOT 做 containment（lazy 引用），故 KB_ROOT 本体
    也要重定向（沿用 test_reconcile_kb_titles.py 的 patch 口径）。
    """
    monkeypatch.setattr("agenote.core.KB_ROOT", tmp_path)
    monkeypatch.setattr(reconcile, "AGENOTE_ROOT", tmp_path)
    monkeypatch.setattr(reconcile, "RECONCILE_DIR", tmp_path / ".reconcile")
    monkeypatch.setattr(
        reconcile, "RECONCILE_INDEX", tmp_path / ".reconcile" / "index.json"
    )
    monkeypatch.setattr(dream, "AGENOTE_ROOT", tmp_path)


def _fact(i: int, term: str, source: str = "fake") -> dict:
    return {
        "id": f"{source}:s{i}:m{i}",
        "source": source,
        "native_id": f"m{i}",
        "title": f"关于{term}的讨论{i}",
        "category": "general",
        "content": f"第{i}条：{term} 的配置经验与踩坑记录，步骤完整可复现。",
        "retrieved_at": "2026-09-24T00:00:00",
        "timestamp": "",  # 无 ts 默认保留，不受 window 过滤
        "tags": [],
        "trust_score": 0.7,
        "weight": 0.5,
    }


def _write_index(tmp_path, facts: list[dict], meta: dict | None = None) -> None:
    d = tmp_path / ".reconcile"
    d.mkdir(exist_ok=True)
    idx = {
        "version": 1,
        "updated": "2026-09-24T00:00:00",
        "by_source": {"fake": len(facts)},
        "facts": facts,
        "meta": meta or {},
    }
    (d / "index.json").write_text(
        json.dumps(idx, ensure_ascii=False), encoding="utf-8"
    )


def test_dream_same_hash_short_circuits(tmp_path, monkeypatch):
    """同 hash 二次调用短路：unchanged=True + candidates 清空 + 游标指针信息。"""
    _redirect(tmp_path, monkeypatch)
    _write_index(tmp_path, [_fact(i, "host-spawn") for i in range(6)])

    first = dream.run_dream()
    assert first.snapshot_hash, "首轮应有候选与指纹"
    assert first.unchanged is False
    assert len(first.candidates) == 5

    second = dream.run_dream()
    assert second.unchanged is True
    assert second.candidates == []  # 短路不重复报候选（省消费方注意力）
    assert second.total_candidates == first.total_candidates  # 候选数保留在指针信息里
    assert "未变化" in second.message
    cursor = json.loads((tmp_path / "dream-cursor.json").read_text(encoding="utf-8"))
    assert cursor["last_run_at"] in second.message  # 指回上次报告时间
    assert first.snapshot_hash in second.message
    assert second.snapshot_hash == first.snapshot_hash


def test_dream_cursor_corrupt_falls_back(tmp_path, monkeypatch):
    """游标损坏/非 dict → 回空 dict 不 fail-closed：本轮按首跑处理，游标重建。"""
    _redirect(tmp_path, monkeypatch)
    _write_index(tmp_path, [_fact(i, "host-spawn") for i in range(6)])
    (tmp_path / "dream-cursor.json").write_text("{not-json!!", encoding="utf-8")

    report = dream.run_dream()
    assert report.unchanged is False  # 损坏游标不误判「未变化」
    rebuilt = json.loads((tmp_path / "dream-cursor.json").read_text(encoding="utf-8"))
    assert rebuilt["snapshot_hash"] == report.snapshot_hash

    (tmp_path / "dream-cursor.json").write_text('["wrong", "shape"]', encoding="utf-8")
    assert dream.run_dream().unchanged is False  # 非 dict 同样回退


def test_dream_zero_candidates_skips_cursor_write(tmp_path, monkeypatch):
    """零候选（total==0 提前返回）不写游标：无可固化的候选集指纹。"""
    _redirect(tmp_path, monkeypatch)
    _write_index(tmp_path, [_fact(0, "host-spawn")])  # df=1 < MIN_TERM_FREQ → 无候选

    report = dream.run_dream()
    assert report.candidates == [] and report.total_candidates == 0
    assert report.unchanged is False  # 与「未变化短路」可区分
    assert not (tmp_path / "dream-cursor.json").exists()


def test_dream_recomputes_after_index_update(tmp_path, monkeypatch):
    """索引更新（候选集变化）→ snapshot 变化 → 全量重算，不短路。"""
    _redirect(tmp_path, monkeypatch)
    _write_index(tmp_path, [_fact(i, "host-spawn") for i in range(6)])
    first = dream.run_dream()

    _write_index(tmp_path, [_fact(i, "kb-summarize") for i in range(6)])
    second = dream.run_dream()
    assert second.snapshot_hash != first.snapshot_hash
    assert second.unchanged is False


def test_reconcile_empty_source_keeps_orphans(tmp_path, monkeypatch):
    """源抽空（上轮有数）→ 旧事实保留 + orphan:true + 检测时间，不清空。"""
    _redirect(tmp_path, monkeypatch)
    _write_index(
        tmp_path,
        [_fact(0, "host-spawn"), _fact(1, "host-spawn")],
        meta={"fake": {"last_success_at": "2026-09-24T00:00:00", "last_fact_count": 2}},
    )
    empty = lambda: {"fake": lambda: ([], [])}  # noqa: E731 源消失：抽到 0 facts

    with patch.object(reconcile, "_known_extractors", empty):
        rep = reconcile.reconcile_source("fake")
    assert rep.errors == 0
    assert rep.indexed == 0
    assert rep.orphaned == 2

    kept = reconcile.load_reconcile_facts()
    assert len(kept) == 2
    assert all(f.get("orphan") is True for f in kept)
    assert all(f.get("orphan_detected_at") for f in kept)

    # 持续为空 → 继续保留（持久直到显式 prune，不自动清）
    with patch.object(reconcile, "_known_extractors", empty):
        reconcile.reconcile_source("fake")
    assert len(reconcile.load_reconcile_facts()) == 2


def test_reconcile_prune_orphans_clears(tmp_path, monkeypatch):
    """--prune-orphans 显式清理 orphan 旧事实，水位归零。"""
    _redirect(tmp_path, monkeypatch)
    _write_index(
        tmp_path,
        [_fact(0, "host-spawn"), _fact(1, "host-spawn")],
        meta={"fake": {"last_success_at": "2026-09-24T00:00:00", "last_fact_count": 2}},
    )
    empty = lambda: {"fake": lambda: ([], [])}  # noqa: E731

    with patch.object(reconcile, "_known_extractors", empty):
        rep = reconcile.reconcile_source("fake", prune_orphans=True)
    assert rep.errors == 0
    assert reconcile.load_reconcile_facts() == []
    idx = json.loads(reconcile.RECONCILE_INDEX.read_text(encoding="utf-8"))
    assert idx["meta"]["fake"]["last_fact_count"] == 0

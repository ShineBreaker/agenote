# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""S9 补测：health 三组指标（孤立/过时/类型偏斜）+ 去重与空白判定。"""

from __future__ import annotations

import types
from datetime import datetime

import agenote.health as health


def _ctx(monkeypatch, cards, tmp_path, files=()):
    exp = tmp_path / "exp"
    exp.mkdir(exist_ok=True)
    for name, content in files:
        (exp / name).write_text(content, encoding="utf-8")
    monkeypatch.setattr(health, "ensure_dirs", lambda ctx: None)
    monkeypatch.setattr(health, "_load_index", lambda ctx: {"cards": cards})
    monkeypatch.setattr(health, "formal_types", lambda ctx: set())
    return types.SimpleNamespace(experiences=exp)


def _card(cid, cat="tooling", type_="debug", status="done", created="2026-09-01"):
    return {
        "id": cid, "file": f"{cid}.org", "title": cid, "category": cat,
        "type": type_, "owner": "ai", "status": status, "created": created,
    }


def test_status_boundaries():
    assert health._status(14, 15, 25) == "ok"
    assert health._status(15, 15, 25) == "warn"
    assert health._status(24, 15, 25) == "warn"
    assert health._status(25, 15, 25) == "bad"


def test_base_metrics_three_groups(monkeypatch, tmp_path):
    cards = [
        _card("C1"), _card("C2"),
        _card("C3", status="stale"),
        _card("C4", cat="solo", type_="doc"),
    ]
    linked = "* DONE t\n:PROPERTIES:\n:ID: C1\n:END:\nsee [[file:c2.org]]\n"
    ctx = _ctx(monkeypatch, cards, tmp_path, [("c1.org", linked)])
    m = health._compute_base_metrics(ctx)
    assert (m["isolated"]["pct"], m["isolated"]["count"]) == (75, 3)
    assert m["isolated"]["status"] == "bad"  # 75 ≥ 25
    assert (m["stale"]["pct"], m["stale"]["count"]) == (25, 1)
    assert m["stale"]["status"] == "bad"  # 25 ≥ 20
    assert m["type_skew"]["type"] == "debug" and m["type_skew"]["pct"] == 75
    assert m["type_skew"]["status"] == "bad"  # 75 ≥ 60
    assert m["weak_categories"] == {"solo": 1}  # tooling=3 不算薄弱
    assert m["memory"] == {"feedback": 0, "stale_feedback": 0, "project": 0}


def test_base_metrics_empty_kb(monkeypatch, tmp_path):
    ctx = _ctx(monkeypatch, [], tmp_path)
    m = health._compute_base_metrics(ctx)
    assert m["total"] == 0
    assert m["isolated"] == {"pct": 0, "count": 0, "threshold": 15, "status": "ok"}


def test_detect_duplicates_threshold_boundary():
    a = _card("A") | {"title": "same title here"}
    b = _card("B") | {"title": "same title here"}
    c = _card("C") | {"title": "totally different matter"}
    pairs = health._detect_duplicates([a, b, c])
    assert [(p["id_a"], p["id_b"]) for p in pairs] == [("A", "B")]
    assert pairs[0]["similarity"] == 1.0
    assert health._detect_duplicates([a, b, c], threshold=1.01) == []


def test_find_gaps_weak_and_stale(monkeypatch, tmp_path):
    today = datetime.now().strftime("[%Y-%m-%d]")
    cards = [
        _card("T1", cat="thin", created="[2020-01-01]"),
        _card("T2", cat="thin", created=today),
        _card("F1", cat="fat", created=today),
        _card("F2", cat="fat", created=today),
        _card("F3", cat="fat", created=today),
    ]
    gaps = health.find_gaps(_ctx(monkeypatch, cards, tmp_path))
    assert gaps["missing_count"] == 0  # formal_types 为空
    assert [w["category"] for w in gaps["weak_categories"]] == ["thin"]  # ≤2
    assert [s["id"] for s in gaps["stale_cards"]] == ["T1"]

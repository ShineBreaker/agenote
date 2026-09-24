# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""回归：find_gaps 纯AI类别判定须认 owner=collab（VALID_OWNERS 合法值）。

2026-09：health.py 用 "collaborative" 判定，但 core.VALID_OWNERS 合法值是
"collab"，导致纯协作（collab）类别被误报为纯AI（ai_only_categories）。
"""

from __future__ import annotations

import types

import agenote.health as health_mod


def _ctx_with_cards(monkeypatch, cards):
    ctx = types.SimpleNamespace()
    monkeypatch.setattr(health_mod, "ensure_dirs", lambda ctx: None)
    monkeypatch.setattr(health_mod, "_load_index", lambda ctx: {"cards": cards})
    monkeypatch.setattr(health_mod, "formal_types", lambda ctx: set())
    return ctx


def _card(cid, category, owner):
    return {
        "id": cid,
        "file": f"{cid}.org",
        "title": cid,
        "category": category,
        "type": "debug",
        "owner": owner,
        "status": "active",
        "created": "2026-09-20",
    }


def test_collab_only_category_is_not_ai_only(monkeypatch):
    ctx = _ctx_with_cards(monkeypatch, [_card("C1", "tooling", "collab")])
    gaps = health_mod.find_gaps(ctx)
    assert "tooling" not in gaps["ai_only_categories"]


def test_ai_only_category_is_reported(monkeypatch):
    ctx = _ctx_with_cards(monkeypatch, [_card("C1", "tooling", "ai")])
    gaps = health_mod.find_gaps(ctx)
    assert "tooling" in gaps["ai_only_categories"]


def test_human_only_category_is_not_ai_only(monkeypatch):
    ctx = _ctx_with_cards(monkeypatch, [_card("C1", "tooling", "human")])
    gaps = health_mod.find_gaps(ctx)
    assert "tooling" not in gaps["ai_only_categories"]


def test_mixed_ai_collab_category_is_not_ai_only(monkeypatch):
    ctx = _ctx_with_cards(
        monkeypatch,
        [_card("C1", "tooling", "ai"), _card("C2", "tooling", "collab")],
    )
    gaps = health_mod.find_gaps(ctx)
    assert "tooling" not in gaps["ai_only_categories"]

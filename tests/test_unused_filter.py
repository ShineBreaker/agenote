# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""回归：`list --unused-days` 只收 status=done 的卡片。

2026-09-14：96 张老卡复核后转 stable，但降级候选清单照旧把它们全列出来
（判定只看 last_used），每轮策展都要重审同一批老卡。
"""

from __future__ import annotations

import argparse

import agenote.cards as cards


def _card(cid, status, last_used):
    return {
        "id": cid, "file": f"experiences/general/{cid}-note-general.org",
        "title": f"卡片 {cid}", "category": "general", "type": "note",
        "owner": "ai", "entry_type": "note", "source_agent": "hermes",
        "status": status, "last_used": last_used, "created": "2026-01-01",
    }


def test_unused_days_only_lists_done_cards(monkeypatch, capsys):
    index = {"cards": [
        _card("20260101-000001", "done", "[2026-01-01 Mon 00:00]"),      # 候选
        _card("20260101-000002", "stable", "[2026-01-01 Mon 00:00]"),    # 已复核 → 排除
        _card("20260101-000003", "archived", "[2026-01-01 Mon 00:00]"),  # 出局 → 排除
        _card("20260101-000004", "done", "[2026-09-13 Sun 00:00]"),      # 刚用过 → 排除
    ]}
    monkeypatch.setattr(cards, "_load_index", lambda ctx=None, **kwargs: index)
    args = argparse.Namespace(category=None, type=None, owner=None, all=True,
                              recent=0, unused_days=30, json=True)
    cards.cmd_list(args, ctx=None)

    import json

    out = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in out] == ["20260101-000001"]

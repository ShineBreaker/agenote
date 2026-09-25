# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""S10 展示层契约守护：list --json 字段只增；默认输出无 freshness 标记."""

from __future__ import annotations

import argparse
import json
import types

REQUIRED_LIST_KEYS = {"id", "title", "category", "created"}

# S10 完整旧字段集（agenote-el 渲染与用户脚本的事实契约）：只增不改，缺一即破坏
LEGACY_LIST_KEYS = {
    "id", "title", "category", "type", "tech", "owner",
    "created", "status", "last_used", "usage_count", "source_agent", "file",
}

CARD = """* DONE workflow notes
:PROPERTIES:
:ID:       20260101-000001
:CATEGORY: general
:TECH:     pytest
:STATUS:   done
:CREATED:  [2026-09-01]
:END:
** 任务描述
关键词检索验证
** 执行过程
steps
"""


def _kb(tmp_path, monkeypatch):
    import agenote.core as core
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    root = tmp_path / "kb"
    monkeypatch.setattr(core, "KB_ROOT", root)
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    (exp / "20260101-000001-workflow-general.org").write_text(CARD, encoding="utf-8")
    ctx = KBContext(
        name="test", root=root, experiences=exp, memories=root / "memories",
        projects=root / "memories" / "projects", memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org", index=root / "index.json",
        inbox=root / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)
    return ctx


def test_list_json_keys_only_grow(monkeypatch, capsys):
    import agenote.cards as cards

    monkeypatch.setattr(
        cards, "_load_index",
        lambda ctx: {"cards": [{"id": "X", "title": "t", "category": "c",
                                "created": "[2026-01-01]"}]},
    )
    args = argparse.Namespace(category=None, type=None, owner=None, recent=None,
                              all=True, unused_days=None)
    cards.cmd_list(args, ctx=None)
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert REQUIRED_LIST_KEYS <= set(rows[0])  # el 明面契约 4 键
    assert LEGACY_LIST_KEYS <= set(rows[0])  # 完整旧 12 键（含 tech/owner 等非 el 依赖键）
    assert "last_verified" in rows[0]  # S10 增补键在位


def test_search_json_freshness_opt_in(tmp_path, monkeypatch, capsys):
    import agenote.search as search

    ctx = _kb(tmp_path, monkeypatch)
    base = dict(query="关键词", context=1, case_sensitive=False, all_terms=False,
                limit=5, json=True, _cross_domain=False)
    search.cmd_search(argparse.Namespace(**base, freshness=False), ctx)
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) >= 1 and all("freshness" not in r for r in rows)
    search.cmd_search(argparse.Namespace(**base, freshness=True), ctx)
    rows = json.loads(capsys.readouterr().out)
    assert all("freshness" in r for r in rows)  # 开启才增补


def test_default_outputs_have_no_freshness_markers(tmp_path, monkeypatch, capsys):
    import agenote.cards as cards
    import agenote.curator as curator
    import agenote.health as health_mod

    ctx = _kb(tmp_path, monkeypatch)
    stem = "20260101-000001-workflow-general"
    cards.cmd_stats(argparse.Namespace(), ctx)
    health_mod.cmd_health(argparse.Namespace(duplicates=False, quality=False), ctx)
    curator.cmd_review(argparse.Namespace(id=stem), ctx)
    cards.cmd_get(argparse.Namespace(target=stem, used=False), ctx)
    out = capsys.readouterr().out
    assert "freshness" not in out and "(unverified" not in out


def test_memory_list_freshness_opt_in(tmp_path, monkeypatch, capsys):
    import agenote.memory as memory_mod

    monkeypatch.setattr(memory_mod, "ensure_dirs", lambda ctx: None)
    text = ("#+title: MEMORY-test\n\n* feedback\n** F001 旧条目\n"
            "   :PROPERTIES:\n   :CREATED:  [2020-01-01]\n"
            "   :UPDATED:  [2020-01-01]\n   :END:\n")
    path = tmp_path / "MEMORY.org"
    path.write_text(text, encoding="utf-8")
    ctx = types.SimpleNamespace(memory_org=path)
    # --list 默认无标记；时效 opt-in 走 --type 概览路径（_memory_list 无此键）
    memory_mod.cmd_memory(
        argparse.Namespace(type=None, scope=None, json=False, list=True,
                           freshness=False), ctx)
    assert "(unverified" not in capsys.readouterr().out
    memory_mod.cmd_memory(
        argparse.Namespace(type="F", scope=None, json=False, list=False,
                           freshness=False), ctx)
    assert "(unverified" not in capsys.readouterr().out
    memory_mod.cmd_memory(
        argparse.Namespace(type="F", scope=None, json=False, list=False,
                           freshness=True), ctx)
    assert "(unverified" in capsys.readouterr().out

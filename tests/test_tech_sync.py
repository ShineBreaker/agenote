# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""update --tech/--category/--owner 同步 fingerprint 标签行与索引的回归测试。

背景：cmd_add 写卡片时会生成 :cat:type:owner:tech:entry:: 标签行，索引的 tags
字段即由该行解析。旧版 cmd_update 只在 --type 时同步标签行，--tech/--category
改完属性后标签/索引仍停留在旧值，导致 `agenote tags` 与 fields 漂移。
"""

from __future__ import annotations

import argparse
import json
import re

import pytest

from agenote.cards import cmd_add, cmd_update
from agenote.core import KBContext


@pytest.fixture
def kb_root(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    root.mkdir()
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    return root


def _ctx(root) -> KBContext:
    return KBContext(
        name="agenote",
        root=root,
        experiences=root / "agenote" / "experiences",
        memories=root / "agenote" / "memories",
        projects=root / "agenote" / "memories" / "projects",
        memory_org=root / "agenote" / "MEMORY.org",
        memory_archive=root / "agenote" / "MEMORY-ARCHIVE.org",
        index=root / "agenote" / "index.json",
        inbox=root / "agenote" / "inbox.org",
        is_human=False,
        default_weight=1.0,
        agent_name="zcode",
    )


def _add_args(title, category="general", tech="", type_="debug", entry=""):
    return argparse.Namespace(
        title=title,
        category=category,
        tech=tech,
        type=type_,
        owner="ai",
        entry=entry,
        summary="",
        stdin=False,
        force=False,
    )


def _update_args(target, **overrides):
    base = dict(
        target=target,
        status=None,
        category=None,
        tech=None,
        type_=None,
        owner=None,
        append_to=None,
        append_text=None,
        stdin=False,
        force=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _only_card(ctx):
    files = list(ctx.experiences.rglob("*.org"))
    assert len(files) == 1
    return files[0]


def _index_entry(ctx, card_id):
    idx = json.loads(ctx.index.read_text(encoding="utf-8"))
    return next(c for c in idx["cards"] if c["id"] == card_id)


def test_update_tech_syncs_fingerprint_and_index(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    card_id = re.search(r":ID:\s+(\S+)", card.read_text(encoding="utf-8")).group(1)

    cmd_update(_update_args(str(card), tech="rust"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":TECH:     rust" in content
    assert ":general:debug:ai:rust::" in content
    entry = _index_entry(ctx, card_id)
    assert entry["tech"] == "rust"
    assert "rust" in entry["tags"]


def test_update_tech_equal_category_omits_tag(kb_root):
    """tech 与 category 相同则标签行省略 tech（与 add 同口径）。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="rust"), ctx)
    card = _only_card(ctx)

    cmd_update(_update_args(str(card), tech="general"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":TECH:     general" in content
    assert ":general:debug:ai::" in content


def test_update_category_syncs_fingerprint(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="rust"), ctx)
    card = _only_card(ctx)

    cmd_update(_update_args(str(card), category="emacs"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":CATEGORY: emacs" in content
    assert ":emacs:debug:ai:rust::" in content


def test_update_owner_syncs_fingerprint(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="rust"), ctx)
    card = _only_card(ctx)

    cmd_update(_update_args(str(card), owner="collab"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":OWNER:    collab" in content
    assert ":general:debug:collab:rust::" in content


def test_update_status_keeps_lightweight(kb_root):
    """--status 不刷索引/标签（轻量路径，索引由 reindex 刷新）。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="rust"), ctx)
    card = _only_card(ctx)

    cmd_update(_update_args(str(card), status="stale"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":STATUS:   stale" in content
    assert ":general:debug:ai:rust::" in content

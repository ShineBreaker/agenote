# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""type 门禁与重分类测试：新 type 需 --force；update --type 同步属性/标签/文件名/索引。"""

from __future__ import annotations

import argparse
import json
import re

import pytest

from agenote.cards import cmd_add, cmd_update
from agenote.core import KBContext


@pytest.fixture
def kb_root(tmp_path, monkeypatch):
    """把 KB_ROOT 指到 tmp 目录，让 atomic_write 的 containment 放行测试写入。"""
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


def _add_args(title, type_=None, force=False):
    return argparse.Namespace(
        title=title,
        category="general",
        tech="",
        type=type_,
        owner="ai",
        entry="",
        summary="",
        stdin=False,
        force=force,
    )


def _update_args(target, type_=None, force=False):
    return argparse.Namespace(
        target=target,
        status=None,
        category=None,
        tech=None,
        type_=type_,
        owner=None,
        append_to=None,
        append_text=None,
        stdin=False,
        force=force,
    )


def _only_card(ctx):
    files = list(ctx.experiences.rglob("*.org"))
    assert len(files) == 1
    return files[0]


# ── add 门禁 ───────────────────────────────────────────────────────────────────


def test_add_standard_type_ok(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("标准类型", type_="workflow"), ctx)
    assert "-workflow-" in _only_card(ctx).name


def test_add_new_type_blocked_without_force(kb_root, capsys):
    ctx = _ctx(kb_root)
    with pytest.raises(SystemExit) as e:
        cmd_add(_add_args("新类型", type_="brandnew"), ctx)
    assert e.value.code == 1
    err = capsys.readouterr().err
    assert "brandnew" in err and "--force" in err
    assert not list(ctx.experiences.rglob("*.org"))


def test_add_new_type_with_force(kb_root, capsys):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("新类型", type_="brandnew", force=True), ctx)
    assert "-brandnew-" in _only_card(ctx).name
    assert "--force" in capsys.readouterr().err


def test_add_existing_type_reuse_ok(kb_root):
    """--force 建立 brandnew 后即成为已有 type，后续免检复用。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("第一张", type_="brandnew", force=True), ctx)
    cmd_add(_add_args("第二张", type_="brandnew"), ctx)
    assert len(list(ctx.experiences.rglob("*.org"))) == 2


# ── update --type 完整重分类 ───────────────────────────────────────────────────


def test_update_type_full_reclassify(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", type_="debug"), ctx)
    old_card = _only_card(ctx)
    old_name = old_card.name

    cmd_update(_update_args(str(old_card), type_="workflow"), ctx)

    new_card = _only_card(ctx)
    content = new_card.read_text(encoding="utf-8")
    card_id = re.search(r":ID:\s+(\S+)", content).group(1)
    assert new_card.name != old_name
    assert new_card.name == f"{card_id}-workflow-general.org"
    assert ":TYPE:     workflow" in content
    assert ":general:workflow:ai::" in content
    idx = json.loads(ctx.index.read_text(encoding="utf-8"))
    entry = next(c for c in idx["cards"] if c["id"] == card_id)
    assert entry["type"] == "workflow"
    assert entry["file"].endswith(new_card.name)


def test_update_new_type_blocked(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", type_="debug"), ctx)
    card = _only_card(ctx)
    with pytest.raises(SystemExit):
        cmd_update(_update_args(str(card), type_="brandnew"), ctx)
    assert "-debug-" in _only_card(ctx).name  # 未被改动


def test_update_same_type_noop(kb_root):
    """type 未变化时不重命名、不动标签。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", type_="debug"), ctx)
    card = _only_card(ctx)
    cmd_update(_update_args(str(card), type_="debug"), ctx)
    assert _only_card(ctx) == card
    assert ":general:debug:ai::" in card.read_text(encoding="utf-8")

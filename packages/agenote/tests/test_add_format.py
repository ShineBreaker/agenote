# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""add 写入即格式化：新卡落地即通过 lint 的 format 检查（无需事后跑 lint --fix）。"""

from __future__ import annotations

import argparse

import pytest

from agenote.cards import cmd_add
from agenote.core import KBContext
from agenote.orgfmt import format_org


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
        agent_name="dsh",
    )


def _add_args(title, stdin=False):
    return argparse.Namespace(
        title=title,
        category="kb",
        tech="agenote",
        type=None,
        owner="ai",
        entry="note",
        summary="",
        stdin=stdin,
        force=False,
        source="",
        added="",
        allow_secret=False,
    )


def _only_card(root):
    cards = list((root / "agenote" / "experiences").rglob("*.org"))
    assert len(cards) == 1
    return cards[0]


def test_add_output_is_already_formatted(kb_root):
    """生成的卡片文本必须是 format_org 的不动点（lint 无 format 问题）。"""
    cmd_add(_add_args("测试卡"), _ctx(kb_root))

    text = _only_card(kb_root).read_text(encoding="utf-8")
    formatted, changes = format_org(text, strict=True)

    assert changes == []
    assert formatted == text


def test_add_normalizes_markdown_and_blank_lines(kb_root, monkeypatch):
    """stdin 正文里的 MD 残留与连续空行写入时即被规范化。"""
    monkeypatch.setattr(
        "sys.stdin",
        __import__("io").StringIO("** 背景\n\n用 `markdown` 代码\n\n\n\n** 教训\n- 甲\n"),
    )
    cmd_add(_add_args("测试卡", stdin=True), _ctx(kb_root))

    text = _only_card(kb_root).read_text(encoding="utf-8")

    assert "~markdown~" in text
    assert "`markdown`" not in text
    assert "\n\n\n\n" not in text

# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""U3 出处溯源：卡片 SOURCE/ADDED 写盘、索引透传、检索回显、旧卡兼容。"""

from __future__ import annotations

import argparse
import json

from agenote.cards import cmd_add, cmd_update
from agenote.core import KBContext, today
from agenote.index import _card_dict, _load_index
from agenote.orgserde import parse_org_prop
from agenote.search import cmd_search


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
        agent_name="pi",
    )


def _add_args(title, source=None, added=None):
    return argparse.Namespace(
        title=title,
        category="general",
        tech="",
        type=None,
        owner=None,
        entry="note",
        summary="",
        stdin=False,
        force=False,
        allow_secret=False,
        source=source,
        added=added,
    )


def _update_args(target, source=None, added=None):
    return argparse.Namespace(
        target=target,
        status=None,
        category=None,
        tech=None,
        type_=None,
        owner=None,
        append_to=None,
        append_text=None,
        force=False,
        stdin=False,
        allow_secret=False,
        source=source,
        added=added,
    )


def _search_args(query, as_json):
    return argparse.Namespace(
        query=query,
        context=2,
        case_sensitive=False,
        all_terms=False,
        limit=5,
        json=as_json,
        freshness=False,
        max_blocks=3,
        _cross_domain=False,
    )


def _kb(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    root.mkdir()
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    return root


def _only_card(ctx):
    cards = list(ctx.experiences.rglob("*.org"))
    assert len(cards) == 1
    return cards[0]


def test_add_default_added_is_today(tmp_path, monkeypatch, capsys):
    """不传 --added 时 ADDED 缺省当日；不传 --source 时不写 SOURCE 行。"""
    ctx = _ctx(_kb(tmp_path, monkeypatch))
    cmd_add(_add_args("缺省日期卡"), ctx=ctx)
    capsys.readouterr()
    card = _only_card(ctx)
    text = card.read_text(encoding="utf-8")
    assert parse_org_prop(text, "ADDED") == f"[{today()}]"
    assert ":SOURCE:" not in text
    d = _card_dict(card, ctx)
    assert d["added"] == today()
    assert d["source"] == ""


def test_add_explicit_source_passthrough(tmp_path, monkeypatch, capsys):
    """显式 --source/--added 直写属性，索引原样透传。"""
    ctx = _ctx(_kb(tmp_path, monkeypatch))
    cmd_add(_add_args("显式出处卡", source="https://example.com/doc", added="2026-01-02"), ctx=ctx)
    capsys.readouterr()
    card = _only_card(ctx)
    text = card.read_text(encoding="utf-8")
    assert parse_org_prop(text, "SOURCE") == "https://example.com/doc"
    assert parse_org_prop(text, "ADDED") == "[2026-01-02]"
    d = _card_dict(card, ctx)
    assert d["source"] == "https://example.com/doc"
    assert d["added"] == "2026-01-02"


def test_update_changes_source(tmp_path, monkeypatch, capsys):
    """update --source 改写 SOURCE，索引同步刷新。"""
    ctx = _ctx(_kb(tmp_path, monkeypatch))
    cmd_add(_add_args("待改出处卡"), ctx=ctx)
    capsys.readouterr()
    card = _only_card(ctx)
    card_id = parse_org_prop(card.read_text(encoding="utf-8"), "ID")
    cmd_update(_update_args(card_id, source="新出处手册"), ctx=ctx)
    capsys.readouterr()
    text = card.read_text(encoding="utf-8")
    assert ":SOURCE: 新出处手册" in text
    idx = _load_index(ctx)
    assert idx["cards"][0]["source"] == "新出处手册"


def test_search_echoes_provenance(tmp_path, monkeypatch, capsys):
    """search 文本与 --json 均回显 source/added。"""
    ctx = _ctx(_kb(tmp_path, monkeypatch))
    cmd_add(
        _add_args("检索回显香蕉卡", source="回显出处", added="2026-03-04"),
        ctx=ctx,
    )
    capsys.readouterr()
    cmd_search(_search_args("香蕉", True), ctx=ctx)
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["source"] == "回显出处"
    assert rows[0]["added"] == "2026-03-04"
    cmd_search(_search_args("香蕉", False), ctx=ctx)
    out = capsys.readouterr().out
    assert "回显出处" in out and "2026-03-04" in out


def test_old_entry_missing_provenance_shows_empty(tmp_path, monkeypatch, capsys):
    """旧卡缺 SOURCE/ADDED：索引与检索显空，文件不被回填。"""
    ctx = _ctx(_kb(tmp_path, monkeypatch))
    exp = ctx.experiences / "general"
    exp.mkdir(parents=True)
    card = exp / "20200101-000000-workflow-general.org"
    card.write_text(
        "* DONE 旧卡兼容猕猴桃\n"
        ":PROPERTIES:\n"
        ":ID:       20200101-000000\n"
        ":CREATED:  [2020-01-01]\n"
        ":CATEGORY: general\n"
        ":TECH:     general\n"
        ":TYPE:     workflow\n"
        ":OWNER:    ai\n"
        ":STATUS:   done\n"
        ":USAGE_COUNT: 0\n"
        ":END:\n"
        ":general:workflow:ai::\n"
        "\n** 任务描述\n旧卡\n",
        encoding="utf-8",
    )
    d = _card_dict(card, ctx)
    assert d["source"] == "" and d["added"] == ""
    cmd_search(_search_args("猕猴桃", True), ctx=ctx)
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    assert rows[0]["source"] == "" and rows[0]["added"] == ""
    assert ":SOURCE:" not in card.read_text(encoding="utf-8")
    assert ":ADDED:" not in card.read_text(encoding="utf-8")

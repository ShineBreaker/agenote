# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""N1 事实层数据模型 + `memory --list` 只读命令。"""

from __future__ import annotations

import argparse
import json
import types

import agenote.memory as memory_mod
from agenote.core import MEMORY_SECTIONS

TEXT = """#+title: MEMORY-test

* user
** U001 回复用中文
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     U
   :SCOPE:    user
   :END:
   # 用户要求所有回复使用中文

* feedback
** F001 不要用 cat
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-10]
   :VALIDATED_AT: [2026-09-10]
   :END:

* project
** agenote
   :PROPERTIES:
   :PATH:     /home/u/Projects/agenote
   :FILE:     /home/u/Projects/agenote
   :UPDATED:  [2026-09-20]
   :END:
** P001 提交前跑 pytest
   :PROPERTIES:
   :CREATED:  [2026-09-05]
   :UPDATED:  [2026-09-05]
   :TYPE:     P
   :SCOPE:    project
   :END:

* environment
** E001 Guix 系统禁持久安装
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :UPDATED:  [2026-09-02]
   :SCOPE:    machine
   :END:

* reference
** R001 构建产物路径
   :PROPERTIES:
   :CREATED:  [2026-09-03]
   :UPDATED:  [2026-09-03]
   :END:

* deprecated
** F000 旧纠正
   :PROPERTIES:
   :CREATED:  [2026-01-01]
   :UPDATED:  [2026-01-01]
   :END:
"""


def _ctx(tmp_path, text=TEXT):
    path = tmp_path / "MEMORY.org"
    path.write_text(text, encoding="utf-8")
    return types.SimpleNamespace(memory_org=path)


def test_add_project_type_writes_memory_org_entry(tmp_path, monkeypatch):
    """--add --type P 与 import 同一写路径：MEMORY.org project 节 + PROJECT 属性，可被投影。"""
    import agenote.core as core
    from agenote import projector

    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    org = tmp_path / "MEMORY.org"
    ctx = types.SimpleNamespace(memory_org=org, root=tmp_path, name="test", is_human=True)
    args = argparse.Namespace(add=True, type="P", stdin=False, ref=None,
                              title="agenote 提交前要跑全量测试",
                              project="agenote", freshness=False)
    memory_mod._memory_add(args, ctx)
    text = org.read_text(encoding="utf-8")
    assert "** P001 agenote 提交前要跑全量测试" in text
    assert ":SCOPE:    project" in text and ":PROJECT:  agenote" in text
    # 投影器 --project agenote 能选中该条目（旧侧文件路径下这不可能）
    sel = projector._select_entries(
        argparse.Namespace(type=None, scope=None, project="agenote"),
        memory_mod._iter_memory_entries(text))
    assert [e["id"] for e in sel] == ["P001"]


def _args(**kw):
    base = {"type": None, "scope": None, "json": False, "list": True}
    base.update(kw)
    return argparse.Namespace(**base)


def test_sections_are_six():
    assert MEMORY_SECTIONS == [
        "user", "feedback", "project", "environment", "reference", "deprecated",
    ]
    sections = memory_mod._parse_memory_sections(TEXT)
    for sec in MEMORY_SECTIONS:
        assert sec in sections, f"缺节: {sec}"


def test_project_section_mixed_index_and_entry():
    entries = memory_mod._iter_memory_entries(TEXT)
    proj = [e for e in entries if e["section"] == "project"]
    by_id = {e["id"]: e for e in proj}
    assert by_id["agenote"]["kind"] == "index"
    assert by_id["agenote"]["type"] == "P"  # 无前缀索引按节推导
    assert by_id["P001"]["kind"] == "entry"
    assert by_id["P001"]["type"] == "P"


def test_type_derivation_priority():
    entries = memory_mod._iter_memory_entries(TEXT)
    by_id = {e["id"]: e for e in entries}
    assert by_id["U001"]["type"] == "U"  # 显式 :TYPE:
    assert by_id["F001"]["type"] == "F"  # 存量无 TYPE 按前缀推导
    assert by_id["R001"]["type"] == "R"
    assert by_id["E001"]["type"] == "E"  # 前缀推导（无显式 TYPE）
    # 显式 TYPE 优先于前缀
    tricky = TEXT.replace("** F001 不要用 cat", "** F009 错位条目").replace(
        ":VALIDATED_AT: [2026-09-10]", ":TYPE: E", 1,
    )
    by_id2 = {e["id"]: e for e in memory_mod._iter_memory_entries(tricky)}
    assert by_id2["F009"]["type"] == "E"


def test_origin_id_stable():
    a = memory_mod.origin_id("zcode", "a/b.org", "标题")
    assert a == memory_mod.origin_id("zcode", "a/b.org", "标题")
    assert len(a) == 16
    assert memory_mod.origin_id("zcode", "a/b.org", "标题2") != a
    assert memory_mod.origin_id("claude", "a/b.org", "标题") != a


def test_list_filter_by_type(tmp_path, capsys):
    memory_mod._memory_list(_args(type="F"), ctx=_ctx(tmp_path))
    out = capsys.readouterr().out
    assert "F001" in out and "F000" in out  # deprecated 节条目同样列出
    assert "U001" not in out and "P001" not in out


def test_list_filter_by_scope_and_json(tmp_path, capsys):
    memory_mod._memory_list(_args(scope="machine"), ctx=_ctx(tmp_path))
    out = capsys.readouterr().out
    assert "E001" in out and "U001" not in out

    memory_mod._memory_list(_args(type="P", json=True), ctx=_ctx(tmp_path))
    rows = json.loads(capsys.readouterr().out)
    assert {r["id"] for r in rows} == {"agenote", "P001"}
    kinds = {r["id"]: r["kind"] for r in rows}
    assert kinds == {"agenote": "index", "P001": "entry"}


def test_list_hook_and_validated(tmp_path, capsys):
    memory_mod._memory_list(_args(), ctx=_ctx(tmp_path))
    out = capsys.readouterr().out
    assert "# 用户要求所有回复使用中文" in out  # 钩子行
    assert "validated=2026-09-10" in out  # VALIDATED_AT 时效

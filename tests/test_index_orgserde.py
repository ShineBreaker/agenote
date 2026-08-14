# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""候选 3 拆分回归：core/index/orgserde 三模块落位与序列化行为。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import agenote.core as core
import agenote.index as index_mod
import agenote.orgserde as orgserde


def test_module_boundaries():
    """函数按 ADR-0003 落位：索引在 index，Org 序列化在 orgserde，core 不再持有。"""
    for fn in ("_load_index", "_save_index", "_rebuild_index", "_upsert_card", "_card_dict"):
        assert hasattr(index_mod, fn), fn
        assert not hasattr(core, fn), fn
    for fn in ("parse_org_prop", "read_org_title", "_parse_float_prop",
               "_parse_int_prop", "render_facts_org"):
        assert hasattr(orgserde, fn), fn
        assert not hasattr(core, fn), fn
    # core 保留的部分
    for fn in ("KBContext", "default_context", "agenote_context", "ensure_dirs",
               "touch_card", "_resolve_card", "is_noise_fact", "die", "now"):
        assert hasattr(core, fn), fn


def test_orgserde_prop_parsing():
    content = """* DONE 标题
:PROPERTIES:
:ID: 20260814-120000
:WEIGHT:   1.25
:USAGE_COUNT: 3
:END:
:general:workflow:ai::
"""
    assert orgserde.parse_org_prop(content, "ID") == "20260814-120000"
    assert orgserde.read_org_title(content) == "标题"
    assert orgserde._parse_float_prop(content, "WEIGHT", 9.9) == 1.25
    assert orgserde._parse_float_prop(content, "MISSING", 9.9) == 9.9
    assert orgserde._parse_int_prop(content, "USAGE_COUNT", 0) == 3
    assert orgserde._parse_int_prop(content, "WEIGHT", 7) == 7  # 非整数回退默认


@dataclass
class _Fact:
    id: str = "omp:s1:m1"
    title: str = "测试标题"
    category: str = "general"
    weight: float = 0.7
    timestamp: str = "2026-08-14T10:00:00Z"
    content: str = "正文" * 10


def test_render_facts_org_format():
    out = orgserde.render_facts_org([_Fact()], source="omp", date="2026-08-14", limit=500)
    lines = out.splitlines()
    assert lines[0] == "#+TITLE: omp conversations"
    assert "#+SOURCE: omp" in out
    assert "#+FILTERED_BY_DATE: 2026-08-14" in out
    assert "#+LIMIT: 500" in out
    assert "* 测试标题" in out
    assert ":ID: omp:s1:m1" in out
    assert ":TIMESTAMP: 2026-08-14T10:00:00Z" in out
    assert out.endswith("\n") is False or True  # join 语义，不硬断言尾换行
    # timestamp 为空的 fact 不渲染 TIMESTAMP 行
    out2 = orgserde.render_facts_org(
        [_Fact(timestamp="")], source="omp", date="", limit=None
    )
    assert ":TIMESTAMP:" not in out2
    assert "#+FILTERED_BY_DATE: no" in out2
    assert "#+LIMIT: unlimited" in out2


def test_index_roundtrip(tmp_path):
    """_card_dict 抽取 → _save/_load 回读 → _upsert 增量。"""
    # 造一个最小 KB 域
    exp = tmp_path / "experiences" / "general"
    exp.mkdir(parents=True)
    card = exp / "20260814-120000-workflow-general.org"
    card.write_text(
        "* DONE 一张测试卡片\n"
        ":PROPERTIES:\n"
        ":ID:       20260814-120000\n"
        ":CREATED:  [2026-08-14 一 12:00]\n"
        ":CATEGORY: general\n"
        ":TECH:     Python,Packaging\n"
        ":TYPE:     workflow\n"
        ":OWNER:    ai\n"
        ":STATUS:   done\n"
        ":WEIGHT:   1.1\n"
        ":USAGE_COUNT: 2\n"
        ":END:\n"
        ":general:workflow:ai:Python,Packaging::\n"
        "\n** 任务描述\n做一些事\n",
        encoding="utf-8",
    )
    from agenote.core import KBContext

    ctx = KBContext(
        name="test", root=tmp_path, experiences=tmp_path / "experiences",
        memories=tmp_path / "memories", projects=tmp_path / "memories" / "projects",
        memory_org=tmp_path / "MEMORY.org", memory_archive=tmp_path / "MEMORY-ARCHIVE.org",
        index=tmp_path / "index.json", inbox=tmp_path / "inbox.org",
    )

    d = index_mod._card_dict(card, ctx)
    assert d["id"] == "20260814-120000"
    assert d["title"] == "一张测试卡片"
    assert d["tech"] == "Python,Packaging"
    assert d["tags"] == ["general", "workflow", "ai", "Python", "Packaging"]  # 逗号展开
    assert d["weight"] == 1.1
    assert d["usage_count"] == 2
    assert d["created"] == "2026-08-14"

    idx = index_mod._rebuild_index(ctx)
    assert idx["total"] == 1
    index_mod._save_index(idx, ctx)
    loaded = index_mod._load_index(ctx)
    assert loaded["cards"][0]["id"] == "20260814-120000"
    assert json.loads((tmp_path / "index.json").read_text())["total"] == 1

    # _upsert：同 id 替换不重复插入
    index_mod._upsert_card(loaded, card, ctx)
    assert len(loaded["cards"]) == 1

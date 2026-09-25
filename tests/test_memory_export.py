# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""N3 export 投影器 + N4 supersede 裁决。"""

from __future__ import annotations

import argparse
import json
import types
from pathlib import Path

import pytest

import agenote.memory as memory_mod
from agenote import projector

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
   :END:
** F002 用 rg 不用 grep
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :UPDATED:  [2026-09-02]
   :END:

* project
** agenote
   :PROPERTIES:
   :PATH:     /home/u/Projects/agenote
   :FILE:     /home/u/Projects/agenote
   :UPDATED:  [2026-09-20]
   :END:

* environment
** E001 遗留 agent 条目
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :UPDATED:  [2026-09-02]
   :ORIGIN_AGENT: zcode
   :ORIGIN_PATH: /nonexistent-host-file.org
   :SCOPE:    machine
   :END:
** E002 现存 agent 条目
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :UPDATED:  [2026-09-02]
   :ORIGIN_AGENT: zcode
   :ORIGIN_PATH: {real}
   :SCOPE:    machine
   :END:

* deprecated
** F000 旧纠正
   :PROPERTIES:
   :CREATED:  [2026-01-01]
   :UPDATED:  [2026-01-01]
   :END:
"""


@pytest.fixture
def kb_ctx(tmp_path, monkeypatch):
    """KB_ROOT 指到 tmp，让 atomic_write containment 放行；targets 走 env 指向 tmp。"""
    import agenote.core as core

    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    real = tmp_path / "src.org"
    real.write_text("x", encoding="utf-8")
    mem = tmp_path / "MEMORY.org"
    mem.write_text(TEXT.format(real=real), encoding="utf-8")
    monkeypatch.setenv("AGENOTE_ZCODE_DIR", str(tmp_path / "zcode"))
    monkeypatch.setenv("AGENOTE_CLAUDE_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("AGENOTE_CODEX_SUGGEST_DIR", str(tmp_path / "codex"))
    return types.SimpleNamespace(memory_org=mem, root=tmp_path)


def _export_args(**kw):
    base = {"type": None, "scope": None, "project": None, "export": True}
    base.update(kw)
    return argparse.Namespace(**base)


def test_export_aggregate_and_marker(kb_ctx, capsys):
    projector.cmd_export(_export_args(), kb_ctx)
    for host in ("zcode", "claude"):
        agg = kb_ctx.root / host / "agenote-profile.md"
        text = agg.read_text(encoding="utf-8")
        assert "type: project" in text
        assert projector.MARKER_KEY + ":" in text
        assert "U001" in text and "F001" in text and "E001" in text
        assert "F000" not in text  # deprecated 不投影
        assert "P001" not in text and "** agenote" not in text  # P 无 --project 排除
        idx = kb_ctx.root / host / "MEMORY.md"
        assert projector.POINTER_MARK in idx.read_text(encoding="utf-8")
    state = json.loads((kb_ctx.root / ".memory-export.json").read_text(encoding="utf-8"))
    assert state["targets"]["zcode"]["content_hash"]
    assert state["machine_key"]


def test_export_idempotent_no_rewrite(kb_ctx):
    projector.cmd_export(_export_args(), kb_ctx)
    agg = kb_ctx.root / "zcode" / "agenote-profile.md"
    mtime = agg.stat().st_mtime_ns
    projector.cmd_export(_export_args(), kb_ctx)
    assert agg.stat().st_mtime_ns == mtime


def test_export_drift_no_overwrite(kb_ctx, capsys):
    projector.cmd_export(_export_args(), kb_ctx)
    agg = kb_ctx.root / "zcode" / "agenote-profile.md"
    agg.write_text(agg.read_text(encoding="utf-8") + "\n宿主手写行\n", encoding="utf-8")
    projector.cmd_export(_export_args(), kb_ctx)
    out = capsys.readouterr().out
    assert "漂移" in out
    assert "宿主手写行" in agg.read_text(encoding="utf-8")


def test_export_codex_suggest_only(kb_ctx):
    projector.cmd_export(_export_args(), kb_ctx)
    suggest = kb_ctx.root / "codex" / "agenote-suggestions.md"
    assert suggest.exists() and "F001" in suggest.read_text(encoding="utf-8")
    assert list((kb_ctx.root / "codex").glob("*.md")) == [suggest]


def test_export_skips_project_index_rows(kb_ctx):
    """project 索引行（PATH 指针）不投影——即使 --project 命中它。"""
    projector.cmd_export(_export_args(project="agenote"), kb_ctx)
    text = (kb_ctx.root / "zcode" / "agenote-profile.md").read_text(encoding="utf-8")
    assert "** agenote" not in text


def test_export_idempotent_across_days(kb_ctx, monkeypatch):
    """聚合文件无生成日期戳：跨天重投影字节级幂等（宿主 git 无每日噪声）。"""
    projector.cmd_export(_export_args(), kb_ctx)
    agg = kb_ctx.root / "zcode" / "agenote-profile.md"
    mtime = agg.stat().st_mtime_ns
    monkeypatch.setattr(projector, "today", lambda: "2030-01-01")
    projector.cmd_export(_export_args(), kb_ctx)
    assert agg.stat().st_mtime_ns == mtime


def test_export_freshness_unified_semantics(kb_ctx):
    """时效口径统一：无 VALIDATED_AT 回退 UPDATED/CREATED——新导入条目不立刻 unverified。"""
    from datetime import datetime, timedelta

    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    mem = kb_ctx.memory_org
    mem.write_text(
        "#+title: MEMORY-test\n\n* user\n"
        f"** U001 新导入条目\n   :PROPERTIES:\n"
        f"   :CREATED:  [{yesterday}]\n   :UPDATED:  [{yesterday}]\n   :END:\n"
        "** U002 陈年未验证\n   :PROPERTIES:\n"
        "   :CREATED:  [2020-01-01]\n   :UPDATED:  [2020-01-01]\n   :END:\n",
        encoding="utf-8")
    projector.cmd_export(_export_args(type="U"), kb_ctx)
    text = (kb_ctx.root / "zcode" / "agenote-profile.md").read_text(encoding="utf-8")
    u001 = next(ln for ln in text.split("\n") if "U001" in ln)
    u002 = next(ln for ln in text.split("\n") if "U002" in ln)
    assert "(unverified" not in u001  # CREATED=昨天，统一口径下新鲜
    assert "(unverified" in u002


def test_export_secret_gate(kb_ctx):
    mem = kb_ctx.memory_org
    mem.write_text(mem.read_text(encoding="utf-8").replace(
        "** F002 用 rg 不用 grep", "** F002 密钥 sk-ant-abc123XYZ456"), encoding="utf-8")
    with pytest.raises(SystemExit):
        projector.cmd_export(_export_args(), kb_ctx)


def test_supersede_bilateral(kb_ctx):
    import re

    memory_mod._memory_supersede("F002", "F001", kb_ctx)
    text = kb_ctx.memory_org.read_text(encoding="utf-8")
    assert re.search(r":SUPERSEDES:\s+F001", text)
    assert re.search(r":SUPERSEDED_BY:\s+F002", text)
    dep = text.split("* deprecated")[1]
    assert "F001" in dep and "SUPERSEDED_BY" in dep
    assert "** F002" in text.split("* deprecated")[0]  # 新条目留原节


def test_list_orphan_mark(kb_ctx, capsys):
    args = argparse.Namespace(type="E", scope=None, json=True, list=True)
    memory_mod._memory_list(args, ctx=kb_ctx)
    out = capsys.readouterr().out
    rows = {r["id"]: r for r in json.loads(out)}
    assert rows["E001"]["orphan"] is True
    assert rows["E002"]["orphan"] is False

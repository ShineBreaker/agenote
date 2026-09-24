# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""S2 sweep 双键判龄 + dry-run 只读；S3 touch --session 幂等。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

import pytest

import agenote.cards as cards_mod
from agenote import core
from agenote import index as index_mod
from agenote.core import KBContext
from agenote.orgserde import parse_org_prop

OLD = "[2026-01-01 Thu 00:00]"
FRESH = datetime.now().strftime("[%Y-%m-%d %a %H:%M]")


@pytest.fixture
def kb(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    exp = tmp_path / "experiences" / "general"
    exp.mkdir(parents=True)
    ctx = KBContext(
        name="test", root=tmp_path, experiences=tmp_path / "experiences",
        memories=tmp_path / "memories", projects=tmp_path / "memories" / "projects",
        memory_org=tmp_path / "MEMORY.org", memory_archive=tmp_path / "MEMORY-ARCHIVE.org",
        index=tmp_path / "index.json", inbox=tmp_path / "inbox.org",
    )
    return ctx


def _write(ctx, cid, status, last_used=None, last_verified=None, usage=0):
    props = [
        "* NOTE 测试卡片",
        ":PROPERTIES:",
        f":ID:       {cid}",
        ":CREATED:  [2026-01-01 Thu 00:00]",
        ":CATEGORY: general",
        ":TYPE:     note",
        ":OWNER:    ai",
        f":STATUS:   {status}",
        f":USAGE_COUNT: {usage}",
    ]
    if last_used:
        props.append(f":LAST_USED: {last_used}")
    if last_verified:
        props.append(f":LAST_VERIFIED: {last_verified}")
    props.append(":END:")
    path = ctx.experiences / "general" / f"{cid}-note-general.org"
    path.write_text("\n".join(props) + "\n\n正文\n", encoding="utf-8")
    return path


def _reindex(ctx):
    index_mod._save_index(index_mod._rebuild_index(ctx), ctx)


def _args(**kw):
    return argparse.Namespace(**kw)


# ── S2 ──

def _sweep_ctx(kb):
    _write(kb, "20260101-000001", "done", last_used=OLD)                    # done 候选
    _write(kb, "20260101-000002", "done", last_used=FRESH)                  # 新鲜 → 排除
    _write(kb, "20260101-000003", "stable", last_used=OLD, last_verified=OLD)  # stable 候选
    _write(kb, "20260101-000004", "stable", last_used=OLD, last_verified=FRESH)  # 刚复核 → 排除
    _reindex(kb)


def test_sweep_dual_key_aging(kb, capsys):
    _sweep_ctx(kb)
    cards_mod.cmd_sweep(_args(apply=False, json=True), kb)
    out = json.loads(capsys.readouterr().out)
    assert [c["id"] for c in out["done"]] == ["20260101-000001"]
    assert [c["id"] for c in out["stable"]] == ["20260101-000003"]


def test_sweep_dry_run_writes_nothing(kb, capsys):
    _sweep_ctx(kb)
    before = {p: p.read_bytes() for p in (kb.experiences / "general").glob("*.org")}
    before_index = kb.index.read_bytes()
    cards_mod.cmd_sweep(_args(apply=False, json=False), kb)
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "20260101-000001" in out and "20260101-000003" in out
    assert {p: p.read_bytes() for p in (kb.experiences / "general").glob("*.org")} == before
    assert kb.index.read_bytes() == before_index


def test_sweep_apply_demotes_and_refreshes_verified(kb, capsys):
    _sweep_ctx(kb)
    cards_mod.cmd_sweep(_args(apply=True, json=False), kb)
    out = capsys.readouterr().out
    assert "已降级 2 张" in out
    today = datetime.now().strftime("%Y-%m-%d")
    for cid in ("20260101-000001", "20260101-000003"):
        content = (kb.experiences / "general" / f"{cid}-note-general.org").read_text(encoding="utf-8")
        assert parse_org_prop(content, "STATUS") == "stale"
        assert today in (parse_org_prop(content, "LAST_VERIFIED") or "")
    untouched = (kb.experiences / "general" / "20260101-000002-note-general.org").read_text(encoding="utf-8")
    assert parse_org_prop(untouched, "STATUS") == "done"


# ── S3 ──

def test_touch_same_session_counts_once(kb, monkeypatch, capsys):
    path = _write(kb, "20260101-000010", "done", last_used=OLD, usage=0)
    _reindex(kb)
    stamps = iter(["2026-03-01 Sat 00:00", "2026-06-01 Sun 00:00"])
    monkeypatch.setattr(core, "now", lambda: next(stamps, "2026-06-01 Sun 00:00"))
    for _ in range(2):
        cards_mod.cmd_touch(_args(target=path.name, used_only=True, session="s1"), kb)
        capsys.readouterr()
    content = path.read_text(encoding="utf-8")
    assert parse_org_prop(content, "USAGE_COUNT") == "1"  # 两次同 session 只 +1
    assert "2026-06-01" in (parse_org_prop(content, "LAST_USED") or "")  # 第二次仍刷时间戳
    # 不带 session 保持旧语义：每次 +1
    cards_mod.cmd_touch(_args(target=path.name, used_only=True, session=None), kb)
    capsys.readouterr()
    assert parse_org_prop(path.read_text(encoding="utf-8"), "USAGE_COUNT") == "2"


def test_touch_session_record_corrupt_still_counts(kb, capsys):
    path = _write(kb, "20260101-000011", "done", last_used=OLD, usage=0)
    _reindex(kb)
    (kb.root / ".touch-sessions.json").write_text("garbage{{{", encoding="utf-8")
    cards_mod.cmd_touch(_args(target=path.name, used_only=True, session="s9"), kb)
    capsys.readouterr()
    assert parse_org_prop(path.read_text(encoding="utf-8"), "USAGE_COUNT") == "1"  # 不少计

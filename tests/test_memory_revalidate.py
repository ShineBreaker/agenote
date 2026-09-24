# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""N5 事件驱动重验 + S7 --list 时效：machine 批量、手填过期、validate 刷时间、--stale 不变。"""

from __future__ import annotations

import argparse
import types
from datetime import datetime, timedelta

import pytest

import agenote.memory as memory_mod
from agenote import core as core_mod
from agenote.core import today

OLD = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
PAST = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")
FUTURE = (datetime.now() + timedelta(days=30)).strftime("%Y-%m-%d")

TEXT = (
    "* environment\n"
    "** E001 旧机器配置\n"
    "   :PROPERTIES:\n"
    "   :CREATED:  [2026-09-02]\n"
    "   :UPDATED:  [2026-09-02]\n"
    "   :TYPE:     E\n"
    "   :SCOPE:    machine\n"
    "   :MACHINE:  host-a\n"
    "   :END:\n"
    "** E002 本机配置\n"
    "   :PROPERTIES:\n"
    "   :CREATED:  [2026-09-02]\n"
    "   :UPDATED:  [2026-09-02]\n"
    "   :TYPE:     E\n"
    "   :SCOPE:    machine\n"
    "   :MACHINE:  host-b\n"
    "   :END:\n"
    "\n"
    "* feedback\n"
    f"** F001 手填已过期\n"
    "   :PROPERTIES:\n"
    "   :CREATED:  [2026-09-01]\n"
    "   :UPDATED:  [2026-09-10]\n"
    f"   :EXPIRES_AFTER:  [{PAST}]\n"
    "   :END:\n"
    "** F002 手填未过期\n"
    "   :PROPERTIES:\n"
    "   :CREATED:  [2026-09-01]\n"
    "   :UPDATED:  [2026-09-10]\n"
    f"   :EXPIRES_AFTER:  [{FUTURE}]\n"
    "   :END:\n"
    "** F003 无过期字段\n"
    "   :PROPERTIES:\n"
    "   :CREATED:  [2026-09-01]\n"
    "   :UPDATED:  [2026-09-10]\n"
    "   :END:\n"
    "\n"
    "* project\n"
    "** ghost-proj\n"
    "   :PROPERTIES:\n"
    f"   :PATH:     /nonexistent-agenote-test-xyz\n"
    f"   :UPDATED:  [{OLD}]\n"
    "   :END:\n"
)


def _ctx(tmp_path, text=TEXT):
    path = tmp_path / "MEMORY.org"
    path.write_text(text, encoding="utf-8")
    return types.SimpleNamespace(memory_org=path)


def test_machine_change_lists_batch(monkeypatch, tmp_path, capsys):
    """切机后全部异机 :MACHINE: 的 E 条目批量标 machine-changed，本机条目不标。"""
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "host-b")
    memory_mod._memory_revalidate(ctx=_ctx(tmp_path))
    out = capsys.readouterr().out
    assert "E001" in out and "machine-changed" in out
    assert "E002" not in out  # MACHINE 与当前键一致，不待重验
    assert "共 3 条待重验" in out  # E001 + F001 + ghost-proj


def test_handfilled_expiry(monkeypatch, tmp_path, capsys):
    """手填 EXPIRES_AFTER 已过 → expired；未过/缺失 → 不报。"""
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "host-a")  # E 条目全部同机，不干扰
    memory_mod._memory_revalidate(ctx=_ctx(tmp_path))
    out = capsys.readouterr().out
    assert "F001" in out and "expired" in out
    assert "F002" not in out
    assert "F003" not in out


def test_validate_refreshes_time(monkeypatch, tmp_path, capsys):
    """--validate 给无 VALIDATED_AT 条目补上 [today]，已有则刷新；失败提示 supersede/归档。"""
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "host-a")
    monkeypatch.setattr(core_mod, "KB_ROOT", tmp_path)
    ctx = _ctx(tmp_path)
    memory_mod._memory_validate("F003", ctx=ctx)
    assert f":VALIDATED_AT:  [{today()}]" in ctx.memory_org.read_text(encoding="utf-8")
    capsys.readouterr()
    # 重验后 F003 不再因过期出现（它本就无过期），且 validate 是 MUTATING 可重复跑
    memory_mod._memory_validate("F003", ctx=ctx)
    assert ctx.memory_org.read_text(encoding="utf-8").count(":VALIDATED_AT:") == 1
    with pytest.raises(SystemExit):
        memory_mod._memory_validate("F404", ctx=ctx)
    assert "supersede" in capsys.readouterr().err


def test_stale_output_unchanged(monkeypatch, tmp_path, capsys):
    """--stale 输出格式不变，且 --revalidate 是纯只读（不改文件）。"""
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "host-b")
    ctx = _ctx(tmp_path)
    before = ctx.memory_org.read_text(encoding="utf-8")
    memory_mod._memory_revalidate(ctx=ctx)
    capsys.readouterr()
    assert ctx.memory_org.read_text(encoding="utf-8") == before
    memory_mod._memory_stale(ctx=ctx)
    out = capsys.readouterr().out
    assert "(更新于 " in out and "天未更新" in out  # 旧格式原样保留
    assert "machine-changed" not in out and "expired" not in out  # 互不侵入


def test_list_freshness_opt_in(monkeypatch, tmp_path, capsys):
    """--list 默认无时效标记；--freshness 开启后超阈值条目带 (unverified Nd)。"""
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "host-a")
    ctx = _ctx(tmp_path)
    args = argparse.Namespace(type=None, scope=None, json=False, list=True)
    memory_mod._memory_list(args, ctx)
    assert "(unverified" not in capsys.readouterr().out
    args = argparse.Namespace(
        type=None, scope=None, json=False, list=True, freshness=True
    )
    memory_mod._memory_list(args, ctx)
    out = capsys.readouterr().out
    assert "(unverified 200d)" in out  # ghost-proj 索引 UPDATED=200 天前，超默认 30 天

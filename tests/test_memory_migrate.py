# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""C7 一次性迁移（memory --migrate）：存量 ORIGIN_ID 重算为相对源根派生。"""

from __future__ import annotations

import argparse
import re
import types

import agenote.memory as memory_mod
from agenote.memory import origin_id, origin_id_legacy

TITLE = "代码评审要跑完整测试套件"
SRC_REL = "projects/p/memory/code-review.md"


def _setup(tmp_path, monkeypatch, stored_id: str) -> types.SimpleNamespace:
    import agenote.core as core

    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    src_root = tmp_path / "srcmem"
    memdir = src_root / "projects/p/memory"
    memdir.mkdir(parents=True, exist_ok=True)
    (memdir / "code-review.md").write_text(
        f"---\nname: {TITLE}\n---\n正文内容足够长不会被噪声过滤器拦截。\n", encoding="utf-8")
    monkeypatch.setenv("ZCODE_MEMORIES_DIR", str(src_root))
    text = (
        "#+title: MEMORY-test\n\n* feedback\n"
        f"** F001 {TITLE}\n   :PROPERTIES:\n   :CREATED:  [2026-09-01]\n"
        f"   :ORIGIN_ID: {stored_id}\n   :ORIGIN_AGENT: zcode\n"
        f"   :ORIGIN_PATH: {memdir / 'code-review.md'}\n   :END:\n"
        f"   {TITLE} 的正文。\n")
    org = tmp_path / "MEMORY.org"
    org.write_text(text, encoding="utf-8")
    return types.SimpleNamespace(memory_org=org, root=tmp_path)


def test_migrate_rewrites_legacy_id_to_relative(tmp_path, monkeypatch, capsys):
    """旧绝对路径 ID 且出处可验证 → 重算为相对源根派生；幂等键与 import 端一致。"""
    src_root = tmp_path / "srcmem"
    legacy = origin_id_legacy("zcode", str(src_root / SRC_REL), TITLE)
    ctx = _setup(tmp_path, monkeypatch, legacy)

    memory_mod._memory_migrate(ctx)
    out = capsys.readouterr().out
    want = origin_id("zcode", SRC_REL, TITLE)
    assert f"ORIGIN_ID {legacy} → {want}" in out
    assert re.search(rf":ORIGIN_ID:\s+{want}", ctx.memory_org.read_text(encoding="utf-8"))


def test_migrate_idempotent_and_skips_unverified(tmp_path, monkeypatch, capsys):
    """已迁移/手写条目（出处不可验证）不动：重跑幂等，宁可少迁不误迁。"""
    want = origin_id("zcode", SRC_REL, TITLE)
    ctx = _setup(tmp_path, monkeypatch, want)  # 已是新算法 ID
    before = ctx.memory_org.read_text(encoding="utf-8")
    memory_mod._memory_migrate(ctx)
    assert "不符" in capsys.readouterr().out
    assert ctx.memory_org.read_text(encoding="utf-8") == before

    ctx2 = _setup(tmp_path, monkeypatch, "deadbeefdeadbeef")  # 手写 ID
    before2 = ctx2.memory_org.read_text(encoding="utf-8")
    memory_mod._memory_migrate(ctx2)
    out = capsys.readouterr().out
    assert "不符" in out and "跳过 1 条" in out
    assert ctx2.memory_org.read_text(encoding="utf-8") == before2


def test_migrate_via_cli_dispatch(tmp_path, monkeypatch, capsys):
    """--migrate 经 cmd_memory 分发（MUTATING 面内），写盘走 atomic_write。"""
    import agenote.core as core

    monkeypatch.setattr(memory_mod, "ensure_dirs", lambda ctx: None)
    src_root = tmp_path / "srcmem"
    legacy = origin_id_legacy("zcode", str(src_root / SRC_REL), TITLE)
    ctx = _setup(tmp_path, monkeypatch, legacy)
    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    memory_mod.cmd_memory(
        argparse.Namespace(migrate=True, list=False, conflicts=False, get=False,
                           export=False, add=False, do_import=False, stale=False,
                           revalidate=False, validate=None, touch=None, archive=None,
                           archive_to_file=None, project_touch=None, supersede=None,
                           dry_run=False, source="all", type=None, scope=None,
                           json=False, project=None, title=None, stdin=False,
                           freshness=False),
        ctx)
    assert "迁移完成" in capsys.readouterr().out
    assert re.search(rf":ORIGIN_ID:\s+{origin_id('zcode', SRC_REL, TITLE)}",
                     ctx.memory_org.read_text(encoding="utf-8"))

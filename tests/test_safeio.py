# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""写入安全层测试：原子写（KB containment）、KB 进程锁、add 同秒 ID 防碰撞。"""

from __future__ import annotations

import argparse
import threading

import pytest

from agenote.core import KBContext
from agenote.cards import cmd_add
from agenote.safeio import atomic_write, kb_lock


@pytest.fixture
def kb_root(tmp_path, monkeypatch):
    """把 KB_ROOT 指到 tmp 目录，让 atomic_write 的 containment 放行测试写入。"""
    root = tmp_path / "kb"
    root.mkdir()
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    return root


def _fake_ctx(root):
    """构造指向临时 KB 的 agent 域 KBContext。"""
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


def _add_args(title: str) -> argparse.Namespace:
    return argparse.Namespace(
        title=title,
        category="general",
        tech="",
        type="workflow",
        owner="ai",
        entry="note",
        summary="",
        stdin=False,
    )


# ── atomic_write ───────────────────────────────────────────────────────────────


def test_atomic_write_creates_and_replaces(kb_root):
    target = kb_root / "a.org"
    atomic_write(target, "第一版")
    assert target.read_text(encoding="utf-8") == "第一版"
    atomic_write(target, "第二版")
    assert target.read_text(encoding="utf-8") == "第二版"


def test_atomic_write_no_tmp_left(kb_root):
    target = kb_root / "a.org"
    atomic_write(target, "内容")
    assert list(target.parent.iterdir()) == [target]


def test_atomic_write_rejects_outside_kb(kb_root, tmp_path):
    """KB_ROOT 之外的路径直接拒绝（containment 最小不变量）。"""
    with pytest.raises(ValueError):
        atomic_write(tmp_path / "escape.org", "x")
    assert not (tmp_path / "escape.org").exists()


def test_atomic_write_cleans_tmp_on_failure(kb_root, monkeypatch):
    """os.replace 失败时：tmp 清理、原文件内容不损。"""
    target = kb_root / "a.org"
    atomic_write(target, "旧内容")

    def boom(src, dst):
        raise OSError("inject replace failure")

    monkeypatch.setattr("agenote.safeio.os.replace", boom)
    with pytest.raises(OSError):
        atomic_write(target, "新内容")
    assert target.read_text(encoding="utf-8") == "旧内容"
    assert list(target.parent.iterdir()) == [target]


# ── kb_lock ────────────────────────────────────────────────────────────────────


def test_kb_lock_mutual_exclusion(tmp_path):
    """A 持锁时 B 加锁阻塞；A 释放后 B 立即获得。"""
    lock = tmp_path / ".agenote.lock"
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with kb_lock(lock):
            acquired.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(timeout=5)
    # 此时持锁中：短超时加锁应失败
    with pytest.raises(TimeoutError):
        with kb_lock(lock, timeout=0.2):
            pass
    release.set()
    t.join(timeout=5)
    # 释放后可再次获得
    with kb_lock(lock, timeout=1.0):
        pass


# ── cmd_add ID 防碰撞 ──────────────────────────────────────────────────────────


def test_add_same_second_no_collision(kb_root, monkeypatch):
    """同秒两次 add：ID 追加序号，两张卡都落盘且 :ID: 不同。"""
    import agenote.cards as cards

    ctx = _fake_ctx(kb_root)
    monkeypatch.setattr(cards, "timestamp_id", lambda: "20260824-120000")
    cmd_add(_add_args("第一张"), ctx)
    cmd_add(_add_args("第二张"), ctx)
    cards_dir = ctx.experiences / "general"
    files = sorted(p.name for p in cards_dir.glob("*.org"))
    assert len(files) == 2
    # 一张原 ID、一张带序号后缀（sorted 下 '-2-' 的 ASCII 排在 '-w' 前，不假设顺序）
    assert all(f.startswith("20260824-120000-") for f in files)
    assert any(f.startswith("20260824-120000-2-") for f in files)
    # 两张卡 :ID: 属性不同（index 按 ID upsert 的前提）
    ids = [
        next(l for l in p.read_text(encoding="utf-8").splitlines() if l.startswith(":ID:"))
        for p in cards_dir.glob("*.org")
    ]
    assert len(set(ids)) == 2

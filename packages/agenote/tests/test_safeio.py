# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""写入安全层测试：原子写（KB containment）、KB 进程锁、add 同秒 ID 防碰撞。"""

from __future__ import annotations

import argparse
import os
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


# ── 锁文件位置与只读共享锁（只读文件系统 / 沙箱下的可用性）────────────────────


def test_write_lock_creates_lock_file_in_kb(tmp_path):
    """写锁文件在 KB 内：同一 KB 的所有进程天然同 inode（跨 env / 用户不分裂）。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    with kb_lock(kb / ".agenote.lock"):
        assert (kb / ".agenote.lock").exists()


def test_shared_lock_does_not_create_lock_file(tmp_path):
    """只读命令不创建锁文件（只读文件系统上 O_CREAT 会 EROFS）。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    with kb_lock(kb / ".agenote.lock", shared=True):
        pass
    assert list(kb.iterdir()) == []


@pytest.mark.skipif(os.getuid() == 0, reason="root 无视目录权限，模拟不了只读 KB")
def test_shared_lock_on_readonly_kb(tmp_path):
    """只读 KB（chmod 555、无锁文件）上共享锁免锁放行，且不写 KB。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    kb.chmod(0o555)
    try:
        with kb_lock(kb / ".agenote.lock", shared=True):
            pass
    finally:
        kb.chmod(0o755)
    assert list(kb.iterdir()) == []


def test_shared_lock_opens_existing_lock_file(tmp_path):
    """锁文件已存在时只读命令取共享锁（走真实 flock 路径，不总是免锁）。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    with kb_lock(kb / ".agenote.lock"):
        pass  # 写者创建锁文件后释放
    with kb_lock(kb / ".agenote.lock", shared=True, timeout=1.0):
        pass


def test_writer_blocks_shared_lock(tmp_path):
    """写者持独占锁时共享锁按超时失败：只读命令不会绕过正在写入的临界区。"""
    kb = tmp_path / "kb"
    kb.mkdir()
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with kb_lock(kb / ".agenote.lock"):
            acquired.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(timeout=5)
    try:
        with pytest.raises(TimeoutError):
            with kb_lock(kb / ".agenote.lock", shared=True, timeout=0.1):
                pass
    finally:
        release.set()
        t.join(timeout=5)


@pytest.mark.skipif(os.getuid() == 0, reason="root 无视目录权限，模拟不了只读 KB")
def test_write_lock_on_readonly_kb_raises(tmp_path):
    """只读 KB 上写锁无法创建锁文件：写命令本就不能工作，直接暴露 EROFS/EACCES。"""
    import errno

    kb = tmp_path / "kb"
    kb.mkdir()
    kb.chmod(0o555)
    try:
        with pytest.raises(OSError) as exc:
            with kb_lock(kb / ".agenote.lock"):
                pass
        assert exc.value.errno in (errno.EROFS, errno.EACCES)
    finally:
        kb.chmod(0o755)


def test_atomic_write_failure_reports_target_path(tmp_path, monkeypatch):
    """写入失败的报错路径是用户目标，而不是内部 .tmp- 文件名。"""
    import errno

    target = tmp_path / "kb" / "agent" / "card.org"
    target.parent.mkdir(parents=True)
    monkeypatch.setattr("agenote.core.KB_ROOT", tmp_path)

    def boom(src, dst):
        raise OSError(errno.EROFS, "Read-only file system", str(src))

    monkeypatch.setattr("agenote.safeio.os.replace", boom)
    with pytest.raises(OSError) as exc:
        atomic_write(target, "内容")
    assert exc.value.filename == str(target)
    assert ".tmp-" not in str(exc.value.filename)


# ── kb_lock 超时配置化（[safeio].lock_timeout_seconds）─────────────────────────


def _isolate_lock_config(tmp_path, monkeypatch):
    """把 config 隔离到 tmp（无配置文件 + 清进程内缓存），env 由各用例自设。"""
    from agenote import config

    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(config, "_file_config", None)
    monkeypatch.delenv("AGENOTE_LOCK_TIMEOUT_SECONDS", raising=False)


def test_kb_lock_default_timeout_unchanged(tmp_path, monkeypatch):
    """不设 env/配置时取值与旧版一致：默认 10s（断言取值路径，不真等）。"""
    import agenote.safeio as safeio

    _isolate_lock_config(tmp_path, monkeypatch)
    assert safeio._lock_timeout_from_config() == 10.0


def test_lock_timeout_env_value_and_invalid_fallback(tmp_path, monkeypatch):
    """env 数值生效；非数值 / <=0 兜底回默认（取值层 fail-open 到合法值）。"""
    import agenote.safeio as safeio

    _isolate_lock_config(tmp_path, monkeypatch)
    for raw, expected in [("0.1", 0.1), ("2", 2.0), ("abc", 10.0), ("-3", 10.0), ("0", 10.0)]:
        monkeypatch.setenv("AGENOTE_LOCK_TIMEOUT_SECONDS", raw)
        assert safeio._lock_timeout_from_config() == expected, raw


def test_lock_timeout_file_value_overrides_default(tmp_path, monkeypatch):
    """config.toml 文件值覆盖默认（float 键宽容 int；env 缺席时生效）。"""
    from agenote import config, safeio

    cfg = tmp_path / "config.toml"
    cfg.write_text("[safeio]\nlock_timeout_seconds = 0.25\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    monkeypatch.setattr(config, "_file_config", None)
    assert safeio._lock_timeout_from_config() == 0.25


def test_lock_timeout_file_zero_falls_back(tmp_path, monkeypatch):
    """文件值 0 通过类型校验（int），取值层仍兜底回默认，杜绝 0/负超时。"""
    from agenote import config, safeio

    cfg = tmp_path / "config.toml"
    cfg.write_text("[safeio]\nlock_timeout_seconds = 0\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", cfg)
    monkeypatch.setattr(config, "_file_config", None)
    assert safeio._lock_timeout_from_config() == 10.0


def test_kb_lock_contention_honors_config_timeout(tmp_path, monkeypatch):
    """真实 flock 竞争下按配置超时（0.1s）快速抛 KBLockTimeoutError，而非死等 10s。"""
    import time as time_mod

    import agenote.safeio as safeio

    _isolate_lock_config(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENOTE_LOCK_TIMEOUT_SECONDS", "0.1")
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
    start = time_mod.monotonic()
    with pytest.raises(safeio.KBLockTimeoutError):
        with kb_lock(lock):
            pass
    elapsed = time_mod.monotonic() - start
    release.set()
    t.join(timeout=5)
    # 旧默认 10s 必然越界；配置生效时 ~0.1s 即抛，给慢 CI 留裕量取 2s 上界
    assert elapsed < 2.0


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

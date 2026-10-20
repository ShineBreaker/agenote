# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""错误边界回归：kb_lock 超时的可操作提示必须穿透 CLI 边界，其余异常维持脱敏。

背景：safe_error_message 对非 PublicError 只回「操作失败（类型名）」，而 kb_lock
超时消息（等待时长 + 「另一个 agenote 进程可能正持有锁」）是代码自生成的可操作
提示，被压成类型名后用户无从排查并发冲突。
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import os
import sys
import threading

import pytest

from agenote.cards import cmd_add
from agenote.core import KBContext, PublicError, ensure_dirs, safe_error_message
from agenote.safeio import KBLockTimeoutError, kb_lock


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
        agent_name="zcode",
    )


def _add_args(title, category="general", type_="debug"):
    return argparse.Namespace(
        title=title,
        category=category,
        tech="",
        type=type_,
        owner="ai",
        entry="",
        summary="",
        stdin=False,
        force=False,
    )


def test_safe_error_message_passes_lock_timeout_hint():
    """KBLockTimeoutError 的自生成提示完整透出，不被压成类型名。"""
    exc = KBLockTimeoutError(
        "agenote KB 锁等待超时（10.0s）：/tmp/kb/.agenote.lock，"
        "另一个 agenote 进程可能正持有锁。"
    )
    msg = safe_error_message(exc)
    assert "另一个 agenote 进程" in msg
    assert "锁等待超时" in msg


def test_safe_error_message_still_masks_other_errors():
    """白名单只收锁超时：其余异常类型维持脱敏，PublicError 维持原文。"""
    # 裸 OSError（errno=None）无诊断价值，仍按类型脱敏
    assert safe_error_message(OSError("disk full /secret/path")) == "操作失败（OSError）"
    assert safe_error_message(ValueError("内部细节")) == "操作失败（ValueError）"
    assert safe_error_message(PublicError("受控错误")) == "受控错误"


def test_safe_error_message_exposes_rofs_with_path():
    """只读文件系统（沙箱 / 只读挂载）：errno 名 + 路径 + 可操作提示，不再压成裸类型名。"""
    exc = OSError(errno.EROFS, "Read-only file system", "/kb/agenote/card.org")
    msg = safe_error_message(exc)
    assert "EROFS" in msg
    assert "/kb/agenote/card.org" in msg
    assert "不在可写范围" in msg


def test_safe_error_message_exposes_eacces_with_path():
    """权限不足同样给出可操作提示（沙箱按 EACCES 拒绝时与真实权限错误同形）。"""
    exc = PermissionError(errno.EACCES, "Permission denied", "/kb/agenote/MEMORY.org")
    msg = safe_error_message(exc)
    assert "EACCES" in msg
    assert "/kb/agenote/MEMORY.org" in msg
    assert "不在可写范围" in msg


def test_safe_error_message_other_errno_keeps_path():
    """其余 errno（如 ENOSPC）带 errno 名与路径，便于定位；不转发异常正文。"""
    exc = OSError(errno.ENOSPC, "内部细节 /secret", "/kb/x.org")
    msg = safe_error_message(exc)
    assert "ENOSPC" in msg
    assert "/kb/x.org" in msg
    assert "/secret" not in msg


def test_kb_lock_timeout_raises_actionable_error(tmp_path):
    """真实 flock 竞争下 kb_lock 抛 KBLockTimeoutError（仍是 TimeoutError）。"""
    lock = tmp_path / "kb" / ".agenote.lock"
    lock.parent.mkdir(parents=True)
    acquired = threading.Event()
    release = threading.Event()

    def holder():
        with kb_lock(lock):
            acquired.set()
            release.wait(timeout=5)

    t = threading.Thread(target=holder)
    t.start()
    assert acquired.wait(timeout=5)
    try:
        with pytest.raises(KBLockTimeoutError, match="另一个 agenote 进程") as exc:
            with kb_lock(lock, timeout=0.05):
                pass
        assert isinstance(exc.value, TimeoutError)
    finally:
        release.set()
        t.join(timeout=5)


def test_lock_timeout_reaches_cli_stderr(kb_root, monkeypatch, capsys):
    """锁超时经公共 CLI 边界后 stderr 保留完整提示，不泄漏 traceback。"""
    from agenote import cli
    from agenote.safeio import kb_lock as real_kb_lock

    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = next(ctx.experiences.rglob("*.org"))
    # 锁文件在 KB 内，cli 与测试拿到同一 inode
    fd = os.open(ctx.root / ".agenote.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # 测试进程持锁，模拟并发占用
        monkeypatch.setattr(cli, "agenote_context", lambda: ctx)
        # 真实 kb_lock + 短超时：走真的 flock 竞争路径，而非伪造异常
        monkeypatch.setattr(
            cli, "kb_lock", lambda path, **kw: real_kb_lock(path, timeout=0.1)
        )
        monkeypatch.setattr(sys, "argv", ["agenote", "touch", str(card)])
        with pytest.raises(SystemExit) as exc:
            cli.main()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "另一个 agenote 进程" in err
    assert "Traceback" not in err
    # 命中白名单后不应再出现脱敏兜底消息
    assert "操作失败" not in err


def test_ensure_dirs_skips_readonly_filesystem(tmp_path, monkeypatch):
    """EROFS/EACCES：骨架自愈跳过而不抛，只读命令不因建骨架失败而挂。"""
    ctx = _ctx(tmp_path / "kb")

    def boom(self, *args, **kwargs):
        raise OSError(errno.EROFS, "Read-only file system")

    monkeypatch.setattr(type(ctx.experiences), "mkdir", boom)
    ensure_dirs(ctx)  # 不抛


def test_ensure_dirs_reraises_other_filesystem_errors(tmp_path, monkeypatch):
    """ENOSPC / ENOTDIR 等非权限故障必须照抛，不能把写失败吞成「成功」。"""
    ctx = _ctx(tmp_path / "kb")

    def boom(self, *args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(type(ctx.experiences), "mkdir", boom)
    with pytest.raises(OSError) as exc:
        ensure_dirs(ctx)
    assert exc.value.errno == errno.ENOSPC

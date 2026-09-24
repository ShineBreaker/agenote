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
import fcntl
import os
import sys

import pytest

from agenote.cards import cmd_add
from agenote.core import KBContext, PublicError, safe_error_message
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
    assert safe_error_message(OSError("disk full /secret/path")) == "操作失败（OSError）"
    assert safe_error_message(ValueError("内部细节")) == "操作失败（ValueError）"
    assert safe_error_message(PublicError("受控错误")) == "受控错误"


def test_kb_lock_timeout_raises_actionable_error(tmp_path):
    """真实 flock 竞争下 kb_lock 抛 KBLockTimeoutError（仍是 TimeoutError）。"""
    lock = tmp_path / "kb" / ".agenote.lock"
    lock.parent.mkdir(parents=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with pytest.raises(KBLockTimeoutError, match="另一个 agenote 进程") as exc:
            with kb_lock(lock, timeout=0.05):
                pass
        assert isinstance(exc.value, TimeoutError)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_lock_timeout_reaches_cli_stderr(kb_root, monkeypatch, capsys):
    """锁超时经公共 CLI 边界后 stderr 保留完整提示，不泄漏 traceback。"""
    from agenote import cli
    from agenote.safeio import kb_lock as real_kb_lock

    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = next(ctx.experiences.rglob("*.org"))
    lock_path = ctx.root / ".agenote.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)  # 测试进程持锁，模拟并发占用
        monkeypatch.setattr(cli, "agenote_context", lambda: ctx)
        # 真实 kb_lock + 短超时：走真的 flock 竞争路径，而非伪造异常
        monkeypatch.setattr(cli, "kb_lock", lambda path: real_kb_lock(path, timeout=0.1))
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

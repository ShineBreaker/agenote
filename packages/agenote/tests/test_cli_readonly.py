# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""CLI 只读调用判定：共享锁与豁免骨架自愈的依据。

只读命令在只读文件系统（沙箱 / 只读挂载）上必须可用：锁走运行时目录的共享
锁，ensure_dirs 的骨架自愈跳过。判定漏标只读 → 命令在只读 KB 上挂；
误标写命令为只读 → 骨架缺失时写失败。写命令一律按写处理。
"""

from __future__ import annotations

import argparse

from agenote.cli import _read_only_invocation, _read_only_kb_invocation


def _ns(command: str, **kw) -> argparse.Namespace:
    return argparse.Namespace(command=command, **kw)


def test_check_modes_of_mutating_commands_are_read_only():
    """MUTATING_COMMANDS 内的检查形态：lint 无 --fix / format --check / commit --dry-run。"""
    assert _read_only_invocation(_ns("lint", fix=False)) is True
    assert _read_only_invocation(_ns("lint", fix=True)) is False
    assert _read_only_invocation(_ns("format", check=True)) is True
    assert _read_only_invocation(_ns("format", check=False)) is False
    assert _read_only_invocation(_ns("commit", dry_run=True)) is True
    assert _read_only_invocation(_ns("commit", dry_run=False)) is False
    assert _read_only_invocation(_ns("add")) is False


def test_pure_read_commands_are_read_only():
    """查询类命令零写盘：共享锁 + 跳过骨架自愈。"""
    for cmd in (
        "list",
        "search",
        "fields",
        "tags",
        "stats",
        "health",
        "gaps",
        "doctor",
        "trace",
        "review",
        "deduplicate",
        "scan-memories",
    ):
        assert _read_only_kb_invocation(_ns(cmd)) is True, cmd


def test_parameterized_read_paths():
    """get 无 --used、memory 只读子动作（list/conflicts/get）、检查形态均只读。"""
    assert _read_only_kb_invocation(_ns("get", used=False)) is True
    assert _read_only_kb_invocation(_ns("get", used=True)) is False
    for kw in (
        dict(list=True, conflicts=False, get=False, export=False),
        dict(list=False, conflicts=True, get=False, export=False),
        dict(list=False, conflicts=False, get=True, export=False),
    ):
        assert _read_only_kb_invocation(_ns("memory", **kw)) is True, kw
    # export 在 AGENOTE_ROOT 内读改写 .memory-export.json，不是只读调用
    assert (
        _read_only_kb_invocation(
            _ns("memory", list=False, conflicts=False, get=False, export=True)
        )
        is False
    )
    assert (
        _read_only_kb_invocation(
            _ns("memory", list=False, conflicts=False, get=False, export=False)
        )
        is False
    )
    for cmd in ("lint",):
        assert _read_only_kb_invocation(_ns(cmd, fix=False)) is True
        assert _read_only_kb_invocation(_ns(cmd, fix=True)) is False


def test_write_and_unknown_commands_are_not_read_only():
    """写命令与未识别命令（含 dream，写游标/卡片）一律按写处理。"""
    for cmd in ("add", "update", "touch", "sweep", "merge", "archive", "restore",
                "connect", "inbox", "inbox-archive", "reindex", "distill",
                "reconcile", "extract", "viz", "dream", "init", "commit"):
        assert _read_only_kb_invocation(_ns(cmd)) is False, cmd

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
from contextlib import contextmanager
from types import SimpleNamespace

import agenote.cli as cli
from agenote.cli import _read_only_invocation, _read_only_kb_invocation
from agenote.viz.cli import _viz_domain, add_viz_parser


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
        is True
    )
    for cmd in ("lint",):
        assert _read_only_kb_invocation(_ns(cmd, fix=False)) is True
        assert _read_only_kb_invocation(_ns(cmd, fix=True)) is False


def test_memory_query_modes_are_read_only():
    """memory 的所有查询形态都跳过骨架自愈和独占锁。"""
    for kw in (
        dict(stale=True),
        dict(revalidate=True),
        dict(project="agenote"),
        dict(freshness=True),
        dict(),
    ):
        assert _read_only_kb_invocation(_ns("memory", **kw)) is True, kw
    for kw in (
        dict(add=True),
        dict(do_import=True, dry_run=False),
        dict(validate="U001"),
        dict(touch="U001"),
        dict(archive="U001"),
        dict(project_touch="agenote"),
        dict(export=True),
        dict(migrate=True),
        dict(supersede=("U002", "U001")),
    ):
        assert _read_only_kb_invocation(_ns("memory", **kw)) is False, kw


def test_pure_read_command_uses_shared_lock(monkeypatch, tmp_path):
    """纯读命令也要在已有写锁时取共享锁，避免读到半更新视图。"""
    agent = SimpleNamespace(root=tmp_path / "agent")
    captured = []

    @contextmanager
    def fake_lock(path, **kwargs):
        captured.append((path, kwargs))
        yield

    monkeypatch.setattr(cli, "agenote_context", lambda: agent)
    monkeypatch.setattr(cli, "ensure_dirs", lambda ctx: (_ for _ in ()).throw(
        AssertionError("read-only command must not ensure KB directories")
    ))
    monkeypatch.setattr(cli, "cmd_list", lambda args, ctx: None)
    monkeypatch.setattr(cli, "kb_lock", fake_lock)
    monkeypatch.setattr("sys.argv", ["agenote", "list"])

    cli.main()

    assert captured == [(agent.root / ".agenote.lock", {"shared": True})]


def test_human_domain_write_uses_human_lock(monkeypatch, tmp_path):
    """--domain human 的写操作必须和 human KB 共用锁，而不是锁 agent KB。"""
    human = SimpleNamespace(
        root=tmp_path / "human", name="human", experiences=tmp_path / "human" / "experiences",
        memories=tmp_path / "human" / "memories", projects=tmp_path / "human" / "projects",
        memory_org=tmp_path / "human" / "MEMORY.org", memory_archive=tmp_path / "human" / "MEMORY-ARCHIVE.org",
        index=tmp_path / "human" / "index.json", inbox=tmp_path / "human" / "inbox.org",
        is_human=True, default_weight=1.5, agent_name="",
    )
    agent = SimpleNamespace(root=tmp_path / "agent")
    captured = []

    @contextmanager
    def fake_lock(path, **kwargs):
        captured.append(path)
        yield

    monkeypatch.setattr(cli, "default_context", lambda: human)
    monkeypatch.setattr(cli, "agenote_context", lambda: agent)
    monkeypatch.setattr(cli, "ensure_dirs", lambda ctx: None)
    monkeypatch.setattr(cli, "cmd_touch", lambda args, ctx: None)
    monkeypatch.setattr(cli, "kb_lock", fake_lock)
    monkeypatch.setattr("sys.argv", ["agenote", "--domain", "human", "touch", "missing"])

    cli.main()

    assert captured == [human.root / ".agenote.lock"]


def test_shim_health_uses_readonly_shared_lock(monkeypatch, tmp_path):
    """轻量 shim 的 health 入口同样不建骨架，并读取已有锁的共享视图。"""
    import agenote.shim as shim

    ctx = SimpleNamespace(root=tmp_path / "agent")
    captured = []

    @contextmanager
    def fake_lock(path, **kwargs):
        captured.append((path, kwargs))
        yield

    monkeypatch.setattr(shim, "agenote_context", lambda: ctx)
    monkeypatch.setattr(shim, "cmd_health", lambda args, context: None)
    monkeypatch.setattr(shim, "kb_lock", fake_lock)
    monkeypatch.setattr("sys.argv", ["agenote-cli", "health"])

    shim._main()

    assert captured == [(ctx.root / ".agenote.lock", {"shared": True})]


def test_read_only_workflow_modes_are_read_only():
    """dry-run / 纯发现模式不应创建骨架，也不应拿写锁。"""
    assert _read_only_kb_invocation(_ns("sweep", apply=False)) is True
    assert _read_only_kb_invocation(_ns("archive", list_cards=True, stale=False)) is True
    assert _read_only_kb_invocation(_ns("archive", list_cards=False, stale=True)) is True
    assert _read_only_kb_invocation(_ns("distill")) is True
    assert _read_only_kb_invocation(_ns("reconcile", dry_run=True)) is True
    assert _read_only_kb_invocation(_ns("reconcile", dry_run=False)) is False
    assert _read_only_kb_invocation(_ns("extract", dry_run=True)) is True
    assert _read_only_kb_invocation(_ns("extract", dry_run=False)) is False


def test_viz_domain_does_not_shadow_global_domain():
    """顶层 --domain human viz 与 viz --domain human 都必须选择 human。"""
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", choices=["human", "agenote"], default="__auto__")
    subparsers = parser.add_subparsers(dest="command")
    add_viz_parser(subparsers)

    global_domain = parser.parse_args(["--domain", "human", "viz"])
    local_domain = parser.parse_args(["viz", "--domain", "human"])
    implicit = parser.parse_args(["viz"])

    assert _viz_domain(global_domain) == "human"
    assert _viz_domain(local_domain) == "human"
    assert _viz_domain(implicit) == "all"
    assert cli._lock_roots_for(global_domain, None) == sorted(
        {cli.default_context().root.resolve()}, key=str
    )


def test_write_and_unknown_commands_are_not_read_only():
    """真正写入与未识别命令仍按写处理。"""
    for cmd in ("add", "update", "touch", "sweep", "merge", "archive", "restore",
                "connect", "inbox", "inbox-archive", "reindex", "reconcile",
                "extract", "viz", "dream", "init", "commit"):
        assert _read_only_kb_invocation(_ns(cmd, apply=True, dry_run=False)) is False, cmd

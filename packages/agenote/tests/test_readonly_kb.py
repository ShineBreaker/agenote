# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""端到端：只读 KB（沙箱 / 只读挂载）上只读命令必须可用。

这是本次缺陷的真实路径：CLI dispatch → 跳过 ensure_dirs（只读调用）→ 共享锁 /
免锁（只读调用不创建锁文件）→ 命令实现 → 退出码。同时验证写命令仍以可诊断的
EROFS/EACCES 失败，而不是被吞成成功。
"""

from __future__ import annotations

import sys

import pytest

from agenote import cli

_CLEAN_CARD = """* DONE 干净卡片
:PROPERTIES:
:ID:         20260824-130000
:CATEGORY:   general
:TECH:       general
:TYPE:       debug
:OWNER:      ai
:ENTRY_TYPE: note
:STATUS:     done
:END:
:general:debug:ai:note::

测试正文
"""


def _run_cli(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["agenote", *argv])
    try:
        cli.main()
    except SystemExit as exc:  # lint --check / add 等自行退出
        return exc.code if isinstance(exc.code, int) else 1
    return 0  # list / stats 正常返回


@pytest.fixture
def readonly_kb(tmp_path, monkeypatch):
    """骨架不全（无 memories / index.json）且权限只读的 KB——最容易挂掉的形态。"""
    root = tmp_path / "kb"
    agent = root / "agenote"
    cards = agent / "experiences" / "general"
    cards.mkdir(parents=True)
    (cards / "clean.org").write_text(_CLEAN_CARD, encoding="utf-8")
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    monkeypatch.setattr("agenote.core.AGENOTE_ROOT", agent)
    writable = [root, agent, agent / "experiences", cards]
    for p in writable:
        p.chmod(0o555)
    try:
        yield root, agent
    finally:
        for p in writable:
            p.chmod(0o755)


def test_lint_check_works_on_readonly_kb(readonly_kb, monkeypatch):
    """只读 KB 上 lint --check 正常跑完（干净卡 → 0）。"""
    assert _run_cli(monkeypatch, ["lint", "--check"]) == 0
    root, _ = readonly_kb
    assert sorted(p.name for p in root.rglob("*") if p.is_file()) == ["clean.org"]


def test_query_commands_work_on_readonly_kb(readonly_kb, monkeypatch):
    """list / stats 在只读 KB 上不因骨架自愈失败而挂。"""
    assert _run_cli(monkeypatch, ["list"]) == 0
    assert _run_cli(monkeypatch, ["stats"]) == 0


def test_write_command_fails_with_actionable_error(readonly_kb, monkeypatch, capsys):
    """写命令在只读 KB 上失败，错误带 errno 与路径，且不写 KB。"""
    root, _ = readonly_kb
    assert _run_cli(monkeypatch, ["add", "--title", "只读测试"]) == 1
    err = capsys.readouterr().err
    assert "不在可写范围" in err
    assert "EROFS" in err or "EACCES" in err
    assert sorted(p.name for p in root.rglob("*") if p.is_file()) == ["clean.org"]

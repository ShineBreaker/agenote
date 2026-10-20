# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""lint --check 退出码二值化：1=有问题 / 0=干净。

旧版退「问题数」（封顶 127），127 与 shell 的 command-not-found 撞码，工具侧
把「发现问题」误判成「命令不存在」。问题数改由 stdout 汇总与 --json 报告承载。
"""

from __future__ import annotations

import argparse

import pytest

import agenote.lint as lint_mod
from agenote.core import KBContext

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

_BAD_CARD = """* DONE 坏卡片
:PROPERTIES:
:ID:       20260824-130001
:CATEGORY: general
:TECH:     general
:TYPE:     hack
:OWNER:    ai
:STATUS:   done
:END:
:general:hack:ai:general:note:extra::
"""


def _ctx(tmp_path) -> KBContext:
    root = tmp_path / "kb"
    agent = root / "agenote"
    (agent / "experiences").mkdir(parents=True)
    return KBContext(
        name="agenote",
        root=agent,
        experiences=agent / "experiences",
        memories=agent / "memories",
        projects=agent / "memories" / "projects",
        memory_org=agent / "MEMORY.org",
        memory_archive=agent / "MEMORY-ARCHIVE.org",
        index=agent / "index.json",
        inbox=agent / "inbox.org",
        is_human=False,
        default_weight=1.0,
        agent_name="zcode",
    )


def _args(files, *, check=True, fix=False):
    return argparse.Namespace(fix=fix, check=check, json=False, files=list(files))


def test_check_exits_1_when_issues(tmp_path):
    """有问题：退出码恰好 1（不是问题数、更不是 127）。"""
    ctx = _ctx(tmp_path)
    card = ctx.experiences / "bad.org"
    card.write_text(_BAD_CARD, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        lint_mod.cmd_lint(_args([str(card)]), ctx)
    assert exc.value.code == 1


def test_check_exits_0_when_clean(tmp_path, capsys):
    """干净文件：退出码 0，汇总仍打印在 stdout。"""
    ctx = _ctx(tmp_path)
    card = ctx.experiences / "clean.org"
    card.write_text(_CLEAN_CARD, encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        lint_mod.cmd_lint(_args([str(card)]), ctx)
    assert exc.value.code == 0
    assert "共 0 处" in capsys.readouterr().out


def test_without_check_does_not_exit(tmp_path):
    """不带 --check 时只报告不退出（CI 场景之外的交互用法不变）。"""
    ctx = _ctx(tmp_path)
    card = ctx.experiences / "bad.org"
    card.write_text(_BAD_CARD, encoding="utf-8")
    lint_mod.cmd_lint(_args([str(card)], check=False), ctx)  # 不应抛 SystemExit

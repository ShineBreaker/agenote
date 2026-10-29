# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""format 目标解析：卡片 ID / 文件名片段 / KB 根相对路径都要能定位到卡片。

背景：agent 按 get/touch/update 的习惯直接传卡片 ID 给 format，
旧实现只认路径，报 ENOENT（DSH 会话里因此反复重试）。
"""

from __future__ import annotations

import argparse

import pytest

from agenote.core import KBContext
from agenote.orgfmt import _resolve_format_target, cmd_format

CARD_ID = "20261020-130058"
CARD_NAME = f"{CARD_ID}-workflow-git-workflow.org"
CARD_REL = f"experiences/git-workflow/{CARD_NAME}"
CARD_BODY = """* DONE 测试卡片
:PROPERTIES:
:ID:            20261020-130058
:CREATED:       [2026-10-20 Tue 13:00]
:CATEGORY:      git-workflow
:TECH:    git
:TYPE:          workflow
:ENTRY_TYPE:    note
:STATUS:        done
:OWNER:         ai
:SOURCE_AGENT:  dsh
:LAST_USED:     [2026-10-20 Tue 13:00]
:LAST_VERIFIED: [2026-10-20 Tue 13:00]
:EFFORT:
:ADDED:         [2026-10-20]
:USAGE_COUNT:   0
:END:
:git-workflow:git:ai:note::

** 任务描述

测试正文
"""


@pytest.fixture
def ctx(tmp_path) -> KBContext:
    root = tmp_path / "kb"
    experiences = root / "agenote" / "experiences" / "git-workflow"
    experiences.mkdir(parents=True)
    (experiences / CARD_NAME).write_text(CARD_BODY, encoding="utf-8")
    return KBContext(
        name="agenote",
        root=root,
        experiences=root / "agenote" / "experiences",
        memories=root / "agenote" / "memories",
        projects=root / "agenote" / "memories" / "projects",
        memory_org=root / "agenote" / "MEMORY.org",
        memory_archive=root / "agenote" / "MEMORY-ARCHIVED.org",
        index=root / "agenote" / "index.json",
        inbox=root / "agenote" / "inbox.org",
        is_human=False,
        default_weight=1.0,
        agent_name="dsh",
    )


def test_resolves_card_id(ctx):
    """精确卡片 ID 解析到卡片文件。"""
    resolved = _resolve_format_target(CARD_ID, ctx)

    assert resolved.endswith(CARD_NAME)


def test_resolves_card_filename_fragment(ctx):
    """文件名片段解析到卡片文件。"""
    resolved = _resolve_format_target(CARD_NAME, ctx)

    assert resolved.endswith(CARD_NAME)


def test_resolves_kb_root_relative_path(ctx):
    """KB 根相对路径（experiences/<类>/<卡>.org）解析到卡片文件。"""
    resolved = _resolve_format_target(CARD_REL, ctx)

    assert resolved.endswith(CARD_NAME)


def test_unresolvable_target_kept_verbatim(ctx):
    """解析不到时原样返回，错误信息里能看到调用方传的原值。"""
    assert _resolve_format_target("不存在的卡片", ctx) == "不存在的卡片"


def test_format_by_card_id_reports_file(ctx, capsys):
    """按卡片 ID 跑 format --check 能命中文件（不再 ENOENT）。"""
    args = argparse.Namespace(files=[CARD_ID], check=True)

    cmd_format(args, ctx)

    out = capsys.readouterr().out
    assert CARD_NAME in out
    assert "ENOENT" not in out

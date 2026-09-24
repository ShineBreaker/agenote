# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""Q4 三目标投影：reasonix 直写 + pi/hermes 建议清单 + 回声排除。"""

from __future__ import annotations

import argparse
import types
from pathlib import Path

import pytest

from agenote import projector
from agenote.memory_import import _echo_prefixes, _is_echo

TEXT = """#+title: MEMORY-test

* user
** U001 回复用中文
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     U
   :SCOPE:    user
   :END:
   # 用户要求所有回复使用中文

* feedback
** F001 不要用 cat
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :UPDATED:  [2026-09-02]
   :END:
   # 用 rg 不用 grep
"""


@pytest.fixture
def q4_ctx(tmp_path, monkeypatch):
    """KB_ROOT 指到 tmp；Q4 三目标走 env 指向 tmp 内目录。"""
    import agenote.core as core

    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    mem = tmp_path / "MEMORY.org"
    mem.write_text(TEXT, encoding="utf-8")
    rx = tmp_path / "rx" / "projects" / "myproj" / "memory"
    rx.mkdir(parents=True)
    (rx / "seed.md").write_text("---\nname: seed\n---\n宿主旧条目\n", encoding="utf-8")
    monkeypatch.setenv("AGENOTE_REASONIX_DIR", str(tmp_path / "rx"))
    monkeypatch.setenv("AGENOTE_PI_SUGGEST_DIR", str(tmp_path / "pi"))
    monkeypatch.setenv("AGENOTE_HERMES_SUGGEST_DIR", str(tmp_path / "hermes"))
    # 隔离外层 shell 残留的 e2e 目标 env（空 = 不投影）
    for k in ("AGENOTE_ZCODE_DIR", "AGENOTE_CLAUDE_DIR", "AGENOTE_CODEX_SUGGEST_DIR"):
        monkeypatch.setenv(k, "")
    return types.SimpleNamespace(memory_org=mem, root=tmp_path)


def _export_args(**kw):
    base = {"type": None, "scope": None, "project": None, "export": True}
    base.update(kw)
    return argparse.Namespace(**base)


def test_reasonix_missing_slug_dies(q4_ctx, capsys):
    with pytest.raises(SystemExit):
        projector.cmd_export(_export_args(), q4_ctx)
    assert "myproj" in capsys.readouterr().err  # 报错枚举既有 slug


def test_reasonix_entry_lands(q4_ctx):
    projector.cmd_export(_export_args(project="myproj"), q4_ctx)
    memdir = q4_ctx.root / "rx" / "projects" / "myproj" / "memory"
    for mid in ("U001", "F001"):
        text = (memdir / f"agenote-{mid}.md").read_text(encoding="utf-8")
        assert "name: agenote-" + mid in text
        assert "description:" in text and "metadata:\n  type:" in text
        assert 'id: ""' in text and 'revision: ""' in text
        assert projector.MARKER_KEY + ":" in text
        first_body = text.split("---")[-1].strip().split("\n")[0]
        assert len(first_body) <= 120
    assert not (memdir / "MEMORY.md").exists()  # 绝不写索引
    assert "seed" in (memdir / "seed.md").read_text(encoding="utf-8")  # 宿主文件不动


def test_pi_suggest_list(q4_ctx):
    projector.cmd_export(_export_args(project="myproj"), q4_ctx)
    suggest = q4_ctx.root / "pi" / projector.SUGGEST_NAME
    assert "U001" in suggest.read_text(encoding="utf-8")
    assert [p.name for p in (q4_ctx.root / "pi").glob("*.md")] == [projector.SUGGEST_NAME]


def test_hermes_suggest_sections(q4_ctx):
    projector.cmd_export(_export_args(project="myproj"), q4_ctx)
    text = (q4_ctx.root / "hermes" / projector.SUGGEST_NAME).read_text(encoding="utf-8")
    assert "USER.md" in text and "MEMORY.md" in text  # 头注路由规则
    assert "\n§\n" in text  # § 切分每条一节
    for line in text.split("\n"):
        if line.strip() == "§":
            continue
        assert "§" not in line  # 正文禁切分符
    first_section = text.split("\n§\n")[0].split("\n")
    assert len(first_section[2]) <= 40  # 首句 40 字内摘要


def test_echo_prefixes_cover_q4(q4_ctx):
    prefixes = projector.export_target_prefixes()
    for sub in ("rx", "pi", "hermes"):
        assert str(q4_ctx.root / sub) in prefixes
    assert _echo_prefixes() == [Path(p) for p in prefixes]  # import 侧复用同一口径
    assert _is_echo(str(q4_ctx.root / "rx" / "projects" / "myproj" / "memory"
                        / "agenote-U001.md"), _echo_prefixes())

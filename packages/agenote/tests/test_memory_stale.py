# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""回归：`memory --stale` 不得把 deprecated 节里的条目报成陈旧。

2026-09-14：归档 3 个休眠项目记忆后，--stale 仍把它们列出来（按 UPDATED 日期
判定，不看所在节），每轮策展都会重新翻出来一遍。
"""

from __future__ import annotations

import types
from datetime import datetime, timedelta

import agenote.memory as memory_mod

_OLD = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")


def _ctx(tmp_path, text):
    path = tmp_path / "MEMORY.org"
    path.write_text(text, encoding="utf-8")
    return types.SimpleNamespace(memory_org=path)


def test_stale_skips_deprecated_section(tmp_path, capsys):
    text = (
        "* feedback\n"
        "** F999 老反馈\n"
        "   :PROPERTIES:\n"
        f"   :UPDATED:  [{_OLD}]\n"
        "   :END:\n"
        "\n"
        "* deprecated\n"
        "** 已归档项目\n"
        "   :PROPERTIES:\n"
        f"   :UPDATED:  [{_OLD}]\n"
        "   :END:\n"
    )
    memory_mod._memory_stale(ctx=_ctx(tmp_path, text))
    out = capsys.readouterr().out
    assert "F999" in out  # 旧 feedback 照常报
    assert "已归档项目" not in out  # deprecated 节不再报
    assert "共 1 条" in out

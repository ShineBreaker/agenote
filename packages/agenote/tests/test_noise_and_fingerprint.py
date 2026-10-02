# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""回归测试：extract 噪声过滤、fingerprint 口径单一真相源、search 多关键词参数。

三处都源于 2026-09-14 的 14 天跨平台回顾：
  1. extract 产物里 harness 元消息占 40~78%（TodoWrite/task-notification/system-reminder）
  2. lint 按固定 5 段校验 fingerprint，而构建器在 tech==category 时按设计省略 tech
  3. `agenote search a b`（不加引号）报 unrecognized arguments，与 help 的 `<关键词...>` 不符
"""

from __future__ import annotations

import pytest

from agenote.core import build_fingerprint_line, is_noise_fact
from agenote.extract.base import filter_noise_facts
from agenote.extract.models import ReconciledFact


def _fact(title: str, content: str) -> ReconciledFact:
    return ReconciledFact(
        id="zcode:m1",
        source="zcode",
        native_id="m1",
        title=title,
        category="general",
        content=content,
        trust_score=0.5,
        weight=0.7,
    )


# ── 1. extract 噪声过滤 ───────────────────────────────────────────────────────


def test_filter_noise_drops_harness_messages():
    facts = [
        _fact("The TodoWrite tool hasn't been used recently", "The TodoWrite tool hasn't been used recently.\n\nASSISTANT: 收到。"),
        _fact("token 过期排查", "USER: token 过期排查\n\nASSISTANT: 用 gh auth status 看。"),
        _fact("task", "<task-notification>\n<summary>done</summary>\n</task-notification>"),
        _fact("注入消息", "[OUT-OF-BAND USER MESSAGE — 直接消息]\n继续"),
        _fact("后续", "[Continuing toward your standing goal]\n继续推进"),
        _fact("x", "短"),  # 长度不足 → 噪声
    ]
    kept, dropped = filter_noise_facts(facts)
    assert [f.title for f in kept] == ["token 过期排查"]
    assert len(dropped) == 5


def test_noise_markers_cover_new_harness_patterns():
    for text in (
        "<task-notification>",
        "<subagent-message>",
        "[OUT-OF-BAND USER MESSAGE",
        "Continuing toward your standing goal",
    ):
        assert is_noise_fact({"title": text, "content": text + " " * 40}), text


# ── 2. fingerprint 口径（构建器 = 单一真相源） ────────────────────────────────

_CARD_FMT = """* DONE 卡片
:PROPERTIES:
:ID:       20260914-120000
:CATEGORY: {cat}
:TECH:     {tech}
:TYPE:     {type_}
:ENTRY_TYPE: {entry}
:OWNER:    ai
:STATUS:   done
:END:
{fp}
"""


def test_build_fingerprint_line_omits_tech_equal_category():
    assert build_fingerprint_line("guix", "debug", "ai", "guix", "mistake") == ":guix:debug:ai:mistake::"
    assert build_fingerprint_line("config", "workflow", "ai", "agent", "ascended") == ":config:workflow:ai:agent:ascended::"
    assert build_fingerprint_line("general", "note", "ai") == ":general:note:ai::"


def test_lint_accepts_fingerprint_with_omitted_tech():
    """tech == category 时构建器省略 tech，lint 不得再按 5 段报错。"""
    from agenote.lint import _check_fingerprint_fields

    card = _CARD_FMT.format(cat="guix", tech="guix", type_="debug", entry="mistake", fp=":guix:debug:ai:mistake::")
    assert _check_fingerprint_fields(card) is None

    stale = _CARD_FMT.format(cat="guix", tech="guix", type_="debug", entry="mistake", fp=":guix:debug:ai:guix:mistake::")
    issue = _check_fingerprint_fields(stale)
    assert issue and "不一致" in issue


# ── 3. search 多关键词位置参数 ────────────────────────────────────────────────


def test_search_joins_multiple_positional_keywords(monkeypatch):
    """`cmd_search` 收到 list 形态的 query（argparse nargs="+"）时合并回单串。"""
    import argparse

    import agenote.search as search_mod

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        search_mod, "_cmd_cross_domain_search", lambda args: seen.setdefault("q", args.query)
    )
    search_mod.cmd_search(
        argparse.Namespace(query=["blue", "bytecode", "缓存"], _cross_domain=True), ctx=None
    )
    assert seen["q"] == "blue bytecode 缓存"


def test_search_argparse_accepts_bare_multiple_words(monkeypatch):
    """`agenote search a b` 不得报 unrecognized arguments（此前位置参数只有一个）。"""
    import agenote.cli as cli
    import agenote.search as search_mod

    seen: dict[str, object] = {}
    monkeypatch.setattr(cli, "cmd_search", lambda args, ctx=None: seen.setdefault("q", args.query))
    monkeypatch.setattr("sys.argv", ["agenote", "search", "blue", "bytecode"])
    try:
        cli.main()
    except SystemExit:
        pass
    assert seen.get("q") == ["blue", "bytecode"]  # argparse 层已接受裸多词（不再报错）

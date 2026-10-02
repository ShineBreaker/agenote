# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""自由文本渲染契约：elisp / 插件按**原样文字**渲染，不解析。

`test_el_contract.py` 守的是 JSON 形状（list --json 的字段集）。本文件守的是
另一面：`stats` / `health` / `review` / `get` / `memory --list` 的**自由文本**。

`agenote-el` 按退出码判断成败、按 stdout 原样上屏（agenote-knowledge.el 的
browse mode、agenote-health.el 的面板都是如此），所以 CLI 改一个措辞就会静默
破坏 Emacs 侧的显示——没有报错、测试全绿，只有用户看到界面变了。这类耦合
无法靠「记得别改措辞」守住，只能靠快照。

快照里的易变部分（临时路径、绝对日期、含运行时的计数）在归一化时抹掉；其余
逐字比对。改了 CLI 文案而**不**是有意的展示层调整时，本测试会红——那时要么
同步 golden、要么改 elisp 侧。

重新生成快照（确认改动是有意的之后）::

    AGENOTE_CONTRACT_UPDATE=1 uv run pytest tests/test_contracts_text.py
"""

from __future__ import annotations

import argparse
import re

import pytest

from tests.test_el_contract import _kb

# 归一化：把不该进快照的易变片段替换成占位符。
#   tmp_path   pytest 每例一个临时目录
#   /home/...  KB_ROOT 绝对路径
#   2026-09-01 之类日期——卡片 CREATED 固定，但「距今 N 天」随运行日漂移
_NORM: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"/tmp/pytest-[^\s\"']*"), "<TMP>"),
    (re.compile(r"/home/[^\s\"']*"), "<HOME>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<DATE>"),
    (re.compile(r"\b\d+\s*天前\b"), "<AGE>"),
    (re.compile(r"\b\d+\s*days?\b"), "<AGE>"),
    (re.compile(r"\d+(\.\d+)?\s*(s|ms|秒)\b"), "<DUR>"),
)


def _normalize(text: str) -> str:
    for pat, repl in _NORM:
        text = pat.sub(repl, text)
    return text


def _snapshot_path(name: str):
    from pathlib import Path

    return Path(__file__).with_name("contracts") / f"{name}.txt"


@pytest.mark.parametrize(
    "name,invoke",
    [
        ("stats", lambda ctx, capsys: __import__(
            "agenote.cards", fromlist=["cmd_stats"]).cmd_stats(
                argparse.Namespace(), ctx)),
        ("health", lambda ctx, capsys: __import__(
            "agenote.health", fromlist=["cmd_health"]).cmd_health(
                argparse.Namespace(duplicates=False, quality=False), ctx)),
        ("review", lambda ctx, capsys: __import__(
            "agenote.curator", fromlist=["cmd_review"]).cmd_review(
                argparse.Namespace(id="20260101-000001-workflow-general"), ctx)),
        ("get", lambda ctx, capsys: __import__(
            "agenote.cards", fromlist=["cmd_get"]).cmd_get(
                argparse.Namespace(
                    target="20260101-000001-workflow-general", used=False), ctx)),
    ],
)
def test_free_text_output_contract(name, invoke, tmp_path, monkeypatch, capsys):
    """elisp 原样渲染的命令：措辞与结构逐字比对快照。"""
    ctx = _kb(tmp_path, monkeypatch)
    invoke(ctx, capsys)
    got = _normalize(capsys.readouterr().out)

    path = _snapshot_path(name)
    if __import__("os").environ.get("AGENOTE_CONTRACT_UPDATE"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got, encoding="utf-8")
        return

    assert path.exists(), (
        f"缺少快照 {path.name}。确认当前文案是有意为之后，"
        f"用 AGENOTE_CONTRACT_UPDATE=1 重跑生成本快照"
    )
    assert got == path.read_text(encoding="utf-8"), (
        f"{name} 的自由文本输出变了。elisp / 插件按原样文字渲染，"
        f"改措辞会静默破坏显示。若这是有意的展示层调整，"
        f"先同步 elisp 侧，再用 AGENOTE_CONTRACT_UPDATE=1 重生快照。"
        f"\n\n--- 期望（{path.name}）\n{path.read_text(encoding='utf-8')}"
        f"\n--- 实际\n{got}"
    )


def test_memory_list_text_contract(tmp_path, monkeypatch, capsys):
    """`memory --list` 同样被原样渲染（agenote-health.el 的记忆概览节）。"""
    import types

    import agenote.memory as memory_mod

    monkeypatch.setattr(memory_mod, "ensure_dirs", lambda ctx: None)
    (tmp_path / "MEMORY.org").write_text(
        "#+title: MEMORY-test\n\n* feedback\n** F001 旧条目\n"
        "   :PROPERTIES:\n   :CREATED:  [2020-01-01]\n"
        "   :UPDATED:  [2020-01-01]\n   :END:\n",
        encoding="utf-8",
    )
    ctx = types.SimpleNamespace(memory_org=tmp_path / "MEMORY.org")
    memory_mod.cmd_memory(
        argparse.Namespace(type=None, scope=None, json=False, list=True,
                           freshness=False), ctx)
    got = _normalize(capsys.readouterr().out)

    path = _snapshot_path("memory-list")
    if __import__("os").environ.get("AGENOTE_CONTRACT_UPDATE"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got, encoding="utf-8")
        return
    assert path.exists(), (
        f"缺少快照 {path.name}。确认后用 AGENOTE_CONTRACT_UPDATE=1 重跑生成"
    )
    assert got == path.read_text(encoding="utf-8"), (
        f"memory --list 的自由文本输出变了。\n"
        f"\n--- 期望（{path.name}）\n{path.read_text(encoding='utf-8')}"
        f"\n--- 实际\n{got}"
    )

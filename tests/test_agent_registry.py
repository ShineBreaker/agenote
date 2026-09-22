# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""known_agent 动态收录测试：种子集 ∪ index 出现过的 source_agent。

与 type 门禁（test_type_gate）对称：新 agent 首张卡只警告，写入后
index.known_agents 即收录，第二张起不再警告——无需同步改代码。
"""

from __future__ import annotations

import argparse
import json

import pytest

from agenote.cards import cmd_add
from agenote.core import KBContext, SEED_AGENTS
from agenote.index import known_agents


@pytest.fixture
def kb_root(tmp_path, monkeypatch):
    """把 KB_ROOT 指到 tmp 目录，让 atomic_write 的 containment 放行测试写入。"""
    root = tmp_path / "kb"
    root.mkdir()
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    return root


def _ctx(root, agent_name) -> KBContext:
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
        agent_name=agent_name,
    )


def _add_args(title):
    return argparse.Namespace(
        title=title,
        category="general",
        tech="",
        type=None,
        owner=None,
        entry="note",
        summary="",
        stdin=False,
        force=False,
    )


def test_known_agents_empty_kb_is_seed(kb_root):
    """空 KB：已知 agent = 种子集（无 index 时 _load_index 返回空骨架）。"""
    assert known_agents(_ctx(kb_root, "")) == SEED_AGENTS


def test_first_card_from_new_agent_warns_then_registers(kb_root, capsys):
    """新 agent 首张卡：警告提示但不阻塞；index upsert 后立即收录，第二张不警告。"""
    ctx = _ctx(kb_root, "dsh")
    assert "dsh" not in known_agents(ctx)  # 写入前：不在已知集合

    capsys.readouterr()  # 清噪声
    cmd_add(_add_args("第一张"), ctx=ctx)
    err1 = capsys.readouterr().err
    assert "首次出现" in err1  # 首张卡的警告是提示性文案（不阻塞写入）

    # 落卡即收录（cmd_add 内部 upsert 后同进程即生效）
    idx = json.loads((kb_root / "agenote" / "index.json").read_text(encoding="utf-8"))
    assert idx["cards"][0]["source_agent"] == "dsh"
    assert "dsh" in known_agents(ctx)

    # 第二张卡不再出现 source_agent 警告
    capsys.readouterr()
    cmd_add(_add_args("第二张"), ctx=ctx)
    err2 = capsys.readouterr().err
    assert "不在已知 agent" not in err2


def test_archived_card_agent_still_known(kb_root):
    """归档卡片的写入者仍是已知 agent（历史来源不因归档失联）。"""
    ctx = _ctx(kb_root, "dsh")
    cmd_add(_add_args("将被归档"), ctx=ctx)
    idx_path = kb_root / "agenote" / "index.json"
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    idx["cards"][0]["status"] = "archived"
    idx_path.write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    assert "dsh" in known_agents(ctx)

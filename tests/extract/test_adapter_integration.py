# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""薄 adapter + registry 集成测试：验证重构后 opencode/zcode 通过框架产出正确 fact。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from agenote.extract import _resolve_extractors, run_extract


def _seed_db(path: Path) -> None:
    """向 fixture DB 写入一个完整 pair。"""
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO session (id, title, directory, time_created, time_updated) "
        "VALUES ('s1', '配置 guix', '/home/proj', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, data) "
        "VALUES ('m1', 's1', '2026-01-01', ?)",
        ('{"role": "user"}',),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data) "
        "VALUES ('m1_p0', 'm1', 's1', '2026-01-01', ?)",
        ('{"type": "text", "text": "怎么配置 guix channel"}',),
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, data) "
        "VALUES ('m2', 's1', '2026-01-01', ?)",
        ('{"role": "assistant"}',),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data) "
        "VALUES ('m2_p0', 'm2', 's1', '2026-01-01', ?)",
        ('{"type": "text", "text": "在 ~/.config/guix/channels.scm 添加 channel"}',),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def opencode_db(tmp_path: Path) -> Path:
    """构造一个最小 opencode schema DB 并写入一条 pair。"""
    db = tmp_path / "opencode-stable.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, title TEXT, directory TEXT, time_created TEXT, time_updated TEXT);
        CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created TEXT, data TEXT);
        CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, time_created TEXT, data TEXT);
        """
    )
    conn.commit()
    conn.close()
    _seed_db(db)
    return db


def test_registry_includes_opencode_and_zcode():
    """@register 装饰的 opencode/zcode 出现在分发表中。"""
    ext = _resolve_extractors()
    assert "opencode" in ext
    assert "zcode" in ext


def test_opencode_adapter_produces_fact(opencode_db):
    """薄 opencode adapter 通过框架产出正确 fact（title 提取 + content）。"""
    import agenote.extract.opencode as mod

    with patch.object(mod, "OPENCODE_DB", opencode_db):
        from agenote.extract.base import SOURCES

        facts, errors = SOURCES["opencode"].extract()

    assert errors == []
    assert len(facts) == 1
    f = facts[0]
    assert f.source == "opencode"
    assert "怎么配置 guix channel" in f.content
    assert "channels.scm" in f.content


def test_zcode_categorizes_config(opencode_db):
    """zcode adapter 的 categorize 比 opencode 多一个 "config" 类别。"""
    import agenote.extract.zcode as mod

    with patch.object(mod, "ZCODE_DB", opencode_db):
        from agenote.extract.base import SOURCES

        facts, errors = SOURCES["zcode"].extract()

    assert errors == []
    assert len(facts) == 1
    # 标题含 "配置" → zcode categorize 应判为 config（opencode 判为 general）
    assert facts[0].category == "config"


def test_run_extract_dry_run_via_framework(opencode_db):
    """run_extract 走 framework registry 对 opencode 做 dry_run，报告 total_facts=1。"""
    import agenote.extract.opencode as mod

    with patch.object(mod, "OPENCODE_DB", opencode_db):
        report = run_extract(source="opencode", dry_run=True)

    assert "error" not in report
    assert report["total_facts"] == 1
    assert report["source"] == "opencode"

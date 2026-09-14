# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""回归：hermes 源必须带 timestamp，否则记忆库每天被整份重复导出。

2026-09-14 实测：hermes facts 未填 timestamp，而 run_extract 的日期过滤对空
时间戳「不过滤」（防静默丢数据），结果 117 条记忆在 14 天的 extract 产物里
各出现一次。修法是取 DB 的 updated_at（改写即算当天浮现）。
"""

from __future__ import annotations

import sqlite3

import agenote.extract.hermes as hermes_mod

_SCHEMA = """CREATE TABLE facts (
    fact_id INTEGER PRIMARY KEY, content TEXT, category TEXT, tags TEXT,
    trust_score REAL, retrieval_count INTEGER, helpful_count INTEGER,
    created_at TEXT, updated_at TEXT, hrr_vector BLOB)"""


def _db(tmp_path, rows):
    db = tmp_path / "memory_store.db"
    conn = sqlite3.connect(db)
    conn.execute(_SCHEMA)
    conn.executemany("INSERT INTO facts VALUES (?,?,?,?,?,?,?,?,?,NULL)", rows)
    conn.commit()
    conn.close()
    return db


def test_hermes_fact_carries_updated_at(tmp_path, monkeypatch):
    db = _db(tmp_path, [
        (1, "【部署拓扑】hermes 通过 Nix + Guix-configs 部署在固定路径", "tool", "hermes,guix",
         0.5, 0, 0, "2026-09-01 10:00:00", "2026-09-05 11:00:00"),
    ])
    monkeypatch.setattr(hermes_mod, "HERMES_DB", db)

    facts, errs = hermes_mod.extract_hermes()
    assert errs == []
    assert facts[0].timestamp == "2026-09-05 11:00:00"


def test_hermes_fact_falls_back_to_created_at(tmp_path, monkeypatch):
    db = _db(tmp_path, [
        (2, "【旧记忆】未改写的条目用 created_at 定日期", "tool", "",
         0.5, 0, 0, "2026-08-20 09:00:00", None),
    ])
    monkeypatch.setattr(hermes_mod, "HERMES_DB", db)

    facts, _ = hermes_mod.extract_hermes()
    assert facts[0].timestamp == "2026-08-20 09:00:00"

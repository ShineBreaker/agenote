# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""Fixture factory — 构建符合 opencode/zcode schema 的小型 SQLite DB。

Schema（与真实 ~/.local/share/opencode/opencode-stable.db 一致）：
  session(id, title, directory, time_created, time_updated)
  message(id, session_id, time_created, data JSON)
    data.role = 'user' | 'assistant'
  part(id, message_id, session_id, time_created, data JSON)
    data.type = 'text' | 'reasoning' | 'tool' | 'patch'
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


def _make_db(path: Path) -> Path:
    """创建并初始化一个符合 schema 的空 DB 文件。"""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            title TEXT,
            directory TEXT,
            time_created TEXT,
            time_updated TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            time_created TEXT,
            data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            session_id TEXT,
            time_created TEXT,
            data TEXT
        );
        """
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db_factory(tmp_path):
    """返回一个工厂函数，向 tmp_path 写入指定内容后产出 DB 路径。

    ``messages`` 每项需含 ``id``、``role``，可选 ``parts``（含 ``type``/``text``）。
    part 自动获得唯一 id，并通过 message_id 与 message 关联。

    用法::

        def test_x(db_factory):
            db = db_factory(
                session={"id": "s1", "title": "t", "directory": "/p", "time_created": "2026-01-01"},
                messages=[
                    {"id": "m1", "role": "user", "parts": [{"type": "text", "text": "hello"}]},
                    {"id": "m2", "role": "assistant", "parts": [{"type": "text", "text": "hi"}]},
                ],
            )
    """

    def _build(
        session: dict,
        messages: list[dict],
    ) -> Path:
        path = tmp_path / "test.db"
        _make_db(path)
        conn = sqlite3.connect(path)
        conn.execute(
            "INSERT INTO session (id, title, directory, time_created, time_updated) "
            "VALUES (:id, :title, :directory, :time_created, :time_updated)",
            {**session, "time_updated": session.get("time_updated", session.get("time_created", ""))},
        )
        for msg in messages:
            msg_id = msg["id"]
            conn.execute(
                "INSERT INTO message (id, session_id, time_created, data) "
                "VALUES (?, ?, ?, ?)",
                (
                    msg_id,
                    session["id"],
                    msg.get("time_created", "2026-01-01T00:00:00"),
                    json.dumps({"role": msg["role"]}),
                ),
            )
            for j, part in enumerate(msg.get("parts", [])):
                conn.execute(
                    "INSERT INTO part (id, message_id, session_id, time_created, data) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        f"{msg_id}_p{j}",
                        msg_id,
                        session["id"],
                        "2026-01-01T00:00:00",
                        json.dumps(part),
                    ),
                )
        conn.commit()
        conn.close()
        return path

    return _build

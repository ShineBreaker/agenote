# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""framework 核心单测：pair_turns / iter_turns_sqlite / run_sqlite_extractor。"""

from __future__ import annotations

from agenote.extract.base import (
    Turn,
    iter_turns_sqlite,
    pair_turns,
    run_sqlite_extractor,
)


def _turn(role: str, text: str, *, ts: str = "2026-01-01T00:00:00", nid: str = "m1") -> Turn:
    return Turn(role=role, text=text, timestamp=ts, native_id=nid, session={"id": "s1", "title": "t", "directory": "/proj"})


# ═══════════════════════════════════════════════════════════════════════════════
# pair_turns
# ═══════════════════════════════════════════════════════════════════════════════


def test_pair_turns_basic_pairing():
    """user → assistant 配对产出一条 fact，content 格式正确。"""
    turns = iter([_turn("user", "hello"), _turn("assistant", "hi there")])
    facts = list(pair_turns(turns, source="opencode", weight=0.7, categorize=lambda s, u, a: "general"))

    assert len(facts) == 1
    f = facts[0]
    assert f.source == "opencode"
    assert f.content == "USER: hello\n\nASSISTANT: hi there"
    assert f.weight == 0.7
    assert f.trust_score == 0.5
    assert f.timestamp == "2026-01-01T00:00:00"
    assert f.id.startswith("opencode:s1:")


def test_pair_turns_orphan_assistant_skipped():
    """没有前置 user 的 assistant 回合被丢弃。"""
    turns = iter([_turn("assistant", "lonely")])
    facts = list(pair_turns(turns, source="opencode", weight=0.7, categorize=lambda s, u, a: "general"))
    assert facts == []


def test_pair_turns_orphan_user_no_fact():
    """末尾孤立的 user 不产出 fact（等下一个 assistant）。"""
    turns = iter([_turn("user", "unanswered")])
    facts = list(pair_turns(turns, source="opencode", weight=0.7, categorize=lambda s, u, a: "general"))
    assert facts == []


def test_pair_turns_multiple_pairs():
    """多个 user→assistant 配对全部产出。"""
    turns = iter([
        _turn("user", "q1", nid="m1"),
        _turn("assistant", "a1", nid="m2"),
        _turn("user", "q2", nid="m3"),
        _turn("assistant", "a2", nid="m4"),
    ])
    facts = list(pair_turns(turns, source="opencode", weight=0.7, categorize=lambda s, u, a: "general"))
    assert len(facts) == 2
    assert "USER: q1" in facts[0].content
    assert "ASSISTANT: a1" in facts[0].content
    assert "USER: q2" in facts[1].content


def test_pair_turns_categorize_called_with_context():
    """categorize 回调收到 session 元数据 + user/assistant 文本。"""
    seen = []

    def cat(s, user_text, assistant_text):
        seen.append((s["id"], user_text, assistant_text))
        return "fix"

    turns = iter([_turn("user", "x"), _turn("assistant", "y")])
    facts = list(pair_turns(turns, source="opencode", weight=0.7, categorize=cat))
    assert seen == [("s1", "x", "y")]
    assert facts[0].category == "fix"


def test_pair_turns_content_truncation():
    """超长正文按 user[:1000] / assistant[:2000] 截断。"""
    big_user = "U" * 1500
    big_asst = "A" * 2500
    turns = iter([_turn("user", big_user), _turn("assistant", big_asst)])
    f = list(pair_turns(turns, source="opencode", weight=0.7, categorize=lambda s, u, a: "general"))[0]
    assert "U" * 1000 in f.content
    assert "U" * 1001 not in f.content
    assert "A" * 2000 in f.content
    assert "A" * 2001 not in f.content


def test_pair_turns_two_users_replaces():
    """连续两个 user 时后者覆盖前者（current_user 重置）。"""
    turns = iter([
        _turn("user", "first", nid="m1"),
        _turn("user", "second", nid="m2"),
        _turn("assistant", "reply", nid="m3"),
    ])
    f = list(pair_turns(turns, source="opencode", weight=0.7, categorize=lambda s, u, a: "general"))[0]
    assert "USER: second" in f.content
    assert "first" not in f.content


# ═══════════════════════════════════════════════════════════════════════════════
# iter_turns_sqlite
# ═══════════════════════════════════════════════════════════════════════════════


def test_iter_turns_sqlite_basic(db_factory):
    """单 session 单条 user 回合被正确解析为 Turn。"""
    db = db_factory(
        session={"id": "s1", "title": "修复登录", "directory": "/home/proj", "time_created": "2026-01-01"},
        messages=[{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "登录报错"}]}],
    )
    conn = _connect(db)
    sess = conn.execute("SELECT * FROM session").fetchone()
    turns = list(iter_turns_sqlite(conn, sess))
    conn.close()

    assert len(turns) == 1
    assert turns[0].role == "user"
    assert turns[0].text == "登录报错"
    assert turns[0].session["title"] == "修复登录"


def test_iter_turns_sqlite_user_only_text_parts(db_factory):
    """user 消息只取 text 类型 part（保留原 opencode 不对称行为）。"""
    db = db_factory(
        session={"id": "s1", "title": "t", "directory": "/p", "time_created": "2026-01-01"},
        messages=[{
            "id": "m1",
            "role": "user",
            "parts": [
                {"type": "text", "text": "问题"},
                {"type": "tool", "tool": "grep"},  # 应被忽略
                {"type": "patch", "files": ["a.py"]},  # 应被忽略
            ],
        }],
    )
    conn = _connect(db)
    sess = conn.execute("SELECT * FROM session").fetchone()
    turns = list(iter_turns_sqlite(conn, sess))
    conn.close()

    assert len(turns) == 1
    assert turns[0].text == "问题"
    assert "[tool" not in turns[0].text
    assert "[patch" not in turns[0].text


def test_iter_turns_sqlite_assistant_all_part_types(db_factory):
    """assistant 消息格式化全部 4 种 part 类型。"""
    db = db_factory(
        session={"id": "s1", "title": "t", "directory": "/p", "time_created": "2026-01-01"},
        messages=[{
            "id": "m1",
            "role": "assistant",
            "parts": [
                {"type": "text", "text": "答案是"},
                {"type": "reasoning", "text": "让我想想" * 50},
                {"type": "tool", "tool": "Bash"},
                {"type": "patch", "files": ["a.py", "b.py"]},
            ],
        }],
    )
    conn = _connect(db)
    sess = conn.execute("SELECT * FROM session").fetchone()
    turns = list(iter_turns_sqlite(conn, sess))
    conn.close()

    assert len(turns) == 1
    t = turns[0].text
    assert "答案是" in t
    assert "[reasoning]" in t
    assert "[tool: Bash]" in t
    assert "[patch: 2 files]" in t


def test_iter_turns_sqlite_empty_parts_skipped(db_factory):
    """无有效正文的 message 不产出 Turn。"""
    db = db_factory(
        session={"id": "s1", "title": "t", "directory": "/p", "time_created": "2026-01-01"},
        messages=[{"id": "m1", "role": "user", "parts": [{"type": "tool", "tool": "grep"}]}],
    )
    conn = _connect(db)
    sess = conn.execute("SELECT * FROM session").fetchone()
    turns = list(iter_turns_sqlite(conn, sess))
    conn.close()
    assert turns == []


def test_iter_turns_sqlite_malformed_json_skipped(db_factory):
    """data JSON 解析失败的 part 被静默跳过，不影响其它 part。"""
    db = db_factory(
        session={"id": "s1", "title": "t", "directory": "/p", "time_created": "2026-01-01"},
        messages=[{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "ok"}]}],
    )
    # 注入一条损坏的 part data
    conn = _connect(db)
    conn.execute("UPDATE part SET data = '{bad' WHERE message_id LIKE 'm1%'")
    conn.commit()
    sess = conn.execute("SELECT * FROM session").fetchone()
    # 不应抛异常，且 text part 被跳过导致无 Turn
    turns = list(iter_turns_sqlite(conn, sess))
    conn.close()
    assert turns == []


# ═══════════════════════════════════════════════════════════════════════════════
# run_sqlite_extractor
# ═══════════════════════════════════════════════════════════════════════════════


def test_run_sqlite_extractor_happy_path(db_factory):
    """完整 happy path：单 session 单 pair 产出一条 fact。"""
    db = db_factory(
        session={"id": "s1", "title": "修复登录", "directory": "/home/proj", "time_created": "2026-01-01"},
        messages=[
            {"id": "m1", "role": "user", "parts": [{"type": "text", "text": "登录报错"}]},
            {"id": "m2", "role": "assistant", "parts": [{"type": "text", "text": "检查 cookie"}]},
        ],
    )
    facts, errors = run_sqlite_extractor(db, source="opencode", weight=0.7, categorize=lambda s, u, a: "fix")

    assert len(facts) == 1
    assert errors == []
    assert facts[0].content == "USER: 登录报错\n\nASSISTANT: 检查 cookie"
    assert facts[0].category == "fix"
    assert facts[0].tags == ["proj"]


def test_run_sqlite_extractor_missing_db(tmp_path):
    """DB 不存在时返回 ([], [error])，不抛异常。"""
    facts, errors = run_sqlite_extractor(
        tmp_path / "nope.db", source="opencode", weight=0.7, categorize=lambda s, u, a: "general"
    )
    assert facts == []
    assert len(errors) == 1
    assert "不存在" in errors[0]


def test_run_sqlite_extractor_per_session_isolation(db_factory):
    """单个 session 解析失败不中断整源，错误收集到 errors。"""
    # 两个 session：第一个无 parts（正常空），第二个正常 pair
    db = db_factory(
        session={"id": "s1", "title": "空", "directory": "/p", "time_created": "2026-01-01"},
        messages=[{"id": "m1", "role": "user", "parts": [{"type": "text", "text": "hi"}]}],
    )
    # 手动注入第二个 session + pair
    conn = _connect(db)
    conn.execute(
        "INSERT INTO session (id, title, directory, time_created, time_updated) "
        "VALUES ('s2', '有内容', '/p2', '2026-01-02', '2026-01-02')"
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, data) "
        "VALUES ('s2m1', 's2', '2026-01-02', ?)",
        ('{"role": "user"}',),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data) "
        "VALUES ('s2m1_p0', 's2m1', 's2', '2026-01-02', ?)",
        ('{"type": "text", "text": "问题"}',),
    )
    conn.execute(
        "INSERT INTO message (id, session_id, time_created, data) "
        "VALUES ('s2m2', 's2', '2026-01-02', ?)",
        ('{"role": "assistant"}',),
    )
    conn.execute(
        "INSERT INTO part (id, message_id, session_id, time_created, data) "
        "VALUES ('s2m2_p0', 's2m2', 's2', '2026-01-02', ?)",
        ('{"type": "text", "text": "回答"}',),
    )
    conn.commit()
    conn.close()

    facts, errors = run_sqlite_extractor(db, source="opencode", weight=0.7, categorize=lambda s, u, a: "general")
    # s1 只有 user（无配对）→ 0 facts；s2 有 pair → 1 fact
    assert len(facts) == 1
    assert "USER: 问题" in facts[0].content
    assert errors == []


# ═══════════════════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _connect(path):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn

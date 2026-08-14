# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""阶段 1c 迁移回归：codex/claude/crush/hermes 四个 adapter + dispatch 统一。"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import agenote.extract.claude as claude_mod
import agenote.extract.codex as codex_mod
import agenote.extract.crush as crush_mod
import agenote.extract.hermes as hermes_mod
from agenote.extract.base import SOURCES, Turn, _resolve_extractors, pair_turns


# ── pair_turns 空回合语义（1c 收紧：空 text 不参与配对）──────────────────────


def _turn(role, text):
    return Turn(role=role, text=text, timestamp="", native_id="n", session={"id": "s", "title": "t", "directory": ""})


def test_pair_turns_empty_texts_skipped():
    turns = iter([
        _turn("user", ""),          # 空 user 不累积
        _turn("assistant", "reply"),
        _turn("user", "q"),
        _turn("assistant", ""),     # 空 assistant 不配对
        _turn("assistant", "real"),
    ])
    facts = list(pair_turns(turns, source="x", weight=0.7, categorize=lambda s, u, a: "general"))
    assert len(facts) == 1
    assert "real" in facts[0].content


# ── codex（时间排序 + history 索引 + 外部源 weight 0.6）─────────────────────


def test_extract_codex_timestamp_order_and_weight(tmp_path):
    home = tmp_path / "codex"
    sessions = home / "sessions" / "2026" / "08"
    sessions.mkdir(parents=True)
    events = [
        {"timestamp": "2026-08-13T10:00:02Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant",
                     "content": [{"type": "output_text", "text": "装 uv 即可"}]}},
        {"timestamp": "2026-08-13T10:00:01Z", "type": "response_item",
         "payload": {"type": "message", "role": "user",
                     "content": [{"type": "input_text", "text": "怎么装 agenote"}]}},
        {"timestamp": "2026-08-13T10:00:00Z", "type": "session_meta",
         "payload": {"id": "codex-sess-1", "cwd": "/home/u/agenote"}},
    ]
    (sessions / "rollout-uuid-1.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    (home / "history.jsonl").write_text(
        json.dumps({"session_id": "uuid-1", "text": "agenote 安装问题", "cwd": "", "ts": 1}) + "\n",
        encoding="utf-8")

    with patch.object(codex_mod, "CODEX_HOME", home), \
         patch.object(codex_mod, "HISTORY_JSONL", home / "history.jsonl"), \
         patch.object(codex_mod, "SESSIONS_ROOT", home / "sessions"):
        facts, errors = codex_mod.extract_codex()

    assert errors == [] and len(facts) == 1
    f = facts[0]
    assert f.source == "codex"
    assert f.weight == 0.6  # 外部源基准
    assert f.id == "codex:codex-sess-1:2026-08-13T10:00:02Z"  # session_meta id 优先于文件名
    assert f.tags == ["agenote"]  # session_meta cwd 优先
    assert "怎么装 agenote" in f.content


# ── claude（无 assistant 事件：tool 序列合成伪 assistant Turn）──────────────


def test_extract_claude_tool_sequence_paired(tmp_path):
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    events = [
        {"type": "user", "timestamp": "2026-08-13T10:00:01Z",
         "message": {"role": "user", "content": "帮我查一下版本"}},
        {"type": "tool_use", "timestamp": "2026-08-13T10:00:02Z",
         "tool_name": "bash", "tool_input": {"cmd": "agenote --version"}},
        {"type": "tool_result", "timestamp": "2026-08-13T10:00:03Z",
         "tool_name": "bash", "tool_output": "agenote 0.1.2"},
        {"type": "user", "timestamp": "2026-08-13T10:01:00Z",
         "message": {"role": "user", "content": "没有工具调用的提问"}},
    ]
    (transcripts / "ses_abc123.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    with patch.object(claude_mod, "CLAUDE_TRANSCRIPTS_DIR", transcripts):
        facts, errors = claude_mod.extract_claude()

    assert errors == [] and len(facts) == 1  # 第二个 user 无工具调用 → 不产 fact
    f = facts[0]
    assert f.source == "claude"
    assert f.weight == 0.6
    assert "[tool_use: bash]" in f.content
    assert "[tool_result] agenote 0.1.2" in f.content
    assert f.tags == ["claude-code"]
    assert f.id == "claude:ses_abc123:2026-08-13T10:00:02Z"


# ── crush（两表 schema + 多 DB 发现 + 内容分类）─────────────────────────────


def _make_crush_db(path, sessions_and_msgs):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sessions (id TEXT, title TEXT, created_at TEXT, updated_at TEXT)")
    conn.execute("CREATE TABLE messages (id TEXT, session_id TEXT, role TEXT, parts TEXT, created_at TEXT)")
    for sid, title, msgs in sessions_and_msgs:
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?)", (sid, title, "t0", "t1"))
        for i, (role, text) in enumerate(msgs):
            conn.execute(
                "INSERT INTO messages VALUES (?,?,?,?,?)",
                (f"{sid}-m{i}", sid, role,
                 json.dumps([{"type": "text", "data": {"text": text}}]),
                 f"2026-08-13T10:00:0{i}Z"),
            )
    conn.commit()
    conn.close()


def test_extract_crush_content_categorize_and_tags(tmp_path):
    db = tmp_path / "proj" / ".crush" / "crush.db"
    db.parent.mkdir(parents=True)
    _make_crush_db(db, [
        ("s1", "修复会话", [("user", "帮忙 fix 这个 bug"), ("assistant", "已修复")]),
        ("s2", "普通", [("user", "随便聊聊"), ("assistant", "好啊")]),
    ])

    with patch.object(crush_mod, "find_crush_dbs", lambda: [db]):
        facts, errors = crush_mod.extract_crush()

    assert errors == [] and len(facts) == 2
    by_cat = {f.category: f for f in facts}
    assert by_cat["fix"].tags == ["proj"]  # project DB 目录名
    assert by_cat["general"].tags == ["proj"]
    assert by_cat["fix"].id.startswith("crush:crush.db:s1:")  # db 名前缀防跨库碰撞


def test_extract_crush_global_tag(tmp_path):
    db = tmp_path / "crush.db"
    _make_crush_db(db, [("g1", "t", [("user", "hi"), ("assistant", "hello")])])
    with patch.object(crush_mod, "find_crush_dbs", lambda: [db]), \
         patch.object(crush_mod, "CRUSH_GLOBAL_DB", db):
        # global 判定基于路径含 .config/crush；直接构造该路径形态
        cfg_db = tmp_path / ".config" / "crush" / ".crush" / "crush.db"
        cfg_db.parent.mkdir(parents=True)
        _make_crush_db(cfg_db, [("g1", "t", [("user", "hi"), ("assistant", "hello")])])
        with patch.object(crush_mod, "find_crush_dbs", lambda: [cfg_db]):
            facts, errors = crush_mod.extract_crush()
    assert errors == [] and facts[0].tags == ["crush-global"]


# ── hermes（事实型源：trust → weight 映射）──────────────────────────────────


def test_extract_hermes_fact_mapping(tmp_path):
    db = tmp_path / "memory_store.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE facts (fact_id TEXT, content TEXT, category TEXT, tags TEXT, trust_score REAL, retrieval_count INT, helpful_count INT)")
    conn.execute("INSERT INTO facts VALUES (?,?,?,?,?,?,?)",
                 ("f1", "【uv 技巧】uv run 可以临时带依赖", "tool", "uv,python", 0.8, 3, 2))
    conn.commit()
    conn.close()

    with patch.object(hermes_mod, "HERMES_DB", db):
        facts, errors = hermes_mod.extract_hermes()

    assert errors == [] and len(facts) == 1
    f = facts[0]
    assert f.id == "hermes:f1"
    assert f.title == "uv 技巧"
    assert f.category == "tool"
    assert f.tags == ["uv", "python"]
    assert f.weight == 1.0  # 0.7 + (0.8-0.5) = 1.0 封顶
    assert f.timestamp == ""


# ── dispatch 统一（SOURCES 是唯一真相源；trace 走 Source.trace）─────────────


def test_all_seven_sources_registered():
    ex = _resolve_extractors()
    assert set(ex) == {"opencode", "zcode", "omp", "crush", "codex", "claude", "hermes"}
    for name, fn in ex.items():
        assert SOURCES[name].extract is fn


def test_trace_dispatched_via_source_trace():
    import agenote.reconcile as r

    calls = []
    SOURCES["opencode"].trace = lambda sid: {"source": "opencode", "session_id": sid, "messages": calls}
    try:
        result = r.trace_fact("opencode:some-session:m9")
    finally:
        SOURCES["opencode"].trace = __import__(
            "agenote.extract.opencode", fromlist=["trace_session"]
        ).trace_session
    assert result["source"] == "opencode"
    assert result["session_id"] == "some-session"
    assert result["fact_id"] == "opencode:some-session:m9"


def test_reconcile_source_uses_registry():
    import agenote.reconcile as r
    from agenote.extract.base import Source

    SOURCES["fake"] = Source(name="fake", extract=lambda: ([], ["fake done"]))
    try:
        rep = r.reconcile_source("fake", dry_run=True)
    finally:
        del SOURCES["fake"]
    assert rep.errors == 1
    assert "fake done" in rep.error_details[0]

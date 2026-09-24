# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""阶段 1c 迁移回归：六个对话 adapter + dispatch 统一。"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from unittest.mock import patch

import pytest

import agenote.extract.claude as claude_mod
import agenote.extract.codex as codex_mod
import agenote.extract.crush as crush_mod
from agenote.extract.base import (
    SOURCES,
    AdapterMessage,
    Turn,
    _resolve_extractors,
    pair_turns,
)


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
    # 全局库判定 = 与 CRUSH_GLOBAL_DB 路径相等（覆盖到自定义位置也成立）；
    # 默认形态（~/.config/crush 下）只是相等判定的一种特例
    cfg_db = tmp_path / ".config" / "crush" / ".crush" / "crush.db"
    cfg_db.parent.mkdir(parents=True)
    _make_crush_db(cfg_db, [("g1", "t", [("user", "hi"), ("assistant", "hello")])])
    with patch.object(crush_mod, "find_crush_dbs", lambda: [cfg_db]), \
         patch.object(crush_mod, "CRUSH_GLOBAL_DB", cfg_db):
        facts, errors = crush_mod.extract_crush()
    assert errors == [] and facts[0].tags == ["crush-global"]


# ── dispatch 统一（SOURCES 是唯一真相源；trace 走 Source.trace）─────────────


def test_all_six_sources_registered():
    ex = _resolve_extractors()
    assert set(ex) == {"opencode", "zcode", "omp", "crush", "codex", "claude"}
    for name, fn in ex.items():
        assert SOURCES[name].extract is fn


def test_hermes_is_not_an_extract_or_reconcile_source():
    from agenote.extract import run_extract
    import agenote.reconcile as reconcile

    assert "hermes" not in _resolve_extractors()
    with pytest.raises(ValueError, match="未知 source: hermes"):
        run_extract("hermes", dry_run=True)
    with pytest.raises(ValueError, match="未知 source: hermes"):
        reconcile.reconcile_source("hermes", dry_run=True)


@pytest.mark.parametrize("command", ["extract", "reconcile"])
def test_retired_source_cli_fails_without_traceback(tmp_path, command):
    env = os.environ.copy()
    env["KB_ROOT"] = str(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "agenote.cli", command, "--source", "hermes", "--dry-run"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "未知 source: hermes" in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("command", ["extract", "reconcile"])
def test_adapter_value_error_exits_nonzero(tmp_path, command):
    env = os.environ.copy()
    env["KB_ROOT"] = str(tmp_path)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    script = f"""
import sys
from agenote.extract.base import SOURCES, AdapterMessage, Source, _resolve_extractors

def broken_extractor():
    raise ValueError("adapter schema invalid")

# 先完成一次真实注册导入，再替换 registry；避免后续 lazy import 覆盖故障 adapter。
_resolve_extractors()
SOURCES["zcode"] = Source(name="zcode", extract=broken_extractor)
sys.argv = ["agenote", "{command}", "--source", "zcode", "--dry-run"]
from agenote.cli import main
main()
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "ValueError" in result.stderr
    assert "adapter schema invalid" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stdout == ""


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


@pytest.mark.parametrize("fact_id", ["bad", "opencode:no-such:m"])
def test_trace_errors_exit_nonzero_without_stdout(tmp_path, fact_id):
    """trace 失败必须走 stderr + rc1，不能伪装成功输出。"""
    env = os.environ.copy()
    env.update(KB_ROOT=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-m", "agenote.cli", "trace", "--id", fact_id],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "[trace 错误]" in result.stderr
    assert "Traceback" not in result.stderr


def _stored_fact(fact_id: str, source: str, *, title: str = "old") -> dict:
    """构造符合当前 reconcile 索引 schema 的历史事实。"""
    return {
        "id": fact_id,
        "source": source,
        "native_id": fact_id.rsplit(":", 1)[-1],
        "title": title,
        "category": "general",
        "content": "old content",
        "trust_score": 0.5,
        "weight": 0.5,
        "tags": [],
        "retrieved_at": "",
        "timestamp": "",
    }


def test_reconcile_all_prunes_unregistered_sources(tmp_path):
    import agenote.reconcile as r
    from agenote.extract.models import ReconciledFact

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    reconcile_index.write_text(
        json.dumps(
            {
                "version": 1,
                "updated": "",
                "by_source": {"hermes": 1, "retired": 1},
                "facts": [
                    _stored_fact("hermes:old", "hermes"),
                    _stored_fact("retired:old", "retired"),
                ],
            }
        ),
        encoding="utf-8",
    )
    fresh = ReconciledFact(
        id="fake:session:message",
        source="fake",
        native_id="message",
        title="保留的迁移后记忆",
        category="general",
        content="这是一条应当保留的普通迁移后记忆。",
        trust_score=0.7,
        weight=0.7,
    )

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {"fake": lambda: ([fresh], [])}), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        dry_report = r.reconcile_all(dry_run=True)
        dry_saved = json.loads(reconcile_index.read_text(encoding="utf-8"))
        report = r.reconcile_all()

    assert dry_report.pruned == 2
    assert dry_saved["by_source"] == {"hermes": 1, "retired": 1}
    assert [fact["id"] for fact in dry_saved["facts"]] == ["hermes:old", "retired:old"]
    saved = json.loads(reconcile_index.read_text(encoding="utf-8"))
    assert report.pruned == 2
    assert [fact["id"] for fact in saved["facts"]] == [fresh.id]
    assert saved["by_source"] == {"fake": 1}


def test_reconcile_corrupt_index_is_reported_without_overwrite(tmp_path):
    """已有 LKG 损坏时必须报错保留原文，不能静默重建覆盖。"""
    import agenote.reconcile as r
    from agenote.extract.models import ReconciledFact

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    reconcile_index.write_text("{broken", encoding="utf-8")
    before = reconcile_index.read_bytes()
    fresh = ReconciledFact(
        id="fake:new",
        source="fake",
        native_id="new",
        title="新事实",
        category="general",
        content="用户询问配置并得到具体步骤。",
        trust_score=0.7,
        weight=0.7,
    )

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {"fake": lambda: ([fresh], [])}), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        with pytest.raises(ValueError, match="索引损坏"):
            r.reconcile_source("fake")

    assert reconcile_index.read_bytes() == before


def test_reconcile_malformed_fact_public_cli_fails_closed(tmp_path):
    """search/dream 必须报告损坏的 reconcile 索引，不能静默当空库。"""
    import os
    import subprocess
    import sys
    import textwrap

    index = tmp_path / "agenote" / ".reconcile" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text('{"facts":[{"tags":[1]}]}', encoding="utf-8")
    before = index.read_bytes()
    env = os.environ.copy()
    env.update(KB_ROOT=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")

    for command in (["search", "needle"], ["dream", "--limit", "1"]):
        result = subprocess.run(
            [sys.executable, "-m", "agenote.cli", *command],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 1, (command, result)
        assert "事实结构非法" in result.stderr
        assert "Traceback" not in result.stderr
        assert result.stdout == ""
        assert index.read_bytes() == before


def test_reconcile_malformed_fact_reads_fail_closed(tmp_path, monkeypatch):
    """公共读路径不能把损坏索引悄悄降成空数据。"""
    import agenote.reconcile as r

    reconcile_index = tmp_path / "index.json"
    reconcile_index.write_text(
        '{"facts":[{"id":"x","source":"fake","native_id":"x",'
        '"title":"x","category":"general","content":"x",'
        '"trust_score":0.5,"weight":0.5,"tags":[1],'
        '"retrieved_at":"","timestamp":""}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(r, "RECONCILE_INDEX", reconcile_index)

    with pytest.raises(ValueError, match="事实结构非法"):
        r.load_reconcile_facts()


def _full_fact(**overrides) -> dict:
    fact = {
        "id": "fake:x", "source": "fake", "native_id": "x", "title": "t",
        "category": "general", "content": "c", "trust_score": 0.5, "weight": 0.5,
        "tags": [], "retrieved_at": "", "timestamp": "",
    }
    fact.update(overrides)
    return fact


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"version": "BAD", "updated": 123, "by_source": "BAD", "facts": []},
         "索引结构非法"),
        ({"facts": [_full_fact(trust_score=float("nan"))]}, "事实结构非法"),
        ({"facts": [_full_fact(id="other:x")]}, "事实结构非法"),
    ],
)
def test_reconcile_rejects_bad_top_level_and_cross_field_facts(
    tmp_path, monkeypatch, payload, message
):
    """顶层字段类型、非有限数值、id/source 不一致都必须 fail-closed。"""
    import agenote.reconcile as r

    index = tmp_path / "index.json"
    index.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(r, "RECONCILE_INDEX", index)

    with pytest.raises(ValueError, match=message):
        r._load_reconcile_index()


def test_reconcile_write_rejects_invalid_fact(tmp_path, monkeypatch):
    """非法事实不得落盘污染 LKG。"""
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    index = reconcile_dir / "index.json"
    monkeypatch.setattr(r, "RECONCILE_DIR", reconcile_dir)
    monkeypatch.setattr(r, "RECONCILE_INDEX", index)

    with pytest.raises(ValueError, match="事实结构非法"):
        r._save_reconcile_index({"facts": [_full_fact(tags=[1])]})
    assert not index.exists()


def test_trace_error_message_sanitization_policy(monkeypatch, capsys):
    """trace 错误通道的脱敏策略：未标记异常只显示类型；AdapterMessage 透传。"""
    import argparse

    import agenote.cli as cli

    monkeypatch.setattr(
        cli,
        "trace_fact",
        lambda fid: {"error": RuntimeError("SECRET_REAL"), "fact_id": fid},
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_trace(argparse.Namespace(id="fake:x", json=False))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "SECRET_REAL" not in captured.err
    assert "RuntimeError" in captured.err
    assert captured.out == ""


def test_trace_plain_string_adapter_error_keeps_original_text(monkeypatch, capsys):
    """adapter 自产 str 错误含原文透传（不再虚构「Exception」类型字样）。"""
    import argparse

    import agenote.cli as cli
    from agenote.extract.base import safe_adapter_error

    # helper 层：str 输入含原文、不含 Exception 虚构
    msg = safe_adapter_error("crush: partial failure", source="crush")
    assert "crush: partial failure" in msg
    assert "Exception" not in msg

    # 公共 trace 通道：str 透传原文（AdapterMessage 语义一致）
    monkeypatch.setattr(
        cli, "trace_fact", lambda fid: {"error": "公开诊断文本", "fact_id": fid}
    )
    with pytest.raises(SystemExit) as exc:
        cli.cmd_trace(argparse.Namespace(id="fake:x", json=False))
    assert exc.value.code == 1
    captured = capsys.readouterr()
    assert "公开诊断文本" in captured.err
    assert "Exception" not in captured.err
    assert captured.out == ""


@pytest.mark.parametrize("facts", [[42], [None], [{}], [{"id": "x"}], [{"id": "x", "source": "fake", "weight": True}]])
def test_reconcile_rejects_malformed_fact_elements(tmp_path, facts):
    """strict loader 必须在 f.get() 前拒绝非法事实元素并保留 LKG。"""
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    reconcile_index.write_text(json.dumps({"facts": facts}), encoding="utf-8")
    before = reconcile_index.read_bytes()

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {"fake": lambda: ([], ["unused"])}), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        with pytest.raises(ValueError, match="事实结构非法"):
            r.reconcile_source("fake")

    assert reconcile_index.read_bytes() == before


def test_trace_fallback_fails_closed_on_malformed_reconcile_index(tmp_path):
    """trace 的索引降级路径也不能把损坏索引伪装成“未找到”。"""
    import agenote.reconcile as reconcile

    index = tmp_path / "index.json"
    index.write_text('{"facts": [42]}', encoding="utf-8")
    with pytest.raises(ValueError, match="事实结构非法"):
        with patch.object(reconcile, "RECONCILE_INDEX", index):
            reconcile.trace_fact("codex:missing")


def test_reconcile_dry_run_fails_closed_on_malformed_index(tmp_path):
    """dry-run 也必须报告损坏的既有索引，不能把它当成空库。"""
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    reconcile_index.write_text('{"facts": [42]}', encoding="utf-8")
    before = reconcile_index.read_bytes()

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {"fake": lambda: ([], [])}), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        with pytest.raises(ValueError, match="事实结构非法"):
            r.reconcile_source("fake", dry_run=True)

    assert reconcile_index.read_bytes() == before


def test_reconcile_all_replaces_registered_source_facts(tmp_path):
    import agenote.reconcile as r
    from agenote.extract.models import ReconciledFact

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    reconcile_index.write_text(
        json.dumps(
            {
                "version": 1,
                "updated": "",
                "by_source": {"fake": 1},
                "facts": [_stored_fact("fake:old", "fake")],
            }
        ),
        encoding="utf-8",
    )
    fresh = ReconciledFact(
        id="fake:new",
        source="fake",
        native_id="new",
        title="新事实",
        category="general",
        content="用户询问如何配置 Guix，助手给出具体步骤和验证命令。",
        trust_score=0.7,
        weight=0.7,
    )

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {"fake": lambda: ([fresh], [])}), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = r.reconcile_all()

    saved = json.loads(reconcile_index.read_text(encoding="utf-8"))
    assert report.indexed == 1
    assert [fact["id"] for fact in saved["facts"]] == [fresh.id]
    assert saved["by_source"] == {"fake": 1}


def test_reconcile_all_does_not_write_when_extractor_fails(tmp_path):
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    initial = {
        "version": 1,
        "updated": "old",
        "by_source": {"hermes": 1, "fake": 1},
        "facts": [
            _stored_fact("hermes:old", "hermes", title="retired"),
            _stored_fact("fake:old", "fake"),
        ],
    }
    reconcile_index.write_text(json.dumps(initial), encoding="utf-8")
    before = reconcile_index.read_bytes()

    def broken_extractor():
        raise ValueError("adapter schema invalid")

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {"fake": broken_extractor}), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        with pytest.raises(ValueError, match="adapter schema invalid"):
            r.reconcile_all()

    assert reconcile_index.read_bytes() == before


def test_reconcile_all_does_not_write_when_extractor_reports_errors(tmp_path):
    import agenote.reconcile as r
    from agenote.extract.models import ReconciledFact

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    initial = {
        "version": 1,
        "updated": "old",
        "by_source": {"a": 1, "b": 1},
        "facts": [
            _stored_fact("a:old", "a", title="a-old"),
            _stored_fact("b:old", "b", title="b-old"),
        ],
    }
    reconcile_index.write_text(json.dumps(initial), encoding="utf-8")
    before = reconcile_index.read_bytes()
    fresh = ReconciledFact(
        id="a:new",
        source="a",
        native_id="new",
        title="a-new",
        category="general",
        content="用户询问配置并得到具体步骤。",
        trust_score=0.7,
        weight=0.7,
    )

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {
             "a": lambda: ([fresh], []),
             "b": lambda: ([], ["partial failure"]),
         }), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = r.reconcile_all()

    assert report.errors == 1
    assert reconcile_index.read_bytes() == before


def test_reconcile_source_does_not_write_when_extractor_reports_errors(tmp_path):
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    initial = {
        "version": 1,
        "updated": "old",
        "by_source": {"b": 1},
        "facts": [_stored_fact("b:old", "b", title="b-old")],
    }
    reconcile_index.write_text(json.dumps(initial), encoding="utf-8")
    before = reconcile_index.read_bytes()

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {
             "b": lambda: ([], ["partial failure"]),
         }), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = r.reconcile_source("b")

    assert report.errors == 1
    assert reconcile_index.read_bytes() == before


def test_reconcile_empty_extractor_result_prunes_source(tmp_path):
    """0 facts + 0 errors 不再视为失败：源真清空后允许落盘清掉该源旧事实。"""
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    initial = {
        "version": 1,
        "updated": "old",
        "by_source": {"fake": 1},
        "facts": [_stored_fact("fake:old", "fake")],
    }
    reconcile_index.write_text(json.dumps(initial), encoding="utf-8")

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {"fake": lambda: ([], [])}), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = r.reconcile_source("fake")

    assert report.errors == 0
    assert report.pruned == 1
    assert "0 facts" in report.error_details[0]
    saved = json.loads(reconcile_index.read_text(encoding="utf-8"))
    assert saved["facts"] == []
    assert saved["by_source"] == {}


def test_reconcile_source_uses_registry():
    import agenote.reconcile as r
    from agenote.extract.base import Source

    SOURCES["fake"] = Source(
        name="fake", extract=lambda: ([], [AdapterMessage("fake done")])
    )
    try:
        rep = r.reconcile_source("fake", dry_run=True)
    finally:
        del SOURCES["fake"]
    assert rep.errors == 1
    assert "fake done" in rep.error_details[0]


def test_reconcile_runtime_error_is_reported_without_traceback(tmp_path):
    """非 ValueError 异常也必须走报告通道，不能泄漏 traceback。"""
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    initial = {
        "version": 1,
        "updated": "old",
        "by_source": {"fake": 1},
        "facts": [_stored_fact("fake:old", "fake")],
    }
    reconcile_index.write_text(json.dumps(initial), encoding="utf-8")
    before = reconcile_index.read_bytes()

    def broken_extractor():
        raise RuntimeError("RUNTIME_REAL")

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {"fake": broken_extractor}), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = r.reconcile_source("fake")

    assert report.errors == 1
    assert "RuntimeError" in report.error_details[0]
    assert "RUNTIME_REAL" not in report.error_details[0]
    assert reconcile_index.read_bytes() == before

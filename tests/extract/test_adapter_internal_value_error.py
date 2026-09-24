# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest

import agenote.extract.opencode as opencode_mod


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


def _write_opencode_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE session "
        "(id TEXT PRIMARY KEY, title TEXT, directory TEXT, "
        "time_created INTEGER, time_updated INTEGER)"
    )
    conn.execute(
        "CREATE TABLE message "
        "(id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)"
    )
    conn.execute(
        "CREATE TABLE part "
        "(id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
        "time_created INTEGER, data TEXT)"
    )
    conn.execute("INSERT INTO session VALUES (?, ?, ?, ?, ?)", ("s1", "t", "", 0, 0))
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        ("m1", "s1", 1, json.dumps({"role": "user"})),
    )
    conn.execute(
        "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
        ("p1", "m1", "s1", 1, json.dumps({"type": "text", "text": "question"})),
    )
    conn.execute(
        "INSERT INTO message VALUES (?, ?, ?, ?)",
        ("m2", "s1", 2, json.dumps({"role": "assistant"})),
    )
    conn.execute(
        "INSERT INTO part VALUES (?, ?, ?, ?, ?)",
        ("p2", "m2", "s1", 2, json.dumps({"type": "text", "text": "answer"})),
    )
    conn.commit()
    conn.close()


def test_real_opencode_adapter_value_error_propagates(tmp_path, monkeypatch):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)

    def broken_categorize(*args):
        raise ValueError("adapter schema invalid")

    monkeypatch.setattr(opencode_mod, "OPENCODE_DB", db)
    monkeypatch.setattr(opencode_mod, "_categorize", broken_categorize)

    with pytest.raises(ValueError, match="adapter schema invalid"):
        opencode_mod.extract_opencode()


@pytest.mark.parametrize("command", ["extract", "reconcile"])
def test_real_opencode_cli_value_error_exits_nonzero(tmp_path, command):
    db = tmp_path / "opencode.db"
    _write_opencode_db(db)
    env = os.environ.copy()
    env.update(
        KB_ROOT=str(tmp_path / "kb"),
        OPENCODE_DB=str(db),
        PYTHONDONTWRITEBYTECODE="1",
    )
    script = textwrap.dedent(
        f"""
        import sys
        import agenote.extract.opencode as adapter

        def broken(*args):
            raise ValueError("adapter schema invalid")

        adapter._categorize = broken
        sys.argv = ["agenote", "{command}", "--source", "opencode", "--dry-run"]
        from agenote.cli import main
        main()
        """
    )
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


def test_real_reconcile_malformed_fact_element_cli_is_clean(tmp_path):
    """真实 CLI 必须在加载阶段拒绝非法 fact，不泄漏 AttributeError，且不改 LKG。"""
    index = tmp_path / "agenote" / ".reconcile" / "index.json"
    index.parent.mkdir(parents=True)
    index.write_text('{"facts":[42]}', encoding="utf-8")
    before = index.read_bytes()
    env = os.environ.copy()
    env.update(KB_ROOT=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        import agenote.reconcile as reconcile

        reconcile.RECONCILE_DIR = Path({str(index.parent)!r})
        reconcile.RECONCILE_INDEX = Path({str(index)!r})
        reconcile._known_extractors = lambda: {{"fake": lambda: ([], ["unused"])}}
        sys.argv = ["agenote", "reconcile", "--source", "fake"]
        from agenote.cli import main
        main()
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "事实结构非法" in result.stderr
    assert "Traceback" not in result.stderr
    assert "AttributeError" not in result.stderr
    assert index.read_bytes() == before


def test_real_reconcile_runtime_error_reports_without_traceback(tmp_path):
    index = tmp_path / ".reconcile" / "index.json"
    index.parent.mkdir()
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "updated": "old",
                "by_source": {"fake": 1},
                "facts": [_stored_fact("fake:old", "fake")],
            }
        ),
        encoding="utf-8",
    )
    before = index.read_bytes()
    env = os.environ.copy()
    env.update(KB_ROOT=str(tmp_path), PYTHONDONTWRITEBYTECODE="1")
    script = textwrap.dedent(
        f"""
        import sys
        from pathlib import Path
        import agenote.reconcile as reconcile

        def broken():
            raise RuntimeError("RUNTIME_REAL")

        reconcile._known_extractors = lambda: {{"fake": broken}}
        reconcile._kb_titles = lambda: set()
        reconcile.RECONCILE_DIR = Path({str(index.parent)!r})
        reconcile.RECONCILE_INDEX = Path({str(index)!r})
        sys.argv = ["agenote", "reconcile", "--source", "fake"]
        from agenote.cli import main
        main()
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "RuntimeError" in result.stderr
    assert "RUNTIME_REAL" not in result.stderr
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("=== reconcile (fake, dry_run=False) ===")
    assert "reconcile 完成但存在 adapter 错误" in result.stderr
    assert index.read_bytes() == before


def test_real_crush_adapter_value_error_propagates(tmp_path, monkeypatch):
    import agenote.extract.crush as crush_mod

    db = tmp_path / "crush.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE sessions "
        "(id TEXT PRIMARY KEY, title TEXT, created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE messages "
        "(id TEXT PRIMARY KEY, session_id TEXT, role TEXT, parts TEXT, created_at TEXT)"
    )
    conn.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", ("s1", "t", "t0", "t1"))
    conn.execute(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
        (
            "m1",
            "s1",
            "user",
            json.dumps([{"type": "text", "data": {"text": "question"}}]),
            "t2",
        ),
    )
    conn.execute(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
        (
            "m2",
            "s1",
            "assistant",
            json.dumps([{"type": "text", "data": {"text": "answer"}}]),
            "t3",
        ),
    )
    conn.commit()
    conn.close()

    def broken_categorize(*args):
        raise ValueError("adapter schema invalid")

    monkeypatch.setattr(crush_mod, "find_crush_dbs", lambda: [db])
    monkeypatch.setattr(crush_mod, "_categorize", broken_categorize)

    with pytest.raises(ValueError, match="adapter schema invalid"):
        crush_mod.extract_crush()

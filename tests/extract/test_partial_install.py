# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""部分安装机器上的 extract / reconcile 可用性回归。

「源未安装 / 数据不存在」是 agenote 作为发布工具面对的正常状态：
编排层必须把它降级为 skip（不阻塞整批发布），只有真实错误才 fail-closed。
全部用例在 tmp_path 内构造数据，不触碰真实 HOME 与 agent 数据目录。
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import pytest

import agenote.extract.claude as claude_mod
import agenote.extract.codex as codex_mod
import agenote.extract.crush as crush_mod
import agenote.extract.omp as omp_mod
import agenote.extract.opencode as opencode_mod
import agenote.extract.zcode as zcode_mod
from agenote.extract import run_extract
from agenote.extract.base import AdapterSkip


# ── 共享构造 ─────────────────────────────────────────────────────────────────


def _uninstall_all(tmp_path: Path, *, include_zcode: bool = True):
    """把各源路径 patch 到 tmp 下不存在的位置（模拟未安装）。

    include_zcode=False 时跳过 zcode（部分安装主测试要给它挂真 DB）。
    """
    missing = tmp_path / "missing"
    patches = [
        patch.object(opencode_mod, "OPENCODE_DB", missing / "opencode.db"),
        patch.object(omp_mod, "OMP_SESSIONS_DIR", missing / "omp" / "sessions"),
        patch.object(claude_mod, "CLAUDE_TRANSCRIPTS_DIR", missing / "claude"),
        patch.object(codex_mod, "CODEX_HOME", missing / "codex"),
        patch.object(crush_mod, "CRUSH_GLOBAL_DB", missing / "crush.db"),
        patch.object(crush_mod, "CRUSH_SEARCH_ROOTS", []),
    ]
    if include_zcode:
        patches.insert(0, patch.object(zcode_mod, "ZCODE_DB", missing / "zcode.sqlite"))
    return patches


def _stored_fact(fact_id: str, source: str, *, title: str = "old") -> dict:
    """构造符合 reconcile 索引 schema 的历史事实。"""
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


# ── extract：部分安装整批可用 ────────────────────────────────────────────────


def test_extract_all_succeeds_with_only_zcode_installed(tmp_path, db_factory):
    """仅 zcode 有数据时 --source all 必须成功落盘；其余源以 skip 呈现。"""
    db = db_factory(
        session={"id": "s1", "title": "修复登录", "directory": "/proj", "time_created": "2026-01-01"},
        messages=[
            {"id": "m1", "role": "user", "parts": [{"type": "text", "text": "登录报错怎么办"}]},
            {"id": "m2", "role": "assistant", "parts": [{"type": "text", "text": "检查 cookie 配置"}]},
        ],
    )
    out = tmp_path / "out"

    with ExitStack() as stack:
        stack.enter_context(patch.object(zcode_mod, "ZCODE_DB", db))
        for p in _uninstall_all(tmp_path, include_zcode=False):
            stack.enter_context(p)
        report = run_extract(source="all", output_dir=str(out))

    assert report["failed"] is False
    skips = [e for e in report["errors"] if e.startswith("[skip]")]
    assert len(skips) >= 5  # opencode/omp/claude/codex/crush 全部 skip
    # skip 不是 error：不出现无 [skip] 前缀的「不存在」报错
    assert not [e for e in report["errors"] if "不存在" in e and not e.startswith("[skip]")]
    zcode_org = out / "zcode.org"
    assert zcode_org.exists()
    assert "登录报错怎么办" in zcode_org.read_text(encoding="utf-8")


def test_extract_skip_source_does_not_mask_real_error(tmp_path):
    """skip 源不得稀释 fail-closed：真实错误仍整批拒绝发布。"""
    out = tmp_path / "out"
    with patch("agenote.extract.base._resolve_extractors", return_value={
        "skipsrc": lambda: ([], [AdapterSkip("数据库不存在")]),
        "bad": lambda: ([], ["real failure"]),
    }):
        report = run_extract(source="all", output_dir=str(out))

    assert report["failed"] is True
    assert report["files"] == []
    assert not any(out.glob("*.org"))


# ── crush：全局 DB 不存在时不进扫描 ─────────────────────────────────────────


def test_find_crush_dbs_omits_missing_global_db(tmp_path):
    with patch.object(crush_mod, "CRUSH_GLOBAL_DB", tmp_path / "nope.db"), \
         patch.object(crush_mod, "CRUSH_SEARCH_ROOTS", []):
        assert crush_mod.find_crush_dbs() == []


def test_find_crush_dbs_keeps_existing_global_db(tmp_path):
    db = tmp_path / "crush.db"
    db.touch()
    with patch.object(crush_mod, "CRUSH_GLOBAL_DB", db), \
         patch.object(crush_mod, "CRUSH_SEARCH_ROOTS", []):
        assert crush_mod.find_crush_dbs() == [db]


def test_extract_crush_uninstalled_reports_skip():
    with patch.object(crush_mod, "find_crush_dbs", lambda: []):
        facts, errors = crush_mod.extract_crush()
    assert facts == []
    assert len(errors) == 1
    assert isinstance(errors[0], AdapterSkip)


def _crush_db(path: Path, *, session_id: str = "s1") -> Path:
    """构造符合 crush schema 的双表 DB（sessions/messages + parts JSON 列）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, title TEXT, parent_session_id TEXT,
            message_count INTEGER, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY, session_id TEXT, role TEXT,
            parts TEXT, model TEXT, created_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (session_id, "全局判定回归", "2026-01-01", "2026-01-01"),
    )
    parts = json.dumps([{"type": "text", "data": {"text": "crush 全局库判定是怎么回事"}}])
    for i, role in enumerate(("user", "assistant")):
        conn.execute(
            "INSERT INTO messages (id, session_id, role, parts, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"m{i}", session_id, role, parts, "2026-01-01"),
        )
    conn.commit()
    conn.close()
    return path


def test_extract_crush_global_db_recognized_via_override(tmp_path):
    """CRUSH_GLOBAL_DB 覆盖到自定义位置时按路径相等识别为全局库。

    旧的字符串包含判定（".config/crush" in path）会把覆盖后的全局库误判成
    项目库（project_dir 落成真实目录），本用例与项目库并扫防回归。
    """
    global_db = _crush_db(tmp_path / "global" / ".crush" / "crush.db")
    project_db = _crush_db(tmp_path / "proj" / ".crush" / "crush.db", session_id="s2")
    with patch.object(crush_mod, "CRUSH_GLOBAL_DB", global_db), \
         patch.object(crush_mod, "CRUSH_SEARCH_ROOTS", [str(tmp_path)]):
        facts, errors = crush_mod.extract_crush()

    assert errors == []
    tags = set()
    for fact in facts:
        assert len(fact.tags) == 1
        tags.add(fact.tags[0])
    # 全局库 → (global) 代理 tag；项目库 → 目录名 tag，二者互不混淆
    assert tags == {"crush-global", "proj"}


# ── reconcile：skip 源不阻塞落盘 ─────────────────────────────────────────────


def test_reconcile_all_skip_source_does_not_block_publish(tmp_path):
    """skip 源不阻塞整批：好源事实落盘，skip 源旧事实按清空语义清理。"""
    import agenote.reconcile as r
    from agenote.extract.models import ReconciledFact

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    reconcile_index.write_text(
        json.dumps({
            "version": 1,
            "updated": "old",
            "by_source": {"a": 1, "skipsrc": 1},
            "facts": [
                _stored_fact("a:old", "a", title="a-old"),
                _stored_fact("skipsrc:old", "skipsrc", title="skip-old"),
            ],
        }),
        encoding="utf-8",
    )
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
             "skipsrc": lambda: ([], [AdapterSkip("数据库不存在")]),
         }), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = r.reconcile_all()

    assert report.errors == 0
    assert any(d.startswith("[skip]") and "skipsrc" in d for d in report.error_details)
    saved = json.loads(reconcile_index.read_text(encoding="utf-8"))
    assert [f["id"] for f in saved["facts"]] == ["a:new"]
    assert saved["by_source"] == {"a": 1}


def test_reconcile_all_empty_machine_succeeds(tmp_path):
    """纯空源机器（全部源未安装/无数据）reconcile all 成功并落盘空索引。"""
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {
             "a": lambda: ([], [AdapterSkip("数据库不存在")]),
             "b": lambda: ([], []),
         }), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = r.reconcile_all()

    assert report.errors == 0
    assert reconcile_index.exists()
    saved = json.loads(reconcile_index.read_text(encoding="utf-8"))
    assert saved["facts"] == []


def test_reconcile_real_error_still_blocks_publish_alongside_skip(tmp_path):
    """skip 源与真实错误并存时，真实错误仍阻止落盘（LKG 不变）。"""
    import agenote.reconcile as r

    reconcile_dir = tmp_path / ".reconcile"
    reconcile_dir.mkdir()
    reconcile_index = reconcile_dir / "index.json"
    reconcile_index.write_text(
        json.dumps({
            "version": 1,
            "updated": "old",
            "by_source": {"skipsrc": 1},
            "facts": [_stored_fact("skipsrc:old", "skipsrc")],
        }),
        encoding="utf-8",
    )
    before = reconcile_index.read_bytes()

    with patch.object(r, "RECONCILE_DIR", reconcile_dir), \
         patch.object(r, "RECONCILE_INDEX", reconcile_index), \
         patch.object(r, "_known_extractors", lambda: {
             "skipsrc": lambda: ([], [AdapterSkip("数据库不存在")]),
             "bad": lambda: ([], ["real failure"]),
         }), \
         patch.object(r, "_kb_titles", lambda: set()), \
         patch("agenote.core.KB_ROOT", tmp_path):
        report = r.reconcile_all()

    assert report.errors == 1
    assert reconcile_index.read_bytes() == before


# ── 脱敏：未安装消息不得泄漏本地路径 ─────────────────────────────────────────


@pytest.mark.parametrize("mod_attr,extract_fn", [
    ("CLAUDE_TRANSCRIPTS_DIR", "extract_claude"),
    ("CODEX_HOME", "extract_codex"),
    ("OMP_SESSIONS_DIR", "extract_omp"),
])
def test_uninstalled_source_messages_do_not_leak_paths(tmp_path, mod_attr, extract_fn):
    module = {
        "CLAUDE_TRANSCRIPTS_DIR": claude_mod,
        "CODEX_HOME": codex_mod,
        "OMP_SESSIONS_DIR": omp_mod,
    }[mod_attr]
    missing = tmp_path / "claude-home" / "secrets" / mod_attr
    with patch.object(module, mod_attr, missing):
        _, errors = getattr(module, extract_fn)()
    assert errors
    assert str(tmp_path) not in errors[0]

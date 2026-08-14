# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""omp adapter（JSONL 事件流 + parentId 重建）迁移到 framework 的回归测试。"""

from __future__ import annotations

import json
from unittest.mock import patch

import agenote.extract.omp as omp_mod
from agenote.extract.base import SOURCES, _resolve_extractors


def _write_session(sessions_dir, rel_path, events):
    """写一个 omp .jsonl 会话文件，返回路径。"""
    path = sessions_dir / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )
    return path


SESSION_EVENTS = [
    {"type": "session", "id": "sess-1", "cwd": "/home/u/proj-a"},
    # 故意乱序写入：子消息先于父消息，验证 parentId 链重建
    {"type": "message", "id": "m2", "parentId": "m1", "timestamp": "2026-08-13T10:00:02Z",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "用 pyproject 配置"}]}},
    {"type": "message", "id": "m1", "parentId": "", "timestamp": "2026-08-13T10:00:01Z",
     "message": {"role": "user", "content": "怎么配置 pytest"}},
    {"type": "message", "id": "m4", "parentId": "m3", "timestamp": "2026-08-13T10:00:04Z",
     "message": {"role": "assistant", "content": "不客气"}},
    {"type": "message", "id": "m3", "parentId": "m2", "timestamp": "2026-08-13T10:00:03Z",
     "message": {"role": "user", "content": "谢谢"}},
]


def test_registry_includes_omp_and_excludes_pi():
    extractors = _resolve_extractors()
    assert "omp" in extractors
    assert SOURCES["omp"].extract is omp_mod.extract_omp
    assert "pi" not in extractors  # pi.py 已删除


def test_extract_omp_pairs_and_reorders(tmp_path):
    sessions = tmp_path / "sessions"
    _write_session(sessions, "proj-a/20260813_sess-1.jsonl", SESSION_EVENTS)

    with patch.object(omp_mod, "OMP_SESSIONS_DIR", sessions):
        facts, errors = omp_mod.extract_omp()

    assert errors == []
    assert len(facts) == 2
    # parentId 重建后顺序 m1→m2→m3→m4，配出两对
    assert facts[0].native_id == "m2"
    assert facts[0].id == "omp:sess-1:m2"
    assert facts[0].source == "omp"
    assert "怎么配置 pytest" in facts[0].content
    assert "用 pyproject 配置" in facts[0].content
    assert facts[0].timestamp == "2026-08-13T10:00:01Z"  # 取 user 回合时间戳
    assert facts[0].tags == ["proj-a"]  # cwd 末段
    assert facts[0].category == "general"
    assert facts[1].native_id == "m4"


def test_extract_omp_skips_advisor_and_missing_dir(tmp_path):
    # 目录不存在 → 单条错误
    with patch.object(omp_mod, "OMP_SESSIONS_DIR", tmp_path / "nope"):
        facts, errors = omp_mod.extract_omp()
    assert facts == []
    assert len(errors) == 1 and "不存在" in errors[0]

    # __advisor.jsonl 子对话跳过
    sessions = tmp_path / "sessions"
    _write_session(sessions, "proj-a/__advisor.jsonl", SESSION_EVENTS)
    with patch.object(omp_mod, "OMP_SESSIONS_DIR", sessions):
        facts, errors = omp_mod.extract_omp()
    assert facts == [] and errors == []


def test_extract_omp_tool_parts_formatted(tmp_path):
    sessions = tmp_path / "sessions"
    _write_session(sessions, "s.jsonl", [
        {"type": "session", "id": "sess-t", "cwd": ""},
        {"type": "message", "id": "u1", "parentId": "", "message": {"role": "user", "content": "跑测试"}},
        {"type": "message", "id": "a1", "parentId": "u1", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "bash", "input": {"cmd": "pytest"}},
            {"type": "tool_result", "content": "2 passed"},
            {"type": "text", "text": "搞定"},
        ]}},
    ])
    with patch.object(omp_mod, "OMP_SESSIONS_DIR", sessions):
        facts, errors = omp_mod.extract_omp()

    assert errors == [] and len(facts) == 1
    assert "[tool_use: bash]" in facts[0].content
    assert "[tool_result] 2 passed" in facts[0].content
    assert "搞定" in facts[0].content
    assert facts[0].tags == ["unknown"]  # cwd 为空时框架统一值


def test_trace_session_full_content_untruncated(tmp_path):
    sessions = tmp_path / "sessions"
    _write_session(sessions, "proj-a/20260813_sess-1.jsonl", SESSION_EVENTS)

    with patch.object(omp_mod, "OMP_SESSIONS_DIR", sessions):
        result = omp_mod.trace_session("sess-1")

    assert result["source"] == "omp"
    assert result["session"]["cwd"] == "/home/u/proj-a"
    roles = [m["role"] for m in result["messages"]]
    assert roles == ["user", "assistant", "user", "assistant"]  # parentId 顺序

    with patch.object(omp_mod, "OMP_SESSIONS_DIR", sessions):
        missing = omp_mod.trace_session("no-such")
    assert "error" in missing

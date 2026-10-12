# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""U1 MEMORY 入口预算：配置键、doctor 只读检查、session 截断告警。"""

from __future__ import annotations

import argparse
import json
import types

import pytest

from agenote import config
from agenote.context import ENTRY_BUDGET_MARK, build_context_result, cmd_context
from agenote.doctor import _check_memory_entry_budget, run_checks


def _mem(tmp_path, text):
    mem = tmp_path / "MEMORY.org"
    mem.write_text(text, encoding="utf-8")
    return types.SimpleNamespace(name="human", memory_org=mem)


OK_TEXT = """#+title: MEMORY-test

* user
** U001 正常条目
   :PROPERTIES:
   :TYPE:     U
   :SCOPE:    user
   :END:
   # 短钩子
   短正文
"""


def _args(**kw):
    base = {"mode": "session", "query": None, "budget": None, "project": None,
            "types": None, "host": "generic", "format": "text"}
    base.update(kw)
    return argparse.Namespace(**base)


# ── 配置键 ───────────────────────────────────────────────────────────────────


def test_schema_memory_budget_defaults():
    assert config.get("memory", "max_entry_lines") == 120
    assert config.get("memory", "max_entry_chars") == 6000
    assert config.get("context", "session_entry_budget_chars") == 2000


def test_schema_memory_budget_env_override(monkeypatch):
    monkeypatch.setenv("AGENOTE_MEMORY_MAX_ENTRY_LINES", "240")
    monkeypatch.setenv("AGENOTE_MEMORY_MAX_ENTRY_CHARS", "12000")
    monkeypatch.setenv("AGENOTE_CONTEXT_SESSION_ENTRY_BUDGET_CHARS", "4000")
    assert config.get("memory", "max_entry_lines") == "240"
    assert config.get("memory", "max_entry_chars") == "12000"
    assert config.get("context", "session_entry_budget_chars") == "4000"


# ── doctor 只读检查 ──────────────────────────────────────────────────────────


def test_doctor_over_lines_warns_and_readonly(tmp_path):
    body = "\n".join(f"   正文第{i}行" for i in range(130))
    mem = _mem(tmp_path, OK_TEXT + f"** U002 超长行数\n{body}\n")
    before = mem.memory_org.read_bytes()
    res = _check_memory_entry_budget([mem])
    assert res["name"] == "memory-entry-budget"
    assert res["status"] == "warn"
    assert "U002" in res["detail"]
    assert mem.memory_org.read_bytes() == before  # 只读：不自动修


def test_doctor_over_chars_warns(tmp_path):
    mem = _mem(tmp_path, OK_TEXT + "** U003 超长字符\n   " + "字" * 6100 + "\n")
    res = _check_memory_entry_budget([mem])
    assert res["status"] == "warn"
    assert "U003" in res["detail"]
    assert "手工拆分" in res["affects"]


def test_doctor_inside_budget_ok(tmp_path):
    res = _check_memory_entry_budget([_mem(tmp_path, OK_TEXT)])
    assert res["status"] == "ok"
    assert "1 条在预算内" in res["detail"]


def test_doctor_missing_file_ok(tmp_path):
    ctx = types.SimpleNamespace(
        name="human", memory_org=tmp_path / "MEMORY.org")
    res = _check_memory_entry_budget([ctx])
    assert res["status"] == "ok"


def test_doctor_invalid_config_warns(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENOTE_MEMORY_MAX_ENTRY_LINES", "abc")
    res = _check_memory_entry_budget([_mem(tmp_path, OK_TEXT)])
    assert res["status"] == "warn"
    assert "非法" in res["detail"]


def test_doctor_check_registered():
    assert "memory-entry-budget" in [c["name"] for c in run_checks()]


# ── session 截断 + 告警 ──────────────────────────────────────────────────────


def test_session_entry_over_budget_truncated(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "testhost")
    monkeypatch.setenv("AGENOTE_CONTEXT_SESSION_ENTRY_BUDGET_CHARS", "60")
    text = OK_TEXT.replace("正常条目", "长标题" * 100)
    res = build_context_result(_args(), _mem(tmp_path, text))
    assert res["status"] == "ok"
    assert res["truncated"] is True
    assert ENTRY_BUDGET_MARK in res["content"]


def test_session_entry_inside_budget_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "testhost")
    res = build_context_result(_args(budget=100000), _mem(tmp_path, OK_TEXT))
    assert res["status"] == "ok"
    assert res["truncated"] is False
    assert ENTRY_BUDGET_MARK not in res["content"]


def test_session_json_carries_truncated(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "testhost")
    monkeypatch.setenv("AGENOTE_CONTEXT_SESSION_ENTRY_BUDGET_CHARS", "60")
    text = OK_TEXT.replace("正常条目", "长标题" * 100)
    cmd_context(_args(format="json"), _mem(tmp_path, text))
    res = json.loads(capsys.readouterr().out)
    assert res["truncated"] is True
    assert ENTRY_BUDGET_MARK in res["content"]


def test_session_invalid_entry_budget_dies(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "testhost")

    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "testhost")
    monkeypatch.setenv("AGENOTE_CONTEXT_SESSION_ENTRY_BUDGET_CHARS", "abc")
    with pytest.raises(SystemExit):
        build_context_result(_args(), _mem(tmp_path, OK_TEXT))

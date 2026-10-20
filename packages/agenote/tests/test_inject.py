# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""`agenote inject`：状态化注入决策（指纹缓存 / 三件套 / 会话重置）。

迁移前这些断言散在 `injectors/selftest.sh` 的四个宿主 section 里，各测一遍
同构实现；现在只有一个实现，selftest 只验宿主协议适配。
"""

from __future__ import annotations

import argparse
import json
import types

import pytest

from agenote.inject import _state_path, cmd_inject

MEM = """#+title: MEMORY-test

* user
** U001 回复用中文
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :TYPE:     U
   :SCOPE:    user
   :END:
   # 用户要求所有回复一律使用中文
   正文段落补充说明中文语料

* reference
** R001 BM25 参数
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :END:
   # 检索使用 BM25，k1=1.5、b=0.75
   检索参数说明正文
"""


@pytest.fixture
def kb(tmp_path, monkeypatch):
    import agenote.core as core

    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    monkeypatch.setenv("AGENOTE_INJECTOR_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENOTE_INJECTION_RECALL_MIN_SCORE", "0.1")
    (tmp_path / "memories").mkdir()
    mem = tmp_path / "MEMORY.org"
    mem.write_text(MEM, encoding="utf-8")
    return types.SimpleNamespace(
        memory_org=mem, memories=tmp_path / "memories", root=tmp_path,
    )


def _args(**kw):
    base = {
        "event": "session", "session_id": "s1", "host": "zcode",
        "budget": 8000, "prompt": "", "format": "json",
    }
    base.update(kw)
    return argparse.Namespace(**base)


def _run(kb, capsys, **kw):
    cmd_inject(_args(**kw), kb)
    return json.loads(capsys.readouterr().out)


# ── 会话通道：简报 + 指纹缓存 + 重置 ─────────────────────────────────────────


def test_session_injects_then_replays_from_cache(kb, capsys):
    first = _run(kb, capsys)
    assert first["status"] == "ok" and first["action"] == "inject"
    assert first["content"].startswith("<!-- agenote-context v1 mode=session")
    assert first["cached"] is False

    again = _run(kb, capsys)
    assert again["cached"] is True
    assert again["content"] == first["content"]


def test_session_cache_invalidated_by_kb_change(kb, capsys):
    _run(kb, capsys)
    kb.memory_org.write_text(MEM + "\n* feedback\n", encoding="utf-8")
    changed = _run(kb, capsys)
    assert changed["cached"] is False


def test_session_resets_cumulative_and_recall_key(kb, capsys):
    _run(kb, capsys)
    recall = _run(kb, capsys, event="prompt", prompt="BM25 检索参数是什么来着", budget=4000)
    assert recall["status"] == "ok" and recall["action"] == "inject"

    state_path = _state_path("zcode", "s1")
    before = json.loads(state_path.read_text())
    assert before["cumulative"] == len(recall["content"]) and before["recall_key"]

    _run(kb, capsys)  # session 事件
    after = json.loads(state_path.read_text())
    assert after["cumulative"] == 0 and "recall_key" not in after


def test_disabled_switch_zero_bytes_and_no_cache(kb, capsys, monkeypatch):
    monkeypatch.setenv("AGENOTE_INJECTION_ENABLED", "false")
    res = _run(kb, capsys)
    assert res["status"] == "disabled" and res["action"] == "skip"
    assert res["content"] == ""
    state = json.loads(_state_path("zcode", "s1").read_text())
    assert "brief_content" not in state  # 空结果不缓存，开关转开立即生效


# ── 每轮通道：三件套 ─────────────────────────────────────────────────────────


def test_prompt_injects_once_then_dedupes(kb, capsys):
    _run(kb, capsys)
    res = _run(kb, capsys, event="prompt", prompt="BM25 检索参数是什么来着", budget=4000)
    assert res["action"] == "inject" and "mode=recall" in res["content"]

    dup = _run(kb, capsys, event="prompt", prompt="BM25 检索参数是什么来着", budget=4000)
    assert dup["status"] == "duplicate" and dup["content"] == ""


def test_prompt_short_query_skipped(kb, capsys):
    res = _run(kb, capsys, event="prompt", prompt="继续")
    assert res["status"] == "short" and res["action"] == "skip"
    assert res["content"] == ""


def test_prompt_self_injection_marker_skipped(kb, capsys):
    res = _run(kb, capsys, event="prompt",
               prompt="<agenote-hook>检测到任务完成信号，请评估本次对话</agenote-hook>")
    assert res["status"] == "skip" and res["content"] == ""


def test_prompt_cumulative_budget_stops_recall(kb, capsys, monkeypatch):
    monkeypatch.setenv("AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET", "1")
    # 首轮累计为 0，仍可注入；注入后累计超限，下一轮不同 query 触顶。
    first = _run(kb, capsys, event="prompt",
                 prompt="BM25 检索参数是什么来着", budget=4000)
    assert first["action"] == "inject"
    res = _run(kb, capsys, event="prompt",
               prompt="中文回复偏好是什么", budget=4000)
    assert res["status"] == "budget" and res["action"] == "skip"
    # 触顶也记键：同一 query 下轮不再生成
    assert _run(kb, capsys, event="prompt",
                prompt="中文回复偏好是什么")["status"] == "duplicate"


def test_text_format_emits_only_content(kb, capsys):
    cmd_inject(_args(format="text"), kb)
    out = capsys.readouterr().out
    assert out.startswith("<!-- agenote-context v1 mode=session")

    cmd_inject(_args(format="text", event="prompt", prompt="继续"), kb)
    assert capsys.readouterr().out == ""


def test_session_id_sanitized_in_state_filename(kb, capsys):
    _run(kb, capsys, session_id="../../etc/passwd")
    files = list((kb.root / "state").glob("*.json"))
    assert len(files) == 1
    assert files[0].name == "zcode-etcpasswd.json"

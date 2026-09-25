# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""`agenote context` 注入简报（C1）：三态×两格式、预算裁剪、marker、recall 门槛、项目三步匹配。

recall_min_score 标定方法（SCHEMA 默认值的由来，2026-09-25）：
  1. 以 KB 真实 reconcile 事实（~/Documents/Org/agenote/.reconcile/index.json，
     只读抽样 240 条）在 /tmp 临时 KB 渲染成 MEMORY.org 类型化条目——
     import 管道的产物形态即如此，语料真实；
  2. `KB_ROOT=<tmp> AGENOTE_INJECTION_RECALL_MIN_SCORE=0 uv run agenote context
     --mode recall --query <q> --format json` 跑 10 类典型 query（项目名/技术词/
     中文短语/英文标识符/日常短词），收集全部分数分布；
  3. 相关命中（条目含 query 词）分数 ≥12.3，纯 n-gram 偶然重叠的噪声 ≤5.7，
     取 8.0 居中分隔。语料增长后可用同法重标定（改 SCHEMA 默认值即可）。
  本文件单测语料只有几条，BM25 分数量级远小于真实语料，故统一用
  AGENOTE_INJECTION_RECALL_MIN_SCORE env 放宽下限；标定默认值单独回归
  （test_recall_min_score_default_is_calibrated）。
"""

from __future__ import annotations

import argparse
import json
import types

import pytest

from agenote.context import MORE_GUIDE, build_context_result

TEXT = """#+title: MEMORY-test

* user
** U001 回复用中文
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     U
   :SCOPE:    user
   :END:
   # 用户要求所有回复一律使用中文
   正文段落补充说明中文语料

* feedback
** F001 cat 大文件卡顿
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-12]
   :VALIDATED_AT: [2026-09-12]
   :END:
   # 大文件用 rg 分页查看禁用 cat
   追加正文：rg 优于 cat
** F002 用 rg 不用 grep
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :UPDATED:  [2026-09-02]
   :END:
   # 检索一律用 rg 禁用 grep

* project
** agenote
   :PROPERTIES:
   :PATH:     {projdir}
   :UPDATED:  [2026-09-20]
   :END:
** P001 提交前跑 pytest
   :PROPERTIES:
   :CREATED:  [2026-09-05]
   :UPDATED:  [2026-09-05]
   :TYPE:     P
   :SCOPE:    project
   :PROJECT:  agenote
   :END:
   # 提交前必须全量 pytest 通过

* environment
** E001 本机 Guix 系统
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :UPDATED:  [2026-09-02]
   :TYPE:     E
   :SCOPE:    machine
   :MACHINE:  testhost
   :END:
   # Guix 系统禁持久化安装
** E002 异机条目
   :PROPERTIES:
   :CREATED:  [2026-09-02]
   :UPDATED:  [2026-09-02]
   :TYPE:     E
   :SCOPE:    machine
   :MACHINE:  otherhost
   :END:
   # 其他机器的环境记录

* reference
** R001 BM25 参数参考
   :PROPERTIES:
   :CREATED:  [2026-09-03]
   :UPDATED:  [2026-09-03]
   :TYPE:     R
   :END:
   # Okapi BM25 经典参数 k1 1.5 b 0.75

* deprecated
** F000 旧纠正
   :PROPERTIES:
   :CREATED:  [2026-01-01]
   :UPDATED:  [2026-01-01]
   :END:
"""


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """KB_ROOT 指到 tmp 的最小记忆库；机器键固定，recall 下限放宽到小语料量级。"""
    import agenote.core as core

    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    projdir = tmp_path / "agenote-repo"
    projdir.mkdir()
    mem = tmp_path / "MEMORY.org"
    mem.write_text(TEXT.format(projdir=projdir), encoding="utf-8")
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "testhost")
    monkeypatch.setenv("AGENOTE_INJECTION_RECALL_MIN_SCORE", "0.1")
    return types.SimpleNamespace(memory_org=mem, root=tmp_path)


def _args(**kw):
    base = {"mode": "session", "query": None, "budget": None, "project": None,
            "types": None, "host": "generic", "format": "text"}
    base.update(kw)
    return argparse.Namespace(**base)


# ── 三态 × 两格式 ──────────────────────────────────────────────────────────────


def test_session_ok_text_marker_and_selection(kb):
    res = build_context_result(_args(), kb)
    assert res["status"] == "ok"
    assert res["content"].startswith("<!-- agenote-context v1 mode=session budget=4000 -->")
    assert res["content"].rstrip("\n").endswith(MORE_GUIDE)
    assert "[U|user] 回复用中文" in res["content"]  # 一行式：[类型|scope] 标题 — 首行
    assert "[E|machine] 本机 Guix 系统" in res["content"]
    assert "异机条目" not in res["content"]  # 异机 E 条目不进简报
    assert "旧纠正" not in res["content"]  # deprecated 终态不进简报
    assert "[F|user] cat 大文件卡顿" in res["content"]


def test_session_json_fields_and_content_equals_text(kb, capsys):
    text_res = build_context_result(_args(), kb)
    from agenote.context import cmd_context

    cmd_context(_args(format="json"), kb)
    res = json.loads(capsys.readouterr().out)
    assert {"status", "marker", "mode", "budget", "truncated", "entries", "content"} <= set(res)
    assert res["content"] == text_res["content"]  # content 恒等于 text 正文
    assert res["entries"][0]["id"] == "U001"  # id 走 json entries 字段


def test_empty_text_output_is_zero_bytes(kb, monkeypatch, capsys):
    from agenote.context import cmd_context

    empty_mem = kb.root / "EMPTY.org"
    empty_mem.write_text("#+title: MEMORY-empty\n\n* feedback\n\n", encoding="utf-8")
    ctx = types.SimpleNamespace(memory_org=empty_mem, root=kb.root)
    cmd_context(_args(), ctx)
    assert capsys.readouterr().out == ""  # 零字节，空 additionalContext 不注入
    cmd_context(_args(format="json"), ctx)
    assert json.loads(capsys.readouterr().out)["status"] == "empty"


def test_missing_memory_org_is_empty(kb, capsys):
    from agenote.context import cmd_context

    cmd_context(_args(), types.SimpleNamespace(
        memory_org=kb.root / "nope.org", root=kb.root))
    assert capsys.readouterr().out == ""


def test_disabled_total_switch_zero_bytes(kb, monkeypatch, capsys):
    from agenote.context import cmd_context

    monkeypatch.setenv("AGENOTE_INJECTION_ENABLED", "false")
    cmd_context(_args(), kb)
    assert capsys.readouterr().out == ""
    cmd_context(_args(format="json"), kb)
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "disabled"
    assert res["marker"] == ""  # marker 仅 ok 态存在
    assert res["content"] == ""


def test_host_switch_chain(kb, monkeypatch):
    monkeypatch.setenv("AGENOTE_INJECTION_HOSTS_ZCODE_ENABLED", "false")
    assert build_context_result(_args(host="zcode"), kb)["status"] == "disabled"
    assert build_context_result(_args(host="generic"), kb)["status"] == "ok"
    assert build_context_result(_args(host="hermes"), kb)["status"] == "ok"
    # 总开关优先于 host 开关
    monkeypatch.setenv("AGENOTE_INJECTION_ENABLED", "false")
    monkeypatch.setenv("AGENOTE_INJECTION_HOSTS_HERMES_ENABLED", "true")
    assert build_context_result(_args(host="hermes"), kb)["status"] == "disabled"


# ── marker 与预算裁剪 ──────────────────────────────────────────────────────────


def test_marker_has_no_timestamp(kb):
    res = build_context_result(_args(budget=1234), kb)
    assert res["marker"] == "<!-- agenote-context v1 mode=session budget=1234 -->"
    assert res["marker"] == res["content"].split("\n")[0]


def test_budget_hard_cap_and_trim_order(kb):
    res = build_context_result(_args(budget=200), kb)
    assert len(res["content"]) <= 200  # 硬上限（含 marker）
    assert res["truncated"] is True
    assert "BM25 参数参考" not in res["content"]  # 保头降级：R 先裁
    assert "回复用中文" in res["content"]  # U 画像最后裁


def test_hard_truncate_appends_ellipsis(kb):
    res = build_context_result(_args(budget=60), kb)
    assert len(res["content"]) <= 60
    assert res["truncated"] is True
    assert res["content"].endswith("…")


def test_zero_budget_rejected(kb):
    with pytest.raises(SystemExit):
        build_context_result(_args(budget=0), kb)


# ── recall 模式 ────────────────────────────────────────────────────────────────


def test_recall_ok_with_scores(kb, capsys):
    from agenote.context import cmd_context

    cmd_context(_args(mode="recall", query="rg 还是 grep 检索", format="json"), kb)
    res = json.loads(capsys.readouterr().out)
    assert res["status"] == "ok"
    assert res["mode"] == "recall"
    assert res["entries"], "小语料放宽下限后应有命中"
    assert all("score" in e for e in res["entries"])
    top = res["entries"][0]
    assert top["id"] == "F002"  # rg/grep 词命中
    assert res["content"].startswith("<!-- agenote-context v1 mode=recall budget=4000 -->")


def test_recall_min_score_filters_all(kb, monkeypatch, capsys):
    from agenote.context import cmd_context

    monkeypatch.setenv("AGENOTE_INJECTION_RECALL_MIN_SCORE", "1000000")
    cmd_context(_args(mode="recall", query="rg 还是 grep 检索"), kb)
    assert capsys.readouterr().out == ""  # 低于下限的结果不输出 → 零字节
    cmd_context(_args(mode="recall", query="rg 还是 grep 检索", format="json"), kb)
    assert json.loads(capsys.readouterr().out)["status"] == "empty"


def test_recall_topk_limit(kb, monkeypatch, capsys):
    from agenote.context import cmd_context

    monkeypatch.setenv("AGENOTE_INJECTION_RECALL_TOPK", "1")
    cmd_context(_args(mode="recall", query="rg 还是 grep 检索", format="json"), kb)
    assert len(json.loads(capsys.readouterr().out)["entries"]) == 1


def test_recall_short_query_gate(kb, monkeypatch):
    monkeypatch.setenv("AGENOTE_INJECTION_RECALL_MIN_QUERY", "6")
    res = build_context_result(_args(mode="recall", query="继续"), kb)
    assert res["status"] == "empty"  # 短 prompt 不注入（注入器预检同款）


def test_recall_requires_query(kb):
    with pytest.raises(SystemExit):
        build_context_result(_args(mode="recall"), kb)


# ── --project 确定性三步 ───────────────────────────────────────────────────────


def test_project_cwd_exact_path_hit(kb, monkeypatch):
    projdir = kb.root / "agenote-repo"
    monkeypatch.chdir(projdir)
    res = build_context_result(_args(), kb)
    assert "[P|project] 提交前跑 pytest" in res["content"]  # 索引行命中→P001 跟随


def test_project_git_root_basename_hit(kb, monkeypatch):
    repo = kb.root / "myproj"
    (repo / ".git").mkdir(parents=True)
    mem = kb.root / "MEMORY.org"
    text = (TEXT.format(projdir=kb.root / "elsewhere")
            .replace("** agenote\n", "** myproj\n")
            .replace(":PROJECT:  agenote", ":PROJECT:  myproj"))
    mem.write_text(text, encoding="utf-8")
    monkeypatch.chdir(repo)
    res = build_context_result(_args(), kb)
    assert "[P|project] 提交前跑 pytest" in res["content"]


def test_project_miss_no_guess(kb, monkeypatch):
    outside = kb.root / "unrelated"
    outside.mkdir()
    monkeypatch.chdir(outside)  # 无 .git、路径不等 → 不含 P 段
    res = build_context_result(_args(), kb)
    assert res["status"] == "ok"
    assert "[P|" not in res["content"]


def test_project_explicit_name_hit(kb, monkeypatch):
    outside = kb.root / "unrelated"
    outside.mkdir()
    monkeypatch.chdir(outside)
    res = build_context_result(_args(project="agenote"), kb)
    assert "[P|project] 提交前跑 pytest" in res["content"]  # 显式名精确匹配（索引行 id）


# ── 类型过滤 / 正文捕获 / 配置默认值 ──────────────────────────────────────────


def test_types_filter(kb):
    res = build_context_result(_args(types="U"), kb)
    assert "回复用中文" in res["content"]
    assert "cat 大文件卡顿" not in res["content"]
    assert "BM25 参数参考" not in res["content"]


def test_unknown_type_rejected(kb):
    with pytest.raises(SystemExit):
        build_context_result(_args(types="U,X"), kb)


def test_entry_body_captured_for_corpus(kb):
    from agenote.memory import _iter_memory_entries, _read_memory_org_text

    entries = _iter_memory_entries(_read_memory_org_text(kb))
    u001 = next(e for e in entries if e["id"] == "U001")
    assert "正文段落补充说明中文语料" in u001["body"]

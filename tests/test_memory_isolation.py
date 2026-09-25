# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""记忆隔离与写入侧门禁回归（对照 agent 记忆系统技术报告）：

- 写入侧 secret 门禁：add / memory --add / update --append / inbox-archive
  直入 SSOT 的内容先过 SECRET_PATTERNS，--allow-secret 显式豁免；
- :SENSITIVITY: 非空条目不离开 SSOT（context 注入与 export 投影同口径）；
- :PROJECT: 分区键：import 保留源侧 projects/<slug>，zcode 哈希尾缀 /
  claude 消毒路径 slug 可判定，linked worktree 与主仓共享项目身份，
  非 P 条目的分区键同样门禁注入；
- 生命周期字段：touch 递增 USAGE_COUNT、archive/supersede 落 ARCHIVED_AT
  与 SUPERSEDED_BY 墓碑。
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import types

import pytest

import agenote.core as core
from agenote.context import build_context_result

BASE_ORG = """#+title: MEMORY-test

* user
** U001 回复用中文
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     U
   :SCOPE:    user
   :END:
   # 用户要求所有回复一律使用中文

* feedback
** F001 提交前跑 pytest
   :PROPERTIES:
   :CREATED:  [2026-09-05]
   :UPDATED:  [2026-09-05]
   :TYPE:     F
   :SCOPE:    user
   :END:
   # 提交前必须跑全量 pytest

* project
** {projname}
   :PROPERTIES:
   :PATH:     {projdir}
   :UPDATED:  [2026-09-20]
   :END:
** P001 本项目提交规范
   :PROPERTIES:
   :CREATED:  [2026-09-05]
   :UPDATED:  [2026-09-05]
   :TYPE:     P
   :SCOPE:    project
   :PROJECT:  {projname}
   :END:
   # 本项目提交前必须全量测试通过

* deprecated
"""

SECRET = "sk-ant-api03-abcdefghij1234567890"


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """KB_ROOT 指到 tmp 的最小记忆库；project 节挂一个真实存在的项目目录。"""
    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    monkeypatch.setenv("AGENOTE_MACHINE_KEY", "testhost")
    monkeypatch.setenv("AGENOTE_INJECTION_RECALL_MIN_SCORE", "0.1")
    monkeypatch.setenv("AGENOTE_MEM_SECRET_SCAN", "true")  # 与配置文件无关的确定性
    projdir = tmp_path / "agenote-repo"
    (projdir / ".git").mkdir(parents=True)
    mem = tmp_path / "MEMORY.org"
    mem.write_text(
        BASE_ORG.format(projname="agenote-repo", projdir=projdir),
        encoding="utf-8",
    )
    return types.SimpleNamespace(
        memory_org=mem,
        memory_archive=tmp_path / "MEMORY-ARCHIVE.org",
        root=tmp_path,
        experiences=tmp_path / "experiences",
        memories=tmp_path / "memories",
        projects=tmp_path / "projects",
        index=tmp_path / "index.json",
        inbox=tmp_path / "inbox.org",
        agent_name="",
        name="test",
        default_weight=1.0,
    )


def _ctx_args(**kw):
    base = {"mode": "session", "query": None, "budget": None, "project": None,
            "types": None, "host": "generic", "format": "text"}
    base.update(kw)
    return argparse.Namespace(**base)


def _add_args(**kw):
    base = {"type": "feedback", "title": None, "stdin": False, "project": None,
            "sensitivity": None, "allow_secret": False, "ref": None}
    base.update(kw)
    return argparse.Namespace(**base)


# ── project_key_matches：源 slug 判定 ─────────────────────────────────────────


def test_project_key_matches_slug_forms():
    from agenote.memory import project_key_matches

    assert project_key_matches("agenote", "agenote")
    assert project_key_matches("agenote-0123456789abcdef", "agenote")  # zcode/reasonix
    assert project_key_matches("-home-u-agenote", "agenote")  # claude 消毒路径
    assert project_key_matches("-home-u-agenote-pi", "agenote-pi")
    assert not project_key_matches("agenote2", "agenote")
    assert not project_key_matches("agenote-0123456789abcdef", "agenote2")
    assert not project_key_matches("", "agenote")
    assert not project_key_matches("agenote", "")
    assert not project_key_matches("-home-u-agenote", "note")  # 末段链后缀防子串误命中


# ── import：project slug 进 :PROJECT: ─────────────────────────────────────────


def _src(monkeypatch, tmp_path, files: dict, env: str = "ZCODE_MEMORIES_DIR"):
    root = tmp_path / "srcmem"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    monkeypatch.setenv(env, str(root))
    return root


FM = """---
name: {name}
description: {desc}
metadata:
  type: feedback
---

{body}
"""


def test_import_preserves_project_slug(kb, monkeypatch):
    """projects/<slug>/memory 导入后 :PROJECT: 落 slug（workspace_identity 不改写）。"""
    from agenote.memory_import import run_import

    _src(monkeypatch, kb.root, {
        "projects/myproj-a1b2c3d4e5f60708/memory/note.md": FM.format(
            name="项目内的评审流程记忆条目",
            desc="slug 保留",
            body="这条记忆属于特定项目工作区，导入后必须保留项目分区键信息。"),
    })
    rep = run_import("zcode", ctx=kb)
    assert len(rep["imported"]) == 1
    assert rep["imported"][0]["project"] == "myproj-a1b2c3d4e5f60708"
    assert ":PROJECT:  myproj-a1b2c3d4e5f60708" in kb.memory_org.read_text(encoding="utf-8")


def test_import_flat_source_no_project(kb, monkeypatch):
    """扁平布局源（codex memories/*.md）无项目分区，不写 :PROJECT:。"""
    from agenote.memory_import import run_import

    _src(monkeypatch, kb.root, {
        "memories/note.md": FM.format(
            name="全局编码习惯偏好记忆条目",
            desc="扁平",
            body="这是一条没有项目分区的全局记忆，导入后不应携带任何 PROJECT 属性。"),
    }, env="CODEX_HOME")
    rep = run_import("codex", ctx=kb)
    assert len(rep["imported"]) == 1
    assert rep["imported"][0]["project"] == ""
    text = kb.memory_org.read_text(encoding="utf-8")
    body = text.split("全局编码习惯偏好记忆条目", 1)[1]
    assert ":PROJECT:" not in body.split("** ", 1)[0]


# ── context：项目分区隔离 ─────────────────────────────────────────────────────


def test_slug_project_entry_injected_in_repo(kb, monkeypatch):
    """zcode slug :PROJECT: 在 basename 命中的仓库内注入。"""
    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="agenote-repo", projdir=kb.root / "agenote-repo")
                   .replace(":PROJECT:  agenote-repo",
                            ":PROJECT:  agenote-repo-0123456789abcdef"),
                   encoding="utf-8")
    monkeypatch.chdir(kb.root / "agenote-repo")
    res = build_context_result(_ctx_args(), kb)
    assert "[P|project] 本项目提交规范" in res["content"]


def test_claude_sanitized_slug_injected_in_plain_dir(kb, monkeypatch):
    """claude 消毒路径 slug 在 cwd 路径级全等命中（非 git 目录也成立）。"""
    from agenote.context import _sanitize_workspace_path

    workdir = kb.root / "plain-workdir"
    workdir.mkdir()
    slug = _sanitize_workspace_path(workdir.resolve())
    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="agenote-repo", projdir=kb.root / "agenote-repo")
                   .replace(":PROJECT:  agenote-repo", f":PROJECT:  {slug}"),
                   encoding="utf-8")
    monkeypatch.chdir(workdir)  # 无 .git：name_targets 为空，仅靠 sanitized 路径全等
    res = build_context_result(_ctx_args(), kb)
    assert "[P|project] 本项目提交规范" in res["content"]


def test_worktree_shares_main_repo_identity(kb, monkeypatch):
    """linked worktree 的 .git gitfile 解析回主仓根，共享同一项目身份。"""
    from agenote.context import _git_root_path

    main_repo = kb.root / "mainrepo"
    (main_repo / ".git" / "worktrees" / "wt1").mkdir(parents=True)
    wt = kb.root / "wt1"
    wt.mkdir()
    (wt / ".git").write_text(
        f"gitdir: {main_repo / '.git' / 'worktrees' / 'wt1'}\n", encoding="utf-8")

    assert _git_root_path(wt) == main_repo

    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="mainrepo", projdir=main_repo),
                   encoding="utf-8")
    monkeypatch.chdir(wt)
    res = build_context_result(_ctx_args(), kb)
    assert "[P|project] 本项目提交规范" in res["content"]


def test_project_scope_gate_non_p_entry(kb, monkeypatch):
    """非 P 条目带 :PROJECT: 时仅在对应项目上下文注入（分区不按类型豁免）。"""
    scoped = """
** U009 绑定项目的偏好
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     U
   :SCOPE:    user
   :PROJECT:  agenote-repo
   :END:
   # 仅 agenote-repo 生效的偏好
"""
    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="agenote-repo", projdir=kb.root / "agenote-repo")
                   .replace("\n* feedback", scoped + "\n* feedback"),
                   encoding="utf-8")

    outside = kb.root / "unrelated"
    outside.mkdir()
    monkeypatch.chdir(outside)  # 无 git、无 --project → 分区键条目不注入
    res = build_context_result(_ctx_args(), kb)
    assert "绑定项目的偏好" not in res["content"]
    assert "回复用中文" in res["content"]  # 无分区键条目不受影响

    monkeypatch.chdir(kb.root / "agenote-repo")  # 项目内 → 注入
    res = build_context_result(_ctx_args(), kb)
    assert "绑定项目的偏好" in res["content"]


def test_project_scope_gate_recall(kb, monkeypatch, capsys):
    """recall 语料同样过 :PROJECT: 门禁。"""
    from agenote.context import cmd_context

    scoped = """
** U009 绑定项目的偏好
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     U
   :SCOPE:    user
   :PROJECT:  agenote-repo
   :END:
   # 仅 agenote-repo 生效的偏好夜莺代号
"""
    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="agenote-repo", projdir=kb.root / "agenote-repo")
                   .replace("\n* feedback", scoped + "\n* feedback"),
                   encoding="utf-8")
    outside = kb.root / "unrelated"
    outside.mkdir()
    monkeypatch.chdir(outside)
    cmd_context(_ctx_args(mode="recall", query="夜莺代号", format="json"), kb)
    res = json.loads(capsys.readouterr().out)
    assert all("夜莺" not in e.get("title", "") for e in res["entries"])


# ── :SENSITIVITY: 不离开 SSOT ─────────────────────────────────────────────────

SENS_BLOCK = """
** U010 内部代号
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     U
   :SCOPE:    user
   :SENSITIVITY: private
   :END:
   # 内部代号夜莺不外发
"""


def test_sensitivity_excluded_from_session(kb, monkeypatch):
    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="agenote-repo", projdir=kb.root / "agenote-repo")
                   .replace("\n* feedback", SENS_BLOCK + "\n* feedback"),
                   encoding="utf-8")
    monkeypatch.chdir(kb.root / "agenote-repo")
    res = build_context_result(_ctx_args(), kb)
    assert "内部代号" not in res["content"]
    assert "回复用中文" in res["content"]


def test_sensitivity_excluded_from_export(kb):
    """projector._select_entries 与 context 同口径：受限条目不出 SSOT。"""
    from agenote.memory import _iter_memory_entries
    from agenote.projector import _select_entries

    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="agenote-repo", projdir=kb.root / "agenote-repo")
                   .replace("\n* feedback", SENS_BLOCK + "\n* feedback"),
                   encoding="utf-8")
    entries = _iter_memory_entries(mem.read_text(encoding="utf-8"))
    args = types.SimpleNamespace(type=None, scope=None, project="agenote-repo")
    ids = {e["id"] for e in _select_entries(args, entries)}
    assert "U010" not in ids
    assert {"U001", "F001", "P001"} <= ids


def test_export_project_scope_gate(kb):
    """export 时带 :PROJECT: 的非 P 条目只在 --project 命中其键时投影。"""
    from agenote.memory import _iter_memory_entries
    from agenote.projector import _select_entries

    scoped = """
** F009 项目限定反馈
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     F
   :SCOPE:    user
   :PROJECT:  otherproj-a1b2c3d4e5f60708
   :END:
   # 项目限定反馈正文
"""
    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="agenote-repo", projdir=kb.root / "agenote-repo")
                   .replace("\n* project", scoped + "\n* project"),
                   encoding="utf-8")
    entries = _iter_memory_entries(mem.read_text(encoding="utf-8"))
    no_proj = types.SimpleNamespace(type=None, scope=None, project=None)
    assert "F009" not in {e["id"] for e in _select_entries(no_proj, entries)}
    wrong = types.SimpleNamespace(type=None, scope=None, project="agenote-repo")
    assert "F009" not in {e["id"] for e in _select_entries(wrong, entries)}
    # 去哈希后缀等值：--project otherproj 命中 slug 键
    hit = types.SimpleNamespace(type=None, scope=None, project="otherproj")
    assert "F009" in {e["id"] for e in _select_entries(hit, entries)}


# ── memory --add：分区/敏感属性落盘 + 写入侧门禁 ──────────────────────────────


def test_memory_add_writes_project_and_sensitivity(kb):
    from agenote.memory import _iter_memory_entries, _memory_add

    _memory_add(_add_args(title="项目限定规范", project="myproj",
                          sensitivity="private"), kb)
    entries = _iter_memory_entries(kb.memory_org.read_text(encoding="utf-8"))
    new = next(e for e in entries if e["title"] == "项目限定规范")
    assert new["type"] == "F"
    assert new["props"]["PROJECT"] == "myproj"
    assert new["props"]["SENSITIVITY"] == "private"


def test_memory_add_secret_blocked(kb):
    from agenote.memory import _memory_add

    before = kb.memory_org.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        _memory_add(_add_args(title=f"密钥 {SECRET}"), kb)
    assert kb.memory_org.read_text(encoding="utf-8") == before
    # --allow-secret 显式豁免（人工裁决）
    _memory_add(_add_args(title=f"密钥 {SECRET}", allow_secret=True), kb)
    assert SECRET in kb.memory_org.read_text(encoding="utf-8")


def test_gate_secret_write_respects_config_switch(kb, monkeypatch):
    """[memories].secret_scan_enabled=false 时门禁关闭（与 import/export 同口径）。"""
    monkeypatch.setenv("AGENOTE_MEM_SECRET_SCAN", "false")
    from agenote.memory import _memory_add

    _memory_add(_add_args(title=f"密钥 {SECRET}"), kb)
    assert SECRET in kb.memory_org.read_text(encoding="utf-8")


def test_add_card_secret_blocked(kb):
    """agenote add 直入卡片前过门禁：命中即拒写，不落盘。"""
    from agenote.cards import cmd_add

    args = argparse.Namespace(
        title=f"记录密钥 {SECRET}", category=None, tech=None, type="workflow",
        owner="ai", entry=None, summary="", stdin=False, force=False,
        allow_secret=False,
    )
    with pytest.raises(SystemExit):
        cmd_add(args, kb)
    assert not list(kb.experiences.rglob("*.org"))


def test_update_append_secret_blocked(kb):
    """update --append 的追加文本同样过门禁；拒绝时原卡片不变。"""
    from agenote.cards import cmd_update

    card = kb.experiences / "misc" / "20260101-1-workflow-misc.org"
    card.parent.mkdir(parents=True)
    original = ("* DONE 既有卡片\n:PROPERTIES:\n:ID:       20260101-1\n"
                ":TYPE:     workflow\n:STATUS:   done\n:END:\n\n正文\n")
    card.write_text(original, encoding="utf-8")
    args = argparse.Namespace(
        target=str(card), status=None, category=None, tech=None, type_=None,
        owner=None, append_to="排查过程", append_text=f"密钥 {SECRET}",
        force=False, stdin=False, allow_secret=False,
    )
    with pytest.raises(SystemExit):
        cmd_update(args, kb)
    assert card.read_text(encoding="utf-8") == original


def test_inbox_archive_secret_skips_single_entry(kb, monkeypatch, capsys):
    """inbox-archive 逐条门禁：命中条目跳过并告警，不阻断整批。"""
    from agenote.inbox_archive import cmd_inbox_archive

    payload = json.dumps([
        {"heading": "正常归档条目", "body": "这条经验内容正常归档入库。"},
        {"heading": "带密钥的条目", "body": f"密钥 {SECRET} 不应进卡片"},
    ])
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    args = argparse.Namespace(category="misc", reason=None, no_reindex=True,
                              prune=False, stdin=True, allow_secret=False)
    cmd_inbox_archive(args, kb)
    err = capsys.readouterr().err
    assert "secret" in err and SECRET not in err  # 只报类别不回显值
    written = list(kb.experiences.rglob("*.org"))
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert "正常归档条目" in text and SECRET not in text


# ── 生命周期字段：USAGE_COUNT / ARCHIVED_AT / SUPERSEDED_BY ───────────────────


def test_touch_increments_usage_count(kb):
    from agenote.memory import _iter_memory_entries, _memory_touch

    _memory_touch("F001", kb)
    e = next(x for x in _iter_memory_entries(kb.memory_org.read_text(encoding="utf-8"))
             if x["id"] == "F001")
    assert e["props"]["USAGE_COUNT"] == "1"
    _memory_touch("F001", kb)
    e = next(x for x in _iter_memory_entries(kb.memory_org.read_text(encoding="utf-8"))
             if x["id"] == "F001")
    assert e["props"]["USAGE_COUNT"] == "2"


def test_archive_sets_archived_at(kb):
    from agenote.memory import _iter_memory_entries, _memory_archive

    _memory_archive("F001", kb)
    e = next(x for x in _iter_memory_entries(kb.memory_org.read_text(encoding="utf-8"))
             if x["id"] == "F001")
    assert e["section"].lower() == "deprecated"
    assert (e["props"].get("ARCHIVED_AT") or "").strip("[] ") != ""


def test_supersede_marks_tombstone(kb):
    from agenote.memory import _iter_memory_entries, _memory_supersede

    _memory_add_args = _add_args(type="feedback", title="新的结论条目")
    from agenote.memory import _memory_add
    _memory_add(_memory_add_args, kb)  # F002 新条目
    _memory_supersede("F002", "F001", kb)
    entries = {e["id"]: e for e in
               _iter_memory_entries(kb.memory_org.read_text(encoding="utf-8"))}
    assert entries["F002"]["props"]["SUPERSEDES"] == "F001"
    old = entries["F001"]
    assert old["section"].lower() == "deprecated"
    assert old["props"]["SUPERSEDED_BY"] == "F002"
    assert (old["props"].get("ARCHIVED_AT") or "").strip("[] ") != ""


def test_memory_list_marks_sensitive(kb, capsys):
    from agenote.memory import _memory_list

    mem = kb.root / "MEMORY.org"
    mem.write_text(BASE_ORG.format(projname="agenote-repo", projdir=kb.root / "agenote-repo")
                   .replace("\n* feedback", SENS_BLOCK + "\n* feedback"),
                   encoding="utf-8")
    _memory_list(argparse.Namespace(type=None, scope=None, json=True), kb)
    rows = json.loads(capsys.readouterr().out)
    u010 = next(r for r in rows if r["id"] == "U010")
    assert u010["sensitive"] is True
    u001 = next(r for r in rows if r["id"] == "U001")
    assert u001["sensitive"] is False

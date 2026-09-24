# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""N2 `memory import` 摄取管道测试：幂等、回声、secret、疑似重复、冲突、dry-run。"""

from __future__ import annotations

import json
import types

import agenote.memory_import as mi
from agenote.memory_import import run_import

BASE_ORG = """#+title: MEMORY-test

* feedback
** F001 提交前跑 pytest
   :PROPERTIES:
   :CREATED:  [2026-09-05]
   :UPDATED:  [2026-09-05]
   :TYPE:     F
   :SCOPE:    user
   :END:
   # 提交前跑 pytest 全量测试

* reference
"""


def _ctx(tmp_path, text=BASE_ORG, monkeypatch=None):
    if monkeypatch is not None:
        monkeypatch.setattr("agenote.core.KB_ROOT", tmp_path)
    org = tmp_path / "MEMORY.org"
    org.write_text(text, encoding="utf-8")
    return types.SimpleNamespace(memory_org=org, root=tmp_path)


def _src(monkeypatch, tmp_path, files: dict):
    root = tmp_path / "srcmem"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    monkeypatch.setenv("ZCODE_MEMORIES_DIR", str(root))
    return root


FM = """---
name: {name}
description: {desc}
metadata:
  type: feedback
---

{body}
"""


def test_import_basic_writes_entry(tmp_path, monkeypatch):
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/code-review.md": FM.format(
            name="代码评审要跑完整测试套件",
            desc="评审流程记忆",
            body="代码评审的时候必须运行完整的测试套件来验证所有的修改内容没有破坏现有功能。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert len(rep["imported"]) == 1
    assert rep["skipped"] == rep["suspected_dup"] == rep["conflicted"] == rep["secret_blocked"] == []
    text = ctx.memory_org.read_text(encoding="utf-8")
    assert "代码评审要跑完整测试套件" in text
    assert ":ORIGIN_ID:" in text and ":ORIGIN_AGENT: zcode" in text


def test_import_idempotent_origin_skip(tmp_path, monkeypatch):
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/code-review.md": FM.format(
            name="代码评审要跑完整测试套件",
            desc="评审流程记忆",
            body="代码评审的时候必须运行完整的测试套件来验证所有的修改内容没有破坏现有功能。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    run_import("zcode", ctx=ctx)
    rep2 = run_import("zcode", ctx=ctx)
    assert rep2["imported"] == []
    assert len(rep2["skipped"]) == 1 and rep2["skipped"][0]["reason"] == "origin_id"
    # 幂等：条目只写一次
    assert ctx.memory_org.read_text(encoding="utf-8").count("代码评审要跑完整测试套件") == 1


def test_import_echo_excluded(tmp_path, monkeypatch):
    root = _src(monkeypatch, tmp_path, {
        "projects/p/memory/echo-note.md": FM.format(
            name="自家投影回声条目内容足够长才不会被噪声过滤",
            desc="echo",
            body="这是投影回声文件的内容正文部分，用来验证回声排除逻辑是否正常工作生效。"),
    })
    monkeypatch.setattr(mi, "_echo_prefixes", lambda: [root])
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert rep["imported"] == []
    assert len(rep["skipped"]) == 1 and rep["skipped"][0]["reason"] == "echo"


def test_import_secret_blocked_no_value_leak(tmp_path, monkeypatch):
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/leak.md": FM.format(
            name="部署密钥记录",
            desc="含密钥",
            body="生产环境部署需要的密钥是 sk-ant-api03-abcdefghij1234567890 请妥善保管不要外泄。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert len(rep["secret_blocked"]) == 1
    assert rep["secret_blocked"][0]["category"] == "anthropic_key"
    assert rep["imported"] == []
    dumped = json.dumps(rep, ensure_ascii=False)
    assert "sk-ant-api03" not in dumped  # 只记类别不记值
    assert "sk-ant-api03" not in ctx.memory_org.read_text(encoding="utf-8")


def test_import_suspected_dup(tmp_path, monkeypatch):
    # 与 F001 同 TYPE+SCOPE、标题高度相似、正文相似 → 疑似重复
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/dup.md": FM.format(
            name="提交前跑 pytest",
            desc="重复",
            body="提交前跑 pytest 全量测试验证修改。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert rep["imported"] == []
    assert len(rep["suspected_dup"]) == 1
    assert rep["suspected_dup"][0]["existing"] == "F001"
    assert "提交前跑 pytest 全量测试验证修改" not in ctx.memory_org.read_text(encoding="utf-8")


def test_import_conflict_queued(tmp_path, monkeypatch):
    # 标题相似但正文实质不同 → 冲突队列，不写 MEMORY
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/conflict.md": FM.format(
            name="提交前跑 pytest",
            desc="冲突",
            body="完全不同的主张：提交前只需要做代码格式检查就可以了，配合自动化工具链使用。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert rep["imported"] == [] and len(rep["conflicted"]) == 1
    qpath = tmp_path / ".memory-conflicts.json"
    queued = json.loads(qpath.read_text(encoding="utf-8"))
    assert len(queued) == 1 and queued[0]["existing"]["id"] == "F001"
    assert "只需要做代码格式检查" not in ctx.memory_org.read_text(encoding="utf-8")
    # --conflicts 口径：只读列出
    assert mi.load_conflicts(ctx)[0]["candidate"]["title"] == "提交前跑 pytest"


def test_import_dry_run_no_write(tmp_path, monkeypatch):
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/new-idea.md": FM.format(
            name="全新的工作流想法需要被记录下来",
            desc="新想法",
            body="这是一个全新的工作流想法，它的内容足够长因此可以通过噪声过滤器的检查。"),
        "projects/p/memory/conflict.md": FM.format(
            name="提交前跑 pytest",
            desc="冲突",
            body="完全不同的主张：提交前只需要做代码格式检查就可以了，配合自动化工具链使用。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    before = ctx.memory_org.read_text(encoding="utf-8")
    rep = run_import("zcode", dry_run=True, ctx=ctx)
    assert len(rep["imported"]) == 1 and len(rep["conflicted"]) == 1
    assert ctx.memory_org.read_text(encoding="utf-8") == before
    assert not (tmp_path / ".memory-conflicts.json").exists()

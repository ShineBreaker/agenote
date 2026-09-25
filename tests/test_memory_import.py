# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""N2 `memory import` 摄取管道测试：幂等、回声、secret、疑似重复、冲突、dry-run。"""

from __future__ import annotations

import json
import re
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

FM_E = """---
name: {name}
description: {desc}
metadata:
  type: environment
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


def test_import_echo_excluded_real_config_chain(tmp_path, monkeypatch):
    """回声排除走真实配置链路：targets env → projector 前缀 → 扫描路径，不 monkeypatch。"""
    root = _src(monkeypatch, tmp_path, {
        "projects/p/memory/echo-note.md": FM.format(
            name="自家投影回声条目内容足够长才不会被噪声过滤",
            desc="echo",
            body="这是投影回声文件的内容正文部分，用来验证回声排除逻辑是否正常工作生效。"),
    })
    # 源根就在投影目标树内（记忆库目录被误配进 targets 的真实事故形态）
    monkeypatch.setenv("AGENOTE_ZCODE_DIR", str(tmp_path))
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert rep["imported"] == []
    assert len(rep["skipped"]) == 1 and rep["skipped"][0]["reason"] == "echo"


def test_import_echo_excluded_via_symlink_prefix(tmp_path, monkeypatch):
    """targets 配置写 symlink 拼写时仍命中（realpath 归一，C7 P0）。"""
    root = _src(monkeypatch, tmp_path, {
        "projects/p/memory/echo-note.md": FM.format(
            name="自家投影回声条目内容足够长才不会被噪声过滤",
            desc="echo",
            body="这是投影回声文件的内容正文部分，用来验证回声排除逻辑是否正常工作生效。"),
    })
    link = tmp_path / "target-link"
    link.symlink_to(tmp_path)
    monkeypatch.setenv("AGENOTE_ZCODE_DIR", str(link))
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert rep["imported"] == []
    assert rep["skipped"][0]["reason"] == "echo"


def test_import_rejects_projection_marker_aggregate(tmp_path, monkeypatch):
    """P0 双保险：targets 失配时，聚合投影 frontmatter marker 仍拒收（不 ingest）。"""
    marker = "x-agenote-projected: " + "a" * 64
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/agenote-profile.md": (
            f"---\nname: agenote-profile\ndescription: d\ntype: project\n{marker}\n---\n"
            "# agenote memories\n\n## feedback\n- ** F001 条目标题足够长避免噪声过滤\n"
            "  # 投影正文不应被再次摄取进单一真相源。够长的正文。\n"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert rep["imported"] == []
    assert rep["skipped"][0]["reason"] == "echo"
    assert "投影正文不应被再次摄取" not in ctx.memory_org.read_text(encoding="utf-8")


def test_import_rejects_projection_marker_reasonix_tail(tmp_path, monkeypatch):
    """reasonix 尾注 `<!-- x-agenote-projected: ... -->` 同口径拒收。"""
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/agenote-F001.md": (
            "---\nname: agenote-F001\ntitle: 投影条目标题足够长避免噪声过滤\n---\n"
            "- ** F001 投影条目标题足够长避免噪声过滤\n"
            "  # 正文摘要足够长，验证尾注 marker 命中回声拒收逻辑。\n"
            "<!-- x-agenote-projected: beefbeef（agenote SSOT 投影） -->\n"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert rep["imported"] == []
    assert rep["skipped"][0]["reason"] == "echo"


def test_import_writes_origin_path_and_machine(tmp_path, monkeypatch):
    """import 产物补 N4/N5 属性：ORIGIN_PATH 落盘、E 条目带 MACHINE。"""
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/code-review.md": FM.format(
            name="代码评审要跑完整测试套件",
            desc="评审流程记忆",
            body="代码评审的时候必须运行完整的测试套件来验证所有的修改内容没有破坏现有功能。"),
        "projects/p/memory/env-note.md": FM_E.format(
            name="本机内核版本与驱动布局说明",
            desc="环境",
            body="当前机器的内核版本为定制构建，驱动目录布局与上游发行版默认布局不同，排查硬件问题时要先确认。"),
    })
    from agenote.memory import resolve_machine_key

    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    run_import("zcode", ctx=ctx)
    text = ctx.memory_org.read_text(encoding="utf-8")
    assert ":ORIGIN_PATH:" in text
    assert f"{tmp_path / 'srcmem' / 'projects/p/memory/code-review.md'}" in text
    assert ":MACHINE:" in text
    m = re.search(r":MACHINE:\s+(\S+)", text)
    assert m and m.group(1) == resolve_machine_key()


def test_import_orphan_detected_after_source_removal(tmp_path, monkeypatch, capsys):
    """N4 接通：import 条目的源文件消失后，--revalidate 列为 orphan。"""
    root = _src(monkeypatch, tmp_path, {
        "projects/p/memory/code-review.md": FM.format(
            name="代码评审要跑完整测试套件",
            desc="评审流程记忆",
            body="代码评审的时候必须运行完整的测试套件来验证所有的修改内容没有破坏现有功能。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    run_import("zcode", ctx=ctx)
    (root / "projects/p/memory/code-review.md").unlink()

    from agenote.memory import _memory_revalidate

    _memory_revalidate(ctx)
    out = capsys.readouterr().out
    assert "orphan" in out and "代码评审要跑完整测试套件" in out


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

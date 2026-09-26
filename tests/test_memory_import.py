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


def test_import_id_survives_source_root_rename(tmp_path, monkeypatch):
    """ORIGIN_ID 相对源根派生：源根改名后重复 import 仍幂等（不再全量碎裂）。"""
    root = _src(monkeypatch, tmp_path, {
        "projects/p/memory/code-review.md": FM.format(
            name="代码评审要跑完整测试套件",
            desc="评审流程记忆",
            body="代码评审的时候必须运行完整的测试套件来验证所有的修改内容没有破坏现有功能。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    run_import("zcode", ctx=ctx)
    renamed = tmp_path / "renamed-mem"
    root.rename(renamed)
    monkeypatch.setenv("ZCODE_MEMORIES_DIR", str(renamed))
    rep2 = run_import("zcode", ctx=ctx)
    assert rep2["imported"] == []
    assert len(rep2["skipped"]) == 1 and rep2["skipped"][0]["reason"] == "origin_id"


def test_import_legacy_absolute_id_still_skipped(tmp_path, monkeypatch):
    """迁移过渡期双算：存量旧绝对路径 ID 的条目重复 import 仍幂等跳过。"""
    from agenote.memory import origin_id_legacy

    root = _src(monkeypatch, tmp_path, {
        "projects/p/memory/code-review.md": FM.format(
            name="代码评审要跑完整测试套件",
            desc="评审流程记忆",
            body="代码评审的时候必须运行完整的测试套件来验证所有的修改内容没有破坏现有功能。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    legacy = origin_id_legacy("zcode", str(root / "projects/p/memory/code-review.md"),
                              "代码评审要跑完整测试套件")
    legacy_block = (
        "\n** F099 存量旧键条目\n   :PROPERTIES:\n"
        f"   :ORIGIN_ID: {legacy}\n   :ORIGIN_AGENT: zcode\n   :END:\n"
        "   代码评审的时候必须运行完整的测试套件来验证所有的修改内容没有破坏现有功能。\n")
    # 用旧标题构造：normalize 的标题来自源 name，保持一致才命中 origin 检查
    legacy_block = legacy_block.replace("F099 存量旧键条目", "F099 代码评审要跑完整测试套件")
    with open(ctx.memory_org, "a", encoding="utf-8") as f:
        f.write(legacy_block)
    rep = run_import("zcode", ctx=ctx)
    assert rep["imported"] == []
    assert any(s["reason"] == "origin_id" for s in rep["skipped"])


def test_import_bom_crlf_source_normalized(tmp_path, monkeypatch):
    """BOM/CRLF 源文件：读取侧归一，frontmatter 正常解析、正文无 \\r 进 SSOT。"""
    root = tmp_path / "srcmem"
    p = root / "projects/p/memory/bom.md"
    p.parent.mkdir(parents=True)
    fm = FM.format(name="带 BOM 的条目标题也足够长避免噪声过滤",
                   desc="编码边界",
                   body="这是带 BOM 与 CRLF 换行的正文内容，用来验证读取侧归一化后写进知识库的文本是干净的。")
    p.write_bytes(b"\xef\xbb\xbf" + fm.replace("\n", "\r\n").encode("utf-8"))
    monkeypatch.setenv("ZCODE_MEMORIES_DIR", str(root))
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert len(rep["imported"]) == 1
    text = ctx.memory_org.read_text(encoding="utf-8")
    assert "\r" not in text
    assert "带 BOM 的条目标题也足够长避免噪声过滤" in text
    assert "description: 编码边界" not in text  # frontmatter 没有整段漏进正文
    assert "验证读取侧归一化" in text


def test_import_dedupe_excludes_deprecated(tmp_path, monkeypatch):
    """deprecated 终态条目不参与去重：同主题新事实正常导入而非疑似重复。"""
    dep_org = BASE_ORG + (
        "* deprecated\n"
        "** F090 部署前清理构建缓存\n"
        "   :PROPERTIES:\n"
        "   :CREATED:  [2026-01-01]\n"
        "   :UPDATED:  [2026-01-01]\n"
        "   :END:\n")
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/dup.md": FM.format(
            name="部署前清理构建缓存",
            desc="重复",
            body="部署前先清理构建缓存再重新构建产物。"),
    })
    ctx = _ctx(tmp_path, text=dep_org, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert len(rep["imported"]) == 1 and rep["suspected_dup"] == []


def test_import_persists_needs_review(tmp_path, monkeypatch):
    """启发式判型落盘 :NEEDS_REVIEW: true——报告丢失后仍可与确认判型区分。"""
    root = tmp_path / "srcmem"
    p = root / "projects/p/memory/no-type.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\nname: 用户要求回复保持简短风格\n---\n"
                 "用户明确要求所有回复保持简短风格，这是长期偏好，需要在后续会话中持续遵守执行。\n",
                 encoding="utf-8")
    monkeypatch.setenv("ZCODE_MEMORIES_DIR", str(root))
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert len(rep["imported"]) == 1 and rep["imported"][0]["needs_review"] is True
    assert ":NEEDS_REVIEW: true" in ctx.memory_org.read_text(encoding="utf-8")


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


FM_P = """---
name: {name}
description: {desc}
metadata:
  type: project
---

{body}
"""


def test_import_multi_entries_same_section_placement_and_ids(tmp_path, monkeypatch):
    """同节多条目落位与 ID 递增：block 必须按行展开插入。

    回归背景：_write_entries 曾把整块作为单元素 insert，而 target 行号是
    join 后文本行坐标——列表里出现多行块元素后坐标错位，同节第 2 条起
    全部尾插到文件尾并落入 deprecated 节、ID 恒为同值（P002×N）。
    """
    _src(monkeypatch, tmp_path, {
        "projects/p/memory/pa.md": FM_P.format(
            name="项目甲的构建前置步骤需要先同步子模块",
            desc="构建流程",
            body="项目甲构建之前必须先把全部子模块同步到位，否则构建系统会拿到过期的接口定义产生难以排查的编译错误。"),
        "projects/p/memory/pb.md": FM_P.format(
            name="项目甲的发版前必须回填变更日志",
            desc="发版流程",
            body="项目甲每次发版前必须把本版本的变更写进变更日志文件，漏写会导致下游打包流程取不到正确的版本说明。"),
        "projects/p/memory/pc.md": FM_P.format(
            name="项目甲的接口变更要同步类型定义",
            desc="接口约定",
            body="项目甲的接口发生变更时必须同步更新类型定义文件，两侧不一致会让调用方在运行期才暴露字段缺失问题。"),
        "projects/p/memory/fa.md": FM.format(
            name="反馈一：审查意见要绑定产物哈希",
            desc="审查纪律",
            body="异步审查的意见必须绑定被审产物的哈希值，否则产物在审查期间被修改后意见就失去了指向对象无法核销。"),
        "projects/p/memory/fb.md": FM.format(
            name="反馈二：探索性搜索交给子智能体",
            desc="委派纪律",
            body="大范围的探索性搜索应当交给子智能体执行并只取回结论，主会话直接翻找大量文件会消耗宝贵的上下文窗口。"),
    })
    ctx = _ctx(tmp_path, monkeypatch=monkeypatch)
    rep = run_import("zcode", ctx=ctx)
    assert len(rep["imported"]) == 5
    text = ctx.memory_org.read_text(encoding="utf-8")

    from agenote.memory import _iter_memory_entries
    entries = list(_iter_memory_entries(text))
    assert len(entries) == 6  # BASE_ORG 预置 F001 + 导入 5 条
    # 无条目落进 deprecated（bug 的直接症状）
    assert all(e["section"].lower() != "deprecated" for e in entries)
    # 各节归属正确 + 节内 ID 连续递增无重复
    by_section: dict[str, list[str]] = {}
    for e in entries:
        by_section.setdefault(e["section"].lower(), []).append(e["id"])
    assert by_section["project"] == ["P001", "P002", "P003"]
    assert by_section["feedback"] == ["F001", "F002", "F003"]
    all_ids = [e["id"] for e in entries]
    assert len(all_ids) == len(set(all_ids))

    # 幂等：复跑全部 origin_id skip
    rep2 = run_import("zcode", ctx=ctx)
    assert rep2["imported"] == []
    assert len(rep2["skipped"]) == 5

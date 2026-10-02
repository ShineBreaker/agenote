# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""S6/S7/S8/S10：orgserde 条目层 + safe_read + 时效标记 + list --json additive。"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from agenote import orgserde
from agenote.core import KBContext
from agenote.safeio import SafeReadError, safe_read_text


def _fake_ctx(root: Path) -> KBContext:
    return KBContext(
        name="agenote",
        root=root,
        experiences=root / "agenote" / "experiences",
        memories=root / "agenote" / "memories",
        projects=root / "agenote" / "memories" / "projects",
        memory_org=root / "agenote" / "MEMORY.org",
        memory_archive=root / "agenote" / "MEMORY-ARCHIVE.org",
        index=root / "agenote" / "index.json",
        inbox=root / "agenote" / "inbox.org",
        is_human=False,
        default_weight=1.0,
        agent_name="zcode",
    )


OLD_ENTRY = """** F001 旧条目无钩子
   :PROPERTIES:
   :CREATED:  [2020-01-01]
   :UPDATED:  [2020-01-02]
   :END:
   正文第一行
"""

NEW_ENTRY = """** F002 带钩子的条目
   :PROPERTIES:
   :CREATED:  [2026-09-01]
   :UPDATED:  [2026-09-01]
   :TYPE:     user_prefer
   :SCOPE:    global
   :ORIGIN:   zcode:session-1
   :END:
# 一句话钩子
   正文内容
"""


def _block(text: str) -> list[str]:
    lines = text.splitlines()
    return lines[1:]


# ── S6：条目层解析 ───────────────────────────────────────────────────────────


def test_match_memory_entry_semantics():
    assert orgserde.match_memory_entry("** F001 标题").group(1) == "F001"
    assert orgserde.match_memory_entry("** F001 标题", "F001")
    assert orgserde.match_memory_entry("** F002 标题", "F001") is None
    assert orgserde.match_memory_entry("* feedback 节") is None  # 一级节不是条目
    assert orgserde.match_memory_entry("   正文 ** F001") is None
    assert orgserde.is_memory_boundary("** F001 x")
    assert orgserde.is_memory_boundary("* feedback")
    assert not orgserde.is_memory_boundary("   正文")


def test_entry_props_tolerant_unknown_fields():
    block = _block(NEW_ENTRY)
    assert orgserde.memory_prop(block, "TYPE") == "user_prefer"
    assert orgserde.memory_prop(block, "scope") == "global"  # 大小写不敏感
    assert orgserde.memory_prop(block, "ORIGIN") == "zcode:session-1"
    assert orgserde.memory_prop(block, "不存在") == ""


def test_hook_roundtrip_and_old_entry_fallback():
    assert orgserde.read_memory_hook(_block(NEW_ENTRY)) == "一句话钩子"
    assert orgserde.read_memory_hook(_block(OLD_ENTRY)) == ""  # 旧条目不报错
    assert orgserde.entry_hook_or_title(_block(OLD_ENTRY), "旧条目无钩子") == "旧条目无钩子"
    assert orgserde.entry_hook_or_title(_block(NEW_ENTRY), "标题") == "一句话钩子"


def test_build_memory_hook_truncates():
    assert orgserde.build_memory_hook("标题", "") == "标题"
    assert orgserde.build_memory_hook("标题", "首行正文\n第二行") == "首行正文"
    long_body = "字" * 200
    assert len(orgserde.build_memory_hook("标题", long_body)) == orgserde.HOOK_MAX_CHARS


def test_set_memory_prop_line_preserves_layout():
    out = orgserde.set_memory_prop_line("   :UPDATED:  [2020-01-02]", "UPDATED", "2026-09-24")
    assert out == "   :UPDATED:  [2026-09-24]"
    assert orgserde.set_memory_prop_line("   :CREATED:  [2020-01-01]", "UPDATED", "x") is None
    assert orgserde.set_memory_prop_line("正文", "UPDATED", "x") is None


def test_parse_memory_date():
    assert orgserde.parse_memory_date("[2026-09-24]") == date(2026, 9, 24)
    assert orgserde.parse_memory_date("2026-09-24 10:00") == date(2026, 9, 24)
    assert orgserde.parse_memory_date("") is None
    assert orgserde.parse_memory_date("不是日期") is None


# ── S6 回归：add 带钩子，计数/归档行为不变 ────────────────────────────────────


def test_memory_add_writes_hook_and_archive_still_works(tmp_path, monkeypatch):
    from agenote import memory as mem

    monkeypatch.setattr("agenote.core.KB_ROOT", tmp_path / "kb")
    ctx = _fake_ctx(tmp_path / "kb")
    args = argparse.Namespace(type="feedback", title="回归条目", stdin=False, ref=None, project=None)
    mem._memory_add(args, ctx)
    text = ctx.memory_org.read_text(encoding="utf-8")
    assert "# 回归条目" in text  # 无正文时钩子降级用标题
    assert mem._next_memory_id(text, "F") == "F002"
    mem._memory_archive("F001", ctx)  # 走 orgserde 层后归档仍可用
    after = ctx.memory_org.read_text(encoding="utf-8")
    assert "* deprecated" in after and "** F001 回归条目" in after


# ── S8：safe_read ────────────────────────────────────────────────────────────


def test_safe_read_text_normal_file(tmp_path):
    p = tmp_path / "a.org"
    p.write_text("内容", encoding="utf-8")
    assert safe_read_text(p) == "内容"


def test_safe_read_text_rejects_symlink_and_dir(tmp_path):
    target = tmp_path / "real.org"
    target.write_text("secret", encoding="utf-8")
    link = tmp_path / "link.org"
    link.symlink_to(target)
    with pytest.raises(SafeReadError):
        safe_read_text(link)
    with pytest.raises(SafeReadError):
        safe_read_text(tmp_path)
    # SafeReadError 是 OSError 子类：调用方原有 except OSError 继续生效
    assert issubclass(SafeReadError, OSError)


def test_memscan_symlink_does_not_crash(tmp_path, monkeypatch):
    from agenote import memscan

    root = tmp_path / "ext"
    memdir = root / "agent" / "memory"
    memdir.mkdir(parents=True)
    (memdir / "a.md").write_text("# a\nbody", encoding="utf-8")
    (memdir / "b.md").symlink_to(memdir / "a.md")
    monkeypatch.setattr("agenote.memscan.resolve_xdg_path", lambda *a, **k: root)
    entries, errors = memscan._scan_dir_source(memscan.SOURCES["pi"])
    assert [e["name"] for e in entries] == ["a"]
    assert errors  # symlink 被拒绝并记入 note，而非抛异常


# ── S7：时效标记 ─────────────────────────────────────────────────────────────


def test_unverified_tag_and_entry_freshness():
    from agenote import memory as mem

    assert orgserde.unverified_tag(45, 30) == "(unverified 45d)"
    assert orgserde.unverified_tag(10, 30) == ""
    assert orgserde.unverified_tag(None, 30) == "(unverified ?d)"
    old = ["   :UPDATED:  [2020-01-01]", "   :END:"]
    assert mem.memory_entry_freshness(old, 30).startswith("(unverified ")
    today = date.today().strftime("[%Y-%m-%d]")
    assert mem.memory_entry_freshness([f"   :UPDATED:  {today}", "   :END:"], 30) == ""
    # 预留函数：默认输出与旧概览一致
    assert mem.format_memory_entry_line("F001", "标题") == "** F001 标题"
    assert "(unverified" in mem.format_memory_entry_line(
        "F001", "标题", old, freshness=True
    )


def test_search_freshness_tag_unit():
    from agenote.search import _freshness_tag

    assert _freshness_tag("正文", None, False) == ""
    old_body = "* DONE 卡片\n:PROPERTIES:\n:UPDATED: [2020-01-01]\n:END:\n"
    assert _freshness_tag(old_body, None, True).startswith("(unverified ")
    fresh_body = f"* DONE 卡片\n:PROPERTIES:\n:UPDATED: [{date.today()}]\n:END:\n"
    assert _freshness_tag(fresh_body, None, True) == ""
    # 无日期无文件 → 不可判定（?d）；文件 mtime 兜底不断言具体值，只断言类型
    assert isinstance(_freshness_tag("无日期正文", None, True), str)


def test_find_legacy_weight_files(tmp_path):
    from agenote.index import find_legacy_weight_files

    ctx = _fake_ctx(tmp_path / "kb")
    cat = ctx.experiences / "general"
    cat.mkdir(parents=True)
    (cat / "new.org").write_text("* DONE 新卡\n:PROPERTIES:\n:ID: 1\n:END:\n", encoding="utf-8")
    (cat / "old.org").write_text(
        "* DONE 旧卡\n:PROPERTIES:\n:ID: 2\n:WEIGHT: 1.5\n:END:\n", encoding="utf-8"
    )
    found = find_legacy_weight_files(ctx)
    assert len(found) == 1 and found[0].endswith("old.org")


# ── S10：list --json 只增字段 ────────────────────────────────────────────────


def test_list_json_additive_fields(tmp_path, capsys):
    from agenote.cards import cmd_list

    ctx = _fake_ctx(tmp_path / "kb")
    ctx.experiences.mkdir(parents=True)
    card = {
        "id": "20260924-120000",
        "file": "agenote/experiences/general/x.org",
        "title": "标题",
        "category": "general",
        "tech": "",
        "type": "workflow",
        "owner": "ai",
        "status": "done",
        "last_used": "",
        "last_verified": "2026-09-20",
        "created": "2026-09-24",
        "tags": [],
    }
    ctx.index.parent.mkdir(parents=True, exist_ok=True)
    ctx.index.write_text(
        json.dumps({"version": 1, "updated": "", "total": 1, "cards": [card]},
                   ensure_ascii=False),
        encoding="utf-8",
    )
    args = argparse.Namespace(
        recent=None, all=True, category=None, type=None, owner=None, unused_days=None
    )
    cmd_list(args, ctx)
    rows = json.loads(capsys.readouterr().out)
    assert len(rows) == 1
    for k in ("id", "title", "category", "type", "tech", "owner", "created",
              "status", "last_used", "usage_count", "source_agent", "file"):
        assert k in rows[0], k  # 存量键一个不少
    assert rows[0]["last_verified"] == "2026-09-20"  # 新增键
    # 未来字段缺失时降级为空串，不破坏形状
    assert rows[0]["usage_count"] == "" and rows[0]["source_agent"] == ""

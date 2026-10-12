# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""U2：[[路径]] 链接完整性只读检查（纯函数面 + review 展示节）。"""

from __future__ import annotations

import argparse

from agenote import linkcheck
from agenote.linkcheck import check_links, extract_links, resolve_link


# ── extract_links ────────────────────────────────────────────────────


def test_extract_basic():
    assert extract_links("见 [[foo]] 与 [[sub/bar]]") == ["foo", "sub/bar"]


def test_extract_excludes_org_and_external():
    text = "[[file:x.org]] [[file:x][描述]] [[https://a.b]] [[http://a.b]] [[id:123]]"
    assert extract_links(text) == []


def test_extract_alias_fragment_whitespace_dedup():
    assert extract_links("[[foo|别名]] [[bar#节]] [[ a ]] [[a]]") == ["foo", "bar", "a"]


def test_extract_empty_and_non_string():
    assert extract_links("") == []
    assert extract_links(None) == []


# ── resolve_link ─────────────────────────────────────────────────────


def test_resolve_omits_md_extension(tmp_path):
    assert resolve_link(tmp_path, "foo") == tmp_path / "foo.md"
    assert resolve_link(tmp_path, "sub/bar") == tmp_path / "sub" / "bar.md"
    assert resolve_link(tmp_path, "foo.md") == tmp_path / "foo.md"


def test_resolve_keeps_real_extensionless_file(tmp_path):
    (tmp_path / "plain").write_text("x", encoding="utf-8")
    assert resolve_link(tmp_path, "plain") == tmp_path / "plain"


# ── check_links ──────────────────────────────────────────────────────


def _memroot(tmp_path):
    root = tmp_path / "memories"
    root.mkdir()
    (root / "index.md").write_text("# 索引 [[a]]\n", encoding="utf-8")
    (root / "a.md").write_text("见 [[b]] 与 [[missing]]\n", encoding="utf-8")
    (root / "b.md").write_text("无引用\n", encoding="utf-8")
    (root / "orphan.md").write_text("无人指向我\n", encoding="utf-8")
    return root


def test_check_links_broken_and_orphaned(tmp_path):
    got = check_links(_memroot(tmp_path))
    assert got["broken"] == [
        {"source": "a.md", "link": "missing", "expected": "missing.md"}
    ]
    assert got["orphaned"] == ["orphan.md"]  # index 豁免；a 被 index 指，b 被 a 指


def test_check_links_exempt_override(tmp_path):
    root = _memroot(tmp_path)
    got = check_links(root, exempt=())
    assert "index.md" in got["orphaned"]  # 豁免撤掉后入口也算孤儿


def test_check_links_missing_root():
    assert check_links("/nonexistent-agenote-root") == {"broken": [], "orphaned": []}


# ── curator review 展示节 ────────────────────────────────────────────


def _review_ctx(tmp_path, monkeypatch, card_body):
    import agenote.core as core
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    root = tmp_path / "kb"
    monkeypatch.setattr(core, "KB_ROOT", root)
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    mem = root / "memories"
    mem.mkdir(parents=True)
    (mem / "good.md").write_text("好条目\n", encoding="utf-8")
    (exp / "20260101-000001-workflow-general.org").write_text(
        "* DONE workflow notes\n:PROPERTIES:\n:ID:       20260101-000001\n"
        ":CATEGORY: general\n:TECH:     pytest\n:STATUS:   done\n"
        ":CREATED:  [2026-09-01]\n:END:\n** 正文\n" + card_body,
        encoding="utf-8",
    )
    ctx = KBContext(
        name="test", root=root, experiences=exp, memories=mem,
        projects=mem / "projects", memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org", index=root / "index.json",
        inbox=root / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)
    return ctx


def test_review_shows_link_integrity(tmp_path, monkeypatch, capsys):
    import agenote.curator as curator

    ctx = _review_ctx(tmp_path, monkeypatch, "引用 [[good]] 与 [[bad]]\n")
    curator.cmd_review(argparse.Namespace(id="20260101-000001"), ctx)
    out = capsys.readouterr().out
    assert "链接完整性:" in out
    assert "✅ [[good]]" in out and "❌ 坏链 [[bad]]" in out


def test_review_without_links_single_line(tmp_path, monkeypatch, capsys):
    import agenote.curator as curator

    ctx = _review_ctx(tmp_path, monkeypatch, "无引用正文\n")
    curator.cmd_review(argparse.Namespace(id="20260101-000001"), ctx)
    assert "链接完整性: 无 [[路径]] 引用" in capsys.readouterr().out


def test_module_surface():
    assert callable(linkcheck.extract_links)
    assert callable(linkcheck.resolve_link)
    assert callable(linkcheck.check_links)

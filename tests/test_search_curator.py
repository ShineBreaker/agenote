# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""候选 2 拆分回归：cards/search/curator 三模块落位与核心算法。"""

from __future__ import annotations

from pathlib import Path

import agenote.cards as cards
import agenote.curator as curator
import agenote.inbox_archive as inbox_archive
import agenote.search as search
import pytest


def test_module_boundaries():
    """函数按 ADR-0002 落位：CRUD 在 cards，检索在 search，策展在 curator。"""
    for fn in ("cmd_add", "cmd_get", "cmd_list", "cmd_update", "cmd_touch",
               "cmd_merge", "cmd_connect", "cmd_inbox", "cmd_stats",
               "cmd_fields", "cmd_tags"):
        assert hasattr(cards, fn), fn
    for fn in ("cmd_search", "_cross_domain_search", "_query_terms",
               "_iter_search_targets", "_merge_ranges", "_range_score",
               "_line_contains_any", "_make_search_snippet"):
        assert hasattr(search, fn), fn
    for fn in ("cmd_archive", "cmd_restore", "cmd_deduplicate", "cmd_review",
               "_jaccard_similarity", "_archive_stale_candidates"):
        assert hasattr(curator, fn), fn
    # 迁出的不再留在 cards；一键策展编排已删（流程由 agent 依据 skill 主导）
    assert not hasattr(cards, "cmd_search")
    assert not hasattr(cards, "cmd_curate")
    assert not hasattr(cards, "_jaccard_similarity")
    assert not hasattr(curator, "cmd_curate")
    assert not hasattr(curator, "_mark_auto_stale")
    # core 不再持有搜索辅助
    import agenote.core as core
    for fn in ("_query_terms", "_iter_search_targets", "_merge_ranges",
               "_range_score", "_line_contains_any"):
        assert not hasattr(core, fn), fn


def test_health_reuses_curator_jaccard():
    """health 与 curator 共享同一 Jaccard 实现（消除跨职责 seam）。"""
    import agenote.health as health

    assert health._jaccard_similarity is curator._jaccard_similarity


def test_query_terms_split_and_dedup():
    assert search._query_terms("a/b c，d d") == ["a", "b", "c", "d"]
    assert search._query_terms("  ") == []
    assert search._query_terms("唯一短语") == ["唯一短语"]


def test_merge_ranges_and_line_contains():
    assert search._merge_ranges([(1, 3), (3, 5), (8, 9)]) == [(1, 5), (8, 9)]
    assert search._merge_ranges([]) == []
    assert search._line_contains_any("Hello World", ["world"], False)
    assert not search._line_contains_any("Hello World", ["world"], True)


def test_range_score():
    lines = ["alpha beta", "gamma", "alpha"]
    assert search._range_score(lines, 0, 2, ["alpha"], False) == 1 * 100 + 2
    assert search._range_score(lines, 1, 1, ["alpha"], False) == 0


def test_jaccard_similarity():
    assert curator._jaccard_similarity("fix bug", "Fix BUG") == 1.0
    assert curator._jaccard_similarity("a b", "c d") == 0.0
    assert curator._jaccard_similarity("", "x") == 0.0
    # 交集 1 / 并集 3
    assert abs(curator._jaccard_similarity("a b", "b c") - 1 / 3) < 1e-9


def test_archive_preflights_all_ids_before_writing(tmp_path, monkeypatch, capsys):
    """批量 archive 任一 ID 无效时，不得先归档前面的卡片。"""
    import agenote.core as core
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    root = tmp_path / "kb"
    monkeypatch.setattr(core, "KB_ROOT", root)
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    cards = []
    for card_id, title in (("20260101-000001", "A"), ("20260101-000002", "B")):
        card = exp / f"{card_id}-workflow-general.org"
        card.write_text(
            f"* DONE {title}\n:PROPERTIES:\n:ID:       {card_id}\n"
            ":STATUS:   done\n:END:\n:general:workflow:ai::\n",
            encoding="utf-8",
        )
        cards.append(card)
    ctx = KBContext(
        name="test", root=root, experiences=exp, memories=root / "memories",
        projects=root / "memories" / "projects", memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org", index=root / "index.json",
        inbox=root / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)
    before = {card: card.read_bytes() for card in cards}
    import argparse

    with pytest.raises(SystemExit) as exc:
        curator.cmd_archive(
            argparse.Namespace(
                id=[cards[0].stem, "missing"], list_cards=False, stale=False,
                reason=None, json=False,
            ),
            ctx,
        )

    assert exc.value.code == 1
    assert "未找到卡片: missing" in capsys.readouterr().err
    assert {card: card.read_bytes() for card in cards} == before


def test_archive_write_failure_restores_batch_and_index(
    tmp_path, monkeypatch, capsys
):
    """归档发布中途失败时，整批卡片和索引必须恢复。"""
    import argparse
    import agenote.core as core
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    root = tmp_path / "kb"
    monkeypatch.setattr(core, "KB_ROOT", root)
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    cards = []
    for card_id in ("20260101-000001", "20260101-000002"):
        card = exp / f"{card_id}-workflow-general.org"
        card.write_text(
            f"* DONE {card_id}\n:PROPERTIES:\n:ID:       {card_id}\n"
            ":STATUS:   done\n:END:\n:general:workflow:ai::\n",
            encoding="utf-8",
        )
        cards.append(card)
    ctx = KBContext(
        name="test", root=root, experiences=exp, memories=root / "memories",
        projects=root / "memories" / "projects", memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org", index=root / "index.json",
        inbox=root / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)
    before_cards = {card: card.read_bytes() for card in cards}
    before_index = ctx.index.read_bytes()
    real_atomic_write = curator.atomic_write

    def fail_second(path, text):
        if Path(path) == cards[1]:
            raise OSError("injected second write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr(curator, "atomic_write", fail_second)
    with pytest.raises(OSError, match="injected"):
        curator.cmd_archive(
            argparse.Namespace(
                id=[card.stem for card in cards], list_cards=False, stale=False,
                reason=None, json=False,
            ),
            ctx,
        )

    assert {card: card.read_bytes() for card in cards} == before_cards
    assert ctx.index.read_bytes() == before_index
    assert "已归档" not in capsys.readouterr().out


def test_merge_rollback_bypasses_failed_write_wrapper(tmp_path, monkeypatch):
    """原始写失败后，恢复不能再次经过同一故障包装器。"""
    import argparse
    import agenote.cards as cards
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    root = tmp_path / "kb"
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    experiences = root / "experiences"
    exp = experiences / "general"
    exp.mkdir(parents=True)
    primary = exp / "primary.org"
    secondary = exp / "secondary.org"
    primary.write_text(
        "* DONE 主\n:PROPERTIES:\n:ID: primary\n:CATEGORY: general\n"
        ":TYPE: debug\n:OWNER: ai\n:STATUS: done\n:END:\n",
        encoding="utf-8",
    )
    secondary.write_text(
        "* DONE 次\n:PROPERTIES:\n:ID: secondary\n:CATEGORY: general\n"
        ":TYPE: debug\n:OWNER: ai\n:STATUS: done\n:END:\n正文\n",
        encoding="utf-8",
    )
    ctx = KBContext(
        name="test", root=root, experiences=experiences,
        memories=root / "memories", projects=root / "memories" / "projects",
        memory_org=root / "MEMORY.org", memory_archive=root / "MEMORY-ARCHIVE.org",
        index=root / "index.json", inbox=root / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)
    before = {path: path.read_bytes() for path in (primary, secondary, ctx.index)}
    real_atomic_write = cards.atomic_write

    def fail_primary(path, text):
        if Path(path) == primary:
            raise OSError("injected primary write failure")
        return real_atomic_write(path, text)

    monkeypatch.setattr(cards, "atomic_write", fail_primary)
    with pytest.raises(OSError, match="injected primary write failure"):
        cards.cmd_merge(
            argparse.Namespace(primary=str(primary), secondary=[str(secondary)]),
            ctx,
        )

    assert {path: path.read_bytes() for path in before} == before


def test_merge_reports_when_rollback_itself_fails(tmp_path, monkeypatch):
    """补偿写也失败时必须明确报告恢复未完成，不能掩盖原异常。"""
    import argparse
    import agenote.cards as cards
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    root = tmp_path / "kb"
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    experiences = root / "experiences"
    exp = experiences / "general"
    exp.mkdir(parents=True)
    primary = exp / "primary.org"
    secondary = exp / "secondary.org"
    primary.write_text(
        "* DONE 主\n:PROPERTIES:\n:ID: primary\n:CATEGORY: general\n"
        ":TYPE: debug\n:OWNER: ai\n:STATUS: done\n:END:\n",
        encoding="utf-8",
    )
    secondary.write_text(
        "* DONE 次\n:PROPERTIES:\n:ID: secondary\n:CATEGORY: general\n"
        ":TYPE: debug\n:OWNER: ai\n:STATUS: done\n:END:\n正文\n",
        encoding="utf-8",
    )
    ctx = KBContext(
        name="test", root=root, experiences=experiences,
        memories=root / "memories", projects=root / "memories" / "projects",
        memory_org=root / "MEMORY.org", memory_archive=root / "MEMORY-ARCHIVE.org",
        index=root / "index.json", inbox=root / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)
    real_atomic_write = cards.atomic_write
    writes = 0
    rollback_failed = False

    def fail_after_primary_publish(path, text):
        nonlocal writes, rollback_failed
        if Path(path) == primary:
            writes += 1
            real_atomic_write(path, text)
            rollback_failed = True
            raise OSError("injected after primary publish")
        return real_atomic_write(path, text)

    import agenote.safeio as safeio

    def fail_primary_rollback(path, data):
        if rollback_failed and Path(path) == primary:
            raise OSError("injected rollback failure")
        return safeio_atomic_write_bytes(path, data)

    safeio_atomic_write_bytes = safeio.atomic_write_bytes
    monkeypatch.setattr(cards, "atomic_write", fail_after_primary_publish)
    monkeypatch.setattr(safeio, "atomic_write_bytes", fail_primary_rollback)
    with pytest.raises(RuntimeError, match="merge 回滚失败"):
        cards.cmd_merge(
            argparse.Namespace(primary=str(primary), secondary=[str(secondary)]),
            ctx,
        )


def test_restore_write_failure_restores_card_and_index(
    tmp_path, monkeypatch, capsys
):
    """索引发布失败时，卡片必须恢复为归档前字节。"""
    import argparse
    import agenote.core as core
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    root = tmp_path / "kb"
    monkeypatch.setattr(core, "KB_ROOT", root)
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    card = exp / "20260101-000001-workflow-general.org"
    card.write_text(
        "* DONE 卡片\n:PROPERTIES:\n:ID:       20260101-000001\n"
        ":STATUS:   archived\n:ARCHIVED_AT: [2026-01-01 三 10:00]\n"
        ":ARCHIVE_REASON: old\n:END:\n:general:workflow:ai::\n",
        encoding="utf-8",
    )
    ctx = KBContext(
        name="test", root=root, experiences=exp, memories=root / "memories",
        projects=root / "memories" / "projects", memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org", index=root / "index.json",
        inbox=root / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)
    before_card = card.read_bytes()
    before_index = ctx.index.read_bytes()

    def fail_index(*args, **kwargs):
        raise OSError("injected index failure")

    monkeypatch.setattr(curator, "_save_index", fail_index)
    with pytest.raises(OSError, match="injected index failure"):
        curator.cmd_restore(
            argparse.Namespace(id=card.stem, status="stable"), ctx
        )

    assert card.read_bytes() == before_card
    assert ctx.index.read_bytes() == before_index
    assert "已恢复" not in capsys.readouterr().out


def test_inbox_archive_publish_failure_restores_batch_and_index(
    tmp_path, monkeypatch, capsys
):
    """第二张卡片写失败时，不得留下第一张、索引增量或成功输出。"""
    import argparse
    import agenote.core as core
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    root = tmp_path / "kb"
    monkeypatch.setattr(core, "KB_ROOT", root)
    experiences = root / "experiences"
    exp = experiences / "general"
    exp.mkdir(parents=True)
    ctx = KBContext(
        name="test", root=root, experiences=experiences,
        memories=root / "memories",
        projects=root / "memories" / "projects", memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org", index=root / "index.json",
        inbox=root / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)
    before_index = ctx.index.read_bytes()
    real_atomic_write = inbox_archive.atomic_write
    targets: list[Path] = []

    def fail_second(path, text):
        path = Path(path)
        targets.append(path)
        if len(targets) == 2:
            raise OSError("injected second inbox write")
        return real_atomic_write(path, text)

    monkeypatch.setattr(inbox_archive, "atomic_write", fail_second)
    monkeypatch.setattr(
        "sys.stdin", __import__("io").StringIO(
            '[{"heading":"A","body":"a"},{"heading":"B","body":"b"}]'
        )
    )
    with pytest.raises(OSError, match="injected second inbox write"):
        inbox_archive.cmd_inbox_archive(
            argparse.Namespace(
                category="general", reason=None, no_reindex=True, prune=False,
                stdin=True,
            ),
            ctx,
        )

    assert not any(exp.glob("*.org"))
    assert ctx.index.read_bytes() == before_index
    assert capsys.readouterr().out == ""


def test_inbox_archive_failure_keeps_absent_index_and_inbox_absent(
    tmp_path, monkeypatch, capsys
):
    """原始 index/inbox 不存在时，失败回滚不得留下 ensure_dirs 生成的骨架文件。"""
    import argparse
    import io

    import agenote.core as core
    from agenote.core import KBContext

    root = tmp_path / "kb"
    monkeypatch.setattr(core, "KB_ROOT", root)
    experiences = root / "experiences"
    (experiences / "general").mkdir(parents=True)
    ctx = KBContext(
        name="test", root=root, experiences=experiences,
        memories=root / "memories",
        projects=root / "memories" / "projects", memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org", index=root / "index.json",
        inbox=root / "inbox.org",
    )
    assert not ctx.index.exists() and not ctx.inbox.exists()

    def fail_prune(*_args, **_kwargs):
        raise OSError("injected prune failure")

    monkeypatch.setattr(inbox_archive, "_prune_inbox", fail_prune)
    monkeypatch.setattr(
        "sys.stdin", io.StringIO('[{"heading":"A","body":"a"}]')
    )
    with pytest.raises(OSError, match="injected prune failure"):
        inbox_archive.cmd_inbox_archive(
            argparse.Namespace(
                category="general", reason=None, no_reindex=False, prune=True,
                stdin=True,
            ),
            ctx,
        )

    assert not any(experiences.rglob("*.org"))
    assert not ctx.index.exists()
    assert not ctx.inbox.exists()
    assert capsys.readouterr().out == ""


def test_archive_stale_candidates_readonly(tmp_path, monkeypatch, capsys):
    """archive --stale 只列归档候选，不改卡片文件（去留由 agent 审查）。"""
    import agenote.core as core
    from agenote.core import KBContext
    from agenote.index import _rebuild_index, _save_index

    monkeypatch.setattr(core, "KB_ROOT", tmp_path)
    exp = tmp_path / "experiences" / "general"
    exp.mkdir(parents=True)
    card = exp / "20260101-000000-workflow-general.org"
    card.write_text(
        "* DONE 一张陈旧卡\n"
        ":PROPERTIES:\n"
        ":ID:       20260101-000000\n"
        ":STATUS:   stale\n"
        ":LAST_VERIFIED: [2025-01-01 三 10:00]\n"
        ":END:\n"
        ":general:workflow:ai::\n",
        encoding="utf-8",
    )
    ctx = KBContext(
        name="test", root=tmp_path, experiences=tmp_path / "experiences",
        memories=tmp_path / "memories", projects=tmp_path / "memories" / "projects",
        memory_org=tmp_path / "MEMORY.org", memory_archive=tmp_path / "MEMORY-ARCHIVE.org",
        index=tmp_path / "index.json", inbox=tmp_path / "inbox.org",
    )
    _save_index(_rebuild_index(ctx), ctx)

    before = card.read_text(encoding="utf-8")
    curator._archive_stale_candidates(ctx)
    out = capsys.readouterr().out
    assert "20260101-000000" in out
    assert "归档候选" in out
    assert card.read_text(encoding="utf-8") == before  # 只读：文件未被改写


def test_make_search_snippet():
    content = "l1\nl2\nhit here\nl4\nl5"
    snippet = search._make_search_snippet({"content": content}, ["hit"], False)
    assert snippet.splitlines() == ["l1", "l2", "hit here", "l4", "l5"]
    no_hit = search._make_search_snippet({"content": content}, ["zzz"], False)
    assert no_hit == ""

# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import os

import pytest

from agenote.core import KBContext, _resolve_card
from agenote.orgserde import (
    OrgPropertyDrawerError,
    delete_org_prop,
    parse_org_prop,
    set_org_prop,
)


def _ctx(root) -> KBContext:
    return KBContext(
        name="agenote",
        root=root,
        experiences=root / "experiences",
        memories=root / "memories",
        projects=root / "memories" / "projects",
        memory_org=root / "MEMORY.org",
        memory_archive=root / "MEMORY-ARCHIVE.org",
        index=root / "index.json",
        inbox=root / "inbox.org",
        is_human=False,
        default_weight=1.0,
        agent_name="zcode",
    )


def _card(path, card_id: str, title: str = "卡片") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""* DONE {title}
:PROPERTIES:
:ID: {card_id}
:END:
""",
        encoding="utf-8",
    )


def test_parse_org_prop_only_reads_properties_drawer():
    content = """* DONE 示例
:PROPERTIES:
:ID: real-id
:END:

正文里的 :ID: fake-id 不能成为属性。
"""
    assert parse_org_prop(content, "ID") == "real-id"


def test_parse_org_prop_ignores_body_drawer_without_real_id():
    content = """* DONE 正文伪属性
正文示例：
:PROPERTIES:
:ID: body-fake-id
:END:
"""
    assert parse_org_prop(content, "ID") == ""


def test_planning_line_before_properties_is_metadata():
    content = """* TODO 计划
SCHEDULED: <2026-09-25 Fri>
:PROPERTIES:
:id: planned-id
:END:

正文 :ID: body-fake-id
"""
    assert parse_org_prop(content, "ID") == "planned-id"
    updated = set_org_prop(content, "STATUS", "stable")
    assert ":STATUS: stable" in updated
    assert "SCHEDULED: <2026-09-25 Fri>" in updated
    assert "正文 :ID: body-fake-id" in updated
    assert ":id: planned-id" not in delete_org_prop(updated, "id")


def test_property_mutators_reject_body_only_drawer():
    content = """* DONE 正文伪属性

正文示例：
:PROPERTIES:
:USAGE_COUNT: 999
:END:
"""
    with pytest.raises(OrgPropertyDrawerError, match="顶层 PROPERTIES"):
        delete_org_prop(content, "USAGE_COUNT")


def test_short_id_survives_renamed_card_and_ignores_body_example(
    tmp_path, monkeypatch
):
    root = tmp_path / "kb"
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    card_id = "20260924-111827"
    original = exp / "legacy-renamed.org"
    original.write_text(
        f"""* DONE 原卡
:PROPERTIES:
:ID: {card_id}
:END:

:ID: {card_id}
""",
        encoding="utf-8",
    )
    derived = exp / f"{card_id}-3-debug-general.org"
    derived.write_text(
        f"""* DONE 派生卡
:PROPERTIES:
:ID: {card_id}-3
:END:

示例 :ID: {card_id}
""",
        encoding="utf-8",
    )
    ctx = _ctx(root)
    monkeypatch.setattr(
        "agenote.core.Path.rglob", lambda _self, _pattern: [derived, original]
    )

    assert _resolve_card(card_id, ctx) == original


def test_short_id_rejects_duplicate_exact_ids(tmp_path):
    root = tmp_path / "kb"
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    first = exp / "a.org"
    second = exp / "b.org"
    content = """* DONE 重复
:PROPERTIES:
:ID: 20260924-111827
:END:
"""
    first.write_text(content, encoding="utf-8")
    second.write_text(content, encoding="utf-8")
    ctx = _ctx(root)

    assert _resolve_card("20260924-111827", ctx) is None


def test_short_id_skips_unreadable_candidate(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    card_id = "20260924-111827"
    bad = exp / f"{card_id}-3-debug-general.org"
    bad.write_bytes(b"\xff\xfe")
    original = exp / "legacy-renamed.org"
    original.write_text(
        f"""* DONE 原卡
:PROPERTIES:
:ID: {card_id}
:END:
""",
        encoding="utf-8",
    )
    ctx = _ctx(root)
    monkeypatch.setattr(
        "agenote.core.Path.rglob", lambda _self, _pattern: [bad, original]
    )

    assert _resolve_card(card_id, ctx) == original


def test_empty_selector_is_rejected(tmp_path):
    for selector in ("", "   ", ".", ".."):
        root = tmp_path / "kb" / selector.replace(" ", "space")
        exp = root / "experiences" / "audit"
        _card(exp / "only.org", "only-id")
        assert _resolve_card(selector, _ctx(root)) is None


def test_direct_invalid_utf8_file_is_rejected(tmp_path):
    root = tmp_path / "kb"
    exp = root / "experiences" / "audit"
    exp.mkdir(parents=True)
    bad = exp / "bad.org"
    bad.write_bytes(b"\xff\xfe")
    ctx = _ctx(root)

    assert _resolve_card(str(bad), ctx) is None


def test_resolved_path_cannot_escape_experiences(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    outside = tmp_path / "outside.org"
    outside.write_text("secret", encoding="utf-8")
    ctx = _ctx(root)
    monkeypatch.chdir(exp)

    assert _resolve_card(os.path.relpath(outside, exp), ctx) is None


def test_experiences_root_symlink_is_rejected(tmp_path):
    root = tmp_path / "kb"
    real = tmp_path / "real-experiences"
    real.mkdir()
    root.mkdir()
    (root / "experiences").symlink_to(real, target_is_directory=True)
    card = real / "outside.org"
    _card(card, "cross-id")
    ctx = _ctx(root)

    assert _resolve_card("cross-id", ctx) is None


def test_merge_rejects_corrupt_index_before_writing_cards(tmp_path):
    """批量 merge 是 mutating 命令；损坏索引必须在任何卡片写入前报错。"""
    import json
    import os
    import subprocess
    import sys

    root = tmp_path / "kb"
    exp = root / "agenote" / "experiences" / "general"
    exp.mkdir(parents=True)
    primary = exp / "primary.org"
    secondary = exp / "secondary.org"
    primary.write_text("* DONE 主\n:PROPERTIES:\n:ID: primary\n:END:\n", encoding="utf-8")
    secondary.write_text("* DONE 次\n:PROPERTIES:\n:ID: secondary\n:END:\n正文\n", encoding="utf-8")
    index = root / "agenote" / "index.json"
    index.write_text(json.dumps({"cards": [42]}, ensure_ascii=False), encoding="utf-8")
    before = {path: path.read_bytes() for path in (primary, secondary, index)}
    env = os.environ.copy()
    env.update(KB_ROOT=str(root), PYTHONDONTWRITEBYTECODE="1")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenote.cli",
            "merge",
            str(primary),
            str(secondary),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "索引结构非法" in result.stderr
    assert "Traceback" not in result.stderr
    assert all(path.read_bytes() == original for path, original in before.items())


def test_merge_rolls_back_secondary_writes_on_later_failure(tmp_path, monkeypatch):
    """批量 merge 后续写失败时，已写 secondary 不得留下半完成状态。"""
    import argparse
    from pathlib import Path

    import agenote.cards as cards

    root = tmp_path / "kb"
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    primary = exp / "primary.org"
    first = exp / "first.org"
    second = exp / "second.org"
    primary.write_text("* DONE 主\n:PROPERTIES:\n:ID: primary\n:END:\n", encoding="utf-8")
    first.write_text("* DONE 一\n:PROPERTIES:\n:ID: first\n:END:\n正文一\n", encoding="utf-8")
    second.write_text("* DONE 二\n:PROPERTIES:\n:ID: second\n:END:\n正文二\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (primary, first, second)}
    ctx = _ctx(root)
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    real_atomic_write = cards.atomic_write
    failed = False

    def flaky_write(path, text):
        nonlocal failed
        if Path(path) == second and not failed:
            failed = True
            raise OSError("模拟第二个 secondary 写入失败")
        return real_atomic_write(path, text)

    monkeypatch.setattr(cards, "atomic_write", flaky_write)
    args = argparse.Namespace(primary=str(primary), secondary=[str(first), str(second)])
    with pytest.raises(OSError, match="第二个 secondary"):
        cards.cmd_merge(args, ctx)

    assert all(path.read_bytes() == before[path] for path in before)


def test_merge_updates_secondary_index_entry(tmp_path, monkeypatch, capsys):
    """成功 merge 后索引中的 secondary 状态必须与卡片文件一致。"""
    import argparse
    import json

    import agenote.cards as cards
    from agenote.core import KBContext

    root = tmp_path / "kb"
    exp = root / "agenote" / "experiences" / "general"
    exp.mkdir(parents=True)
    primary = exp / "primary.org"
    secondary = exp / "secondary.org"
    primary.write_text(
        "* DONE 主\n:PROPERTIES:\n:ID: primary\n:CATEGORY: general\n:TYPE: debug\n:OWNER: ai\n:STATUS: done\n:END:\n",
        encoding="utf-8",
    )
    secondary.write_text(
        "* DONE 次\n:PROPERTIES:\n:ID: secondary\n:CATEGORY: general\n:TYPE: debug\n:OWNER: ai\n:STATUS: done\n:END:\n正文\n",
        encoding="utf-8",
    )
    index = root / "agenote" / "index.json"
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "updated": "old",
                "total": 2,
                "cards": [
                    {
                        "id": "primary",
                        "file": "agenote/experiences/general/primary.org",
                        "title": "主",
                        "category": "general",
                        "type": "debug",
                        "owner": "ai",
                        "status": "done",
                    },
                    {
                        "id": "secondary",
                        "file": "agenote/experiences/general/secondary.org",
                        "title": "次",
                        "category": "general",
                        "type": "debug",
                        "owner": "ai",
                        "status": "done",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ctx = KBContext(
        name="agenote",
        root=root,
        experiences=exp.parent,
        memories=root / "agenote" / "memories",
        projects=root / "agenote" / "memories" / "projects",
        memory_org=root / "agenote" / "MEMORY.org",
        memory_archive=root / "agenote" / "MEMORY-ARCHIVE.org",
        index=index,
        inbox=root / "agenote" / "inbox.org",
        is_human=False,
        default_weight=1.0,
        agent_name="zcode",
    )
    monkeypatch.setattr("agenote.core.KB_ROOT", root)

    cards.cmd_merge(argparse.Namespace(primary=str(primary), secondary=[str(secondary)]), ctx)

    saved = json.loads(index.read_text(encoding="utf-8"))
    assert {card["id"]: card["status"] for card in saved["cards"]} == {
        "primary": "done",
        "secondary": "archived",
    }
    assert "已合并 1 张卡片到: primary.org" in capsys.readouterr().out


def test_merge_failure_restores_index_and_emits_no_success_output(tmp_path, monkeypatch, capsys):
    """索引发布后抛错时，merge 不得留下已归档卡片或误导性成功输出。"""
    import argparse
    import json

    import agenote.cards as cards
    from agenote.core import KBContext

    root = tmp_path / "kb"
    exp = root / "agenote" / "experiences" / "general"
    exp.mkdir(parents=True)
    primary = exp / "primary.org"
    secondary = exp / "secondary.org"
    primary.write_text(
        "* DONE 主\n:PROPERTIES:\n:ID: primary\n:CATEGORY: general\n:TYPE: debug\n:OWNER: ai\n:STATUS: done\n:END:\n",
        encoding="utf-8",
    )
    secondary.write_text(
        "* DONE 次\n:PROPERTIES:\n:ID: secondary\n:CATEGORY: general\n:TYPE: debug\n:OWNER: ai\n:STATUS: done\n:END:\n正文\n",
        encoding="utf-8",
    )
    index = root / "agenote" / "index.json"
    index.write_text(
        json.dumps(
            {
                "version": 1,
                "updated": "old",
                "total": 2,
                "cards": [
                    {
                        "id": "primary",
                        "file": "agenote/experiences/general/primary.org",
                        "title": "主",
                        "category": "general",
                        "type": "debug",
                        "owner": "ai",
                        "status": "done",
                    },
                    {
                        "id": "secondary",
                        "file": "agenote/experiences/general/secondary.org",
                        "title": "次",
                        "category": "general",
                        "type": "debug",
                        "owner": "ai",
                        "status": "done",
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    original = {
        path: path.read_bytes() for path in (primary, secondary, index)
    }
    ctx = KBContext(
        name="agenote",
        root=root,
        experiences=exp.parent,
        memories=root / "agenote" / "memories",
        projects=root / "agenote" / "memories" / "projects",
        memory_org=root / "agenote" / "MEMORY.org",
        memory_archive=root / "agenote" / "MEMORY-ARCHIVE.org",
        index=index,
        inbox=root / "agenote" / "inbox.org",
        is_human=False,
        default_weight=1.0,
        agent_name="zcode",
    )
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    real_save = cards._save_index

    def save_then_fail(index_value, ctx_value=None):
        real_save(index_value, ctx_value)
        raise OSError("模拟索引提交后失败")

    monkeypatch.setattr(cards, "_save_index", save_then_fail)
    with pytest.raises(OSError, match="索引提交后失败"):
        cards.cmd_merge(
            argparse.Namespace(primary=str(primary), secondary=[str(secondary)]), ctx
        )

    assert all(path.read_bytes() == original[path] for path in original)
    assert capsys.readouterr().out == ""


def test_symlink_path_is_rejected(tmp_path):
    root = tmp_path / "kb"
    exp = root / "experiences" / "general"
    exp.mkdir(parents=True)
    outside = tmp_path / "outside.org"
    outside.write_text("secret", encoding="utf-8")
    link = exp / "link.org"
    link.symlink_to(outside)
    ctx = _ctx(root)

    assert _resolve_card(str(link), ctx) is None

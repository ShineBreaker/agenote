# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""update --tech/--category/--owner 同步 fingerprint 标签行与索引的回归测试。

背景：cmd_add 写卡片时会生成 :cat:type:owner:tech:entry:: 标签行，索引的 tags
字段即由该行解析。旧版 cmd_update 只在 --type 时同步标签行，--tech/--category
改完属性后标签/索引仍停留在旧值，导致 `agenote tags` 与 fields 漂移。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from agenote.cards import cmd_add, cmd_connect, cmd_get, cmd_touch, cmd_update
import agenote.cards as cards
import agenote.core as core
from agenote.core import KBContext
from agenote.orgserde import parse_org_prop


@pytest.fixture
def kb_root(tmp_path, monkeypatch):
    root = tmp_path / "kb"
    root.mkdir()
    monkeypatch.setattr("agenote.core.KB_ROOT", root)
    return root


def _ctx(root) -> KBContext:
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


def _add_args(title, category="general", tech="", type_="debug", entry=""):
    return argparse.Namespace(
        title=title,
        category=category,
        tech=tech,
        type=type_,
        owner="ai",
        entry=entry,
        summary="",
        stdin=False,
        force=False,
    )


def _update_args(target, **overrides):
    base = dict(
        target=target,
        status=None,
        category=None,
        tech=None,
        type_=None,
        owner=None,
        append_to=None,
        append_text=None,
        stdin=False,
        force=False,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _only_card(ctx):
    files = list(ctx.experiences.rglob("*.org"))
    assert len(files) == 1
    return files[0]


def _add_card(ctx, title):
    cmd_add(_add_args(title), ctx)
    return _only_card(ctx)


def _index_entry(ctx, card_id):
    idx = json.loads(ctx.index.read_text(encoding="utf-8"))
    return next(c for c in idx["cards"] if c["id"] == card_id)


def test_update_tech_syncs_fingerprint_and_index(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    card_id = re.search(r":ID:\s+(\S+)", card.read_text(encoding="utf-8")).group(1)

    cmd_update(_update_args(str(card), tech="rust"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":TECH:     rust" in content
    assert ":general:debug:ai:rust::" in content
    entry = _index_entry(ctx, card_id)
    assert entry["tech"] == "rust"
    assert "rust" in entry["tags"]


def test_update_index_failure_restores_card_and_index(kb_root, monkeypatch, capsys):
    """索引发布失败时，update 的卡片内容与索引必须恢复原字节。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="guile"), ctx)
    card = _only_card(ctx)
    before_card = card.read_bytes()
    before_index = ctx.index.read_bytes()
    capsys.readouterr()

    def fail_index(_index, _ctx):
        raise OSError("SIMULATED_INDEX_SAVE_FAILURE")

    monkeypatch.setattr(cards, "_save_index", fail_index)
    with pytest.raises(OSError, match="SIMULATED_INDEX_SAVE_FAILURE"):
        cmd_update(_update_args(str(card), tech="rust"), ctx)

    assert card.read_bytes() == before_card
    assert ctx.index.read_bytes() == before_index
    assert capsys.readouterr().out == ""


def test_update_type_index_failure_restores_renamed_card(kb_root, monkeypatch, capsys):
    """--type 改名的索引失败时，旧路径和卡片内容都必须恢复。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", type_="debug"), ctx)
    old_card = _only_card(ctx)
    before_card = old_card.read_bytes()
    before_index = ctx.index.read_bytes()
    capsys.readouterr()

    def fail_index(_index, _ctx):
        raise OSError("SIMULATED_INDEX_SAVE_FAILURE")

    monkeypatch.setattr(cards, "_save_index", fail_index)
    with pytest.raises(OSError, match="SIMULATED_INDEX_SAVE_FAILURE"):
        cmd_update(_update_args(str(old_card), type_="workflow"), ctx)

    assert old_card.exists()
    assert old_card.read_bytes() == before_card
    assert not [p for p in ctx.experiences.rglob("*.org") if p != old_card]
    assert ctx.index.read_bytes() == before_index
    assert capsys.readouterr().out == ""


def test_bom_prefixed_card_can_be_updated_and_keeps_bom(kb_root):
    """首行 UTF-8 BOM 不应让卡片变成不可策展；写回保留 BOM 原字节。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    card.write_bytes(b"\xef\xbb\xbf" + card.read_bytes())
    assert card.read_bytes().startswith(b"\xef\xbb\xbf")

    cmd_update(_update_args(str(card), tech="rust"), ctx)

    after = card.read_bytes()
    assert after.startswith(b"\xef\xbb\xbf")
    assert b":TECH:     rust" in after
    assert parse_org_prop(card.read_text(encoding="utf-8"), "TECH") == "rust"


def test_bom_card_title_reaches_index_after_touch(kb_root):
    """BOM 卡片 add 前置 BOM 字节 → touch 后 index.json 的 title 必须是真实标题。"""
    from agenote.cards import cmd_touch

    ctx = _ctx(kb_root)
    cmd_add(_add_args("BOM 卡标题"), ctx)
    card = _only_card(ctx)
    card_id = re.search(r":ID:\s+(\S+)", card.read_text(encoding="utf-8")).group(1)
    card.write_bytes(b"\xef\xbb\xbf" + card.read_bytes())

    cmd_touch(argparse.Namespace(target=str(card), used_only=False), ctx)

    entry = _index_entry(ctx, card_id)
    assert entry["title"] == "BOM 卡标题"


def test_update_rename_keeps_new_copy_when_card_restore_fails(kb_root, monkeypatch):
    """card 恢复写失败（如磁盘满）时不得删除改名后的新文件：至少一个副本存活。"""
    import agenote.safeio as safeio

    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", type_="debug"), ctx)
    old_card = _only_card(ctx)

    def fail_index(_index, _ctx):
        raise OSError("SIMULATED_INDEX_SAVE_FAILURE")

    real_atomic_write_bytes = safeio.atomic_write_bytes
    card_writes = {"n": 0}

    def flaky_write_bytes(path, data):
        path = Path(path)
        if path == old_card:
            # 第 1 次是 try 块的正常写入，第 2 次起是回滚恢复写，令其失败
            card_writes["n"] += 1
            if card_writes["n"] >= 2:
                raise OSError("SIMULATED_RESTORE_FAILURE")
        return real_atomic_write_bytes(path, data)

    monkeypatch.setattr(cards, "_save_index", fail_index)
    monkeypatch.setattr(safeio, "atomic_write_bytes", flaky_write_bytes)

    with pytest.raises(RuntimeError, match="update 回滚失败"):
        cmd_update(_update_args(str(old_card), type_="workflow"), ctx)

    survivors = list(ctx.experiences.rglob("*.org"))
    assert len(survivors) == 1, "至少一个副本必须存活"
    assert not old_card.exists()
    assert survivors[0].read_bytes().startswith("* DONE 原始".encode("utf-8"))
    assert b":TYPE:     workflow" in survivors[0].read_bytes()


def test_rollback_preserves_original_bytes(kb_root, monkeypatch):
    """回滚快照用原始字节，不把 CRLF 归一化。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    card.write_bytes(
        b"* DONE raw\r\n"
        b":PROPERTIES:\r\n"
        b":ID: 20260101-000001\r\n"
        b":TYPE: debug\r\n"
        b":CATEGORY: general\r\n"
        b":OWNER: ai\r\n"
        b":STATUS: done\r\n"
        b":END:\r\n"
        b":general:debug:ai::\r\n"
    )
    before = card.read_bytes()
    before_index = ctx.index.read_bytes()

    def fail_index(*args, **kwargs):
        raise OSError("injected index failure")

    monkeypatch.setattr(cards, "_save_index", fail_index)
    with pytest.raises(OSError, match="injected index failure"):
        cmd_update(_update_args(str(card), tech="rust"), ctx)

    assert card.read_bytes() == before
    assert ctx.index.read_bytes() == before_index


def test_add_index_failure_removes_new_card_and_restores_index(kb_root, monkeypatch, capsys):
    """add 的索引发布失败时，不得留下孤立卡片或增量索引。"""
    ctx = _ctx(kb_root)
    before_index = ctx.index.read_bytes() if ctx.index.exists() else None
    capsys.readouterr()

    def fail_index(_index, _ctx):
        raise OSError("SIMULATED_INDEX_SAVE_FAILURE")

    monkeypatch.setattr(cards, "_save_index", fail_index)
    with pytest.raises(OSError, match="SIMULATED_INDEX_SAVE_FAILURE"):
        cmd_add(_add_args("半提交"), ctx)

    assert not list(ctx.experiences.rglob("*.org"))
    assert (ctx.index.read_bytes() if ctx.index.exists() else None) == before_index
    assert capsys.readouterr().out == ""


def test_touch_index_failure_restores_card_and_index(kb_root, monkeypatch, capsys):
    """touch 的索引发布失败时，卡片和索引必须恢复原字节。"""
    ctx = _ctx(kb_root)
    card = _add_card(ctx, "触碰前")
    before_card = card.read_bytes()
    before_index = ctx.index.read_bytes()
    capsys.readouterr()

    def fail_index(_index, _ctx):
        raise OSError("SIMULATED_INDEX_SAVE_FAILURE")

    monkeypatch.setattr(core, "_save_index", fail_index, raising=False)
    monkeypatch.setattr("agenote.index._save_index", fail_index)
    with pytest.raises(OSError, match="SIMULATED_INDEX_SAVE_FAILURE"):
        core.touch_card(card, "LAST_USED", ctx)

    assert card.read_bytes() == before_card
    assert ctx.index.read_bytes() == before_index
    assert capsys.readouterr().out == ""


def test_connect_second_write_failure_restores_both_cards(kb_root, monkeypatch, capsys):
    """双向链接第二张写失败时，两张卡都必须恢复原字节。"""
    ctx = _ctx(kb_root)
    first = _add_card(ctx, "第一张")
    second_card = list(ctx.experiences.rglob("*.org"))[0]
    # 第一张卡已存在；再创建第二张后分别定位
    cmd_add(_add_args("第二张"), ctx)
    cards_paths = sorted(ctx.experiences.rglob("*.org"), key=lambda p: p.name)
    first, second_card = cards_paths[0], cards_paths[1]
    before = {p: p.read_bytes() for p in cards_paths}
    real_atomic_write = cards.atomic_write
    calls = 0

    def fail_second(path, text):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("SIMULATED_SECOND_LINK_WRITE")
        return real_atomic_write(path, text)

    monkeypatch.setattr(cards, "atomic_write", fail_second)
    with pytest.raises(OSError, match="SIMULATED_SECOND_LINK_WRITE"):
        cmd_connect(argparse.Namespace(id_a=str(first), id_b=str(second_card), desc="关联"), ctx)

    assert {p: p.read_bytes() for p in cards_paths} == before
    assert "已建立双向链接" not in capsys.readouterr().out


def test_update_tech_equal_category_omits_tag(kb_root):
    """tech 与 category 相同则标签行省略 tech（与 add 同口径）。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="rust"), ctx)
    card = _only_card(ctx)

    cmd_update(_update_args(str(card), tech="general"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":TECH:     general" in content
    assert ":general:debug:ai::" in content


def test_update_category_rejects_path_escape(kb_root, capsys):
    """update 不得绕过 add 的 category 路径边界。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    before = card.read_bytes()

    with pytest.raises(SystemExit) as exc:
        cmd_update(_update_args(str(card), category="../../escape"), ctx)

    assert exc.value.code == 1
    assert "类别名不能包含路径分隔符" in capsys.readouterr().err
    assert card.read_bytes() == before


def test_update_category_rejects_newline(kb_root, capsys):
    """category 会进入重命名后的文件名，换行必须在写入前被拒绝。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    before = card.read_bytes()

    with pytest.raises(SystemExit) as exc:
        cmd_update(_update_args(str(card), category="a\nb"), ctx)

    assert exc.value.code == 1
    assert "类别名不能包含换行符" in capsys.readouterr().err
    assert card.read_bytes() == before


def test_update_rejects_newline_tech(kb_root):
    """--tech 含伪 :END: 注入值时必须被 set_org_prop 拒绝，卡片保持原字节。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    before = card.read_bytes()

    with pytest.raises(ValueError, match="换行"):
        cmd_update(_update_args(str(card), tech="a\n:END:"), ctx)

    assert card.read_bytes() == before


def test_update_category_syncs_fingerprint(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="rust"), ctx)
    card = _only_card(ctx)

    cmd_update(_update_args(str(card), category="emacs"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":CATEGORY: emacs" in content
    assert ":emacs:debug:ai:rust::" in content


def test_update_owner_syncs_fingerprint(kb_root):
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="rust"), ctx)
    card = _only_card(ctx)

    cmd_update(_update_args(str(card), owner="collab"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":OWNER:    collab" in content
    assert ":general:debug:collab:rust::" in content


def test_update_status_keeps_lightweight(kb_root):
    """--status 不刷索引/标签（轻量路径，索引由 reindex 刷新）。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="rust"), ctx)
    card = _only_card(ctx)

    cmd_update(_update_args(str(card), status="stale"), ctx)

    content = _only_card(ctx).read_text(encoding="utf-8")
    assert ":STATUS:   stale" in content
    assert ":general:debug:ai:rust::" in content


def test_update_short_id_prefers_exact_org_id(kb_root, monkeypatch):
    """短 ID 不得被同名派生卡的模糊匹配抢走。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始", tech="guile"), ctx)
    original = _only_card(ctx)
    original_match = re.search(r":ID:\s+(\S+)", original.read_text(encoding="utf-8"))
    assert original_match is not None
    original_id = original_match.group(1)
    derived = original.with_name(f"{original_id}-3-debug-general.org")
    derived.write_text(
        re.sub(
            rf"(?m)^:ID:\s+{re.escape(original_id)}$",
            f":ID: {original_id}-3",
            original.read_text(encoding="utf-8"),
            count=1,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "agenote.core.Path.rglob",
        lambda _self, _pattern: [derived, original],
    )

    cmd_update(_update_args(original_id, tech="rust"), ctx)

    assert ":TECH:     rust" in original.read_text(encoding="utf-8")
    assert ":TECH:     guile" in derived.read_text(encoding="utf-8")


def test_get_rejects_absolute_path_outside_kb(kb_root, tmp_path, capsys):
    ctx = _ctx(kb_root)
    outside = tmp_path / "outside.org"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cmd_get(argparse.Namespace(target=str(outside), used=False), ctx)

    assert exc.value.code == 1
    assert "路径超出知识库范围" in capsys.readouterr().err


def test_get_cli_registers_used_flag(kb_root, monkeypatch, capsys):
    """公共 CLI 的 --used 参数必须真正进入 cmd_get，而不是只在 handler 内部支持。"""
    from agenote import cli

    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)

    monkeypatch.setattr(cli, "agenote_context", lambda: ctx)
    monkeypatch.setattr("sys.argv", ["agenote", "get", str(card), "--used"])
    cli.main()

    assert "未找到卡片" not in capsys.readouterr().err
    assert ":USAGE_COUNT: 1" in card.read_text(encoding="utf-8")


def test_get_used_rejects_corrupt_index_without_touching_card(kb_root, monkeypatch, capsys):
    """索引损坏时先失败，不能先递增卡片再崩出 traceback。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    ctx.index.write_text("[]", encoding="utf-8")
    before = card.read_bytes()
    capsys.readouterr()

    from agenote import cli
    monkeypatch.setattr(cli, "agenote_context", lambda: ctx)
    monkeypatch.setattr("sys.argv", ["agenote", "get", str(card), "--used"])
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert card.read_bytes() == before
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_get_used_does_not_modify_body_properties(kb_root, monkeypatch):
    """正文示例中的同名属性不是卡片元数据，get --used 不得改写。"""
    from agenote import cli

    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    body_line = ":LAST_USED: [2000-01-01 Sat 00:00]\n:USAGE_COUNT: 999"
    with card.open("a", encoding="utf-8") as fp:
        fp.write("\n" + body_line + "\n")

    monkeypatch.setattr(cli, "agenote_context", lambda: ctx)
    monkeypatch.setattr("sys.argv", ["agenote", "get", str(card), "--used"])
    cli.main()

    content = card.read_text(encoding="utf-8")
    assert body_line in content
    assert content.count(":USAGE_COUNT: 1") == 1
    assert re.search(r"(?m)^:USAGE_COUNT:\s+1$", content)


def test_get_used_rejects_body_only_drawer(kb_root, monkeypatch, capsys):
    """没有真实顶层抽屉时必须明确失败，不能返回成功却没计数。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    card.write_text(
        "* DONE 原始\n\n正文示例：\n:PROPERTIES:\n:USAGE_COUNT: 999\n:END:\n",
        encoding="utf-8",
    )
    before = card.read_bytes()
    capsys.readouterr()

    from agenote import cli
    monkeypatch.setattr(cli, "agenote_context", lambda: ctx)
    monkeypatch.setattr("sys.argv", ["agenote", "get", str(card), "--used"])
    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 1
    assert card.read_bytes() == before
    captured = capsys.readouterr()
    assert captured.out == ""
    err = captured.err
    assert "顶层 PROPERTIES" in err
    assert "Traceback" not in err


def test_get_used_concurrent_counts_are_not_lost(kb_root):
    """8 路公共 CLI 并发时 usage 留痕必须精确，不能靠单进程 mock 假绿。"""
    ctx = _ctx(kb_root)
    cmd_add(_add_args("原始"), ctx)
    card = _only_card(ctx)
    env = os.environ.copy()
    env.update(
        KB_ROOT=str(kb_root),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"),
    )
    env.pop("AGENOTE_AGENT", None)

    workers = [
        subprocess.Popen(
            [sys.executable, "-m", "agenote.cli", "get", str(card), "--used"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]
    for worker in workers:
        _, stderr = worker.communicate(timeout=30)
        assert worker.returncode == 0, stderr

    content = card.read_text(encoding="utf-8")
    assert re.search(r"(?m)^:USAGE_COUNT:\s+8$", content)
    assert parse_org_prop(content, "USAGE_COUNT") == "8"


def test_get_completion_includes_used_flag():
    """新增公共选项必须同步三种 shell 补全。"""
    from agenote.completions import generate

    for shell in ("bash", "zsh", "fish"):
        assert "-l used" in generate(shell) or "--used" in generate(shell)

# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.index — JSON 卡片索引管理。

从 core.py 拆出（ADR-0003）：索引读写（_load/_save）、全量重建（_rebuild）、
增量更新（_upsert）、条目抽取（_card_dict）集中于此。
依赖方向：index → core(KBContext) + orgserde(属性解析)，单向无循环。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from agenote.core import (
    DEFAULT_CATEGORY,
    DEFAULT_OWNER,
    DEFAULT_TYPE,
    KBContext,
    default_context,
)
from agenote.orgserde import (
    _parse_float_prop,
    _parse_int_prop,
    parse_org_prop,
    read_org_title,
)


def _card_dict(filepath: Path, ctx: "KBContext | None" = None) -> dict | None:
    """从一张卡片文件提取索引条目，返回 dict 或 None。

    跳过符号链接（避免索引 Guix store 中的重复文件）。
    tags 字段按逗号展开，解决 tech 含逗号时的数据污染问题。
    """
    ctx = ctx or default_context()
    if filepath.is_symlink():
        return None
    content = filepath.read_text(encoding="utf-8")
    card_id = parse_org_prop(content, "ID") or filepath.stem.split("-")[0]
    created = parse_org_prop(content, "CREATED")
    if created:
        created = re.sub(r"[\[\]]", "", created).split()[0]
    entry_type = parse_org_prop(content, "ENTRY_TYPE") or None
    # source_agent：记录卡片写入者。旧卡片无此属性 → 空串（迁移时补 pi）。
    source_agent = parse_org_prop(content, "SOURCE_AGENT") or ""

    # ── 解析 tags 行 ─────────────────────────────────────────────────────
    # tags 行格式: ":category:type:owner:tech::"（冒号分隔，尾部双冒号）
    # tech 字段可能含逗号（如 "Hexo,Playwright,GuixSD"），需按逗号展开
    tags_line = ""
    m = re.search(r":(\S+)::", content)
    if m:
        tags_line = m.group(1)
    raw_tags = tags_line.split(":") if tags_line else []
    # 展开含逗号的标签（如 "Hexo,Playwright" → ["Hexo", "Playwright"]）
    expanded_tags = []
    for tag in raw_tags:
        expanded_tags.extend(t.strip() for t in tag.split(",") if t.strip())

    return {
        "id": card_id,
        "file": str(filepath.relative_to(ctx.root)),
        "title": read_org_title(content),
        "category": parse_org_prop(content, "CATEGORY") or DEFAULT_CATEGORY,
        "tech": parse_org_prop(content, "TECH") or "",
        "type": parse_org_prop(content, "TYPE") or DEFAULT_TYPE,
        "owner": parse_org_prop(content, "OWNER") or DEFAULT_OWNER,
        "entry_type": entry_type,
        "source_agent": source_agent,
        "status": parse_org_prop(content, "STATUS") or "done",
        "last_used": parse_org_prop(content, "LAST_USED"),
        "last_verified": parse_org_prop(content, "LAST_VERIFIED"),
        "created": created or "",
        "tags": expanded_tags,
        "weight": _parse_float_prop(content, "WEIGHT", ctx.default_weight),
        "usage_count": _parse_int_prop(content, "USAGE_COUNT", 0),
    }


def _load_index(ctx: "KBContext | None" = None) -> dict:
    """加载 JSON 索引，失败返回空骨架。"""
    ctx = ctx or default_context()
    if ctx.index.exists():
        try:
            return json.loads(ctx.index.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "updated": "", "total": 0, "cards": []}


def _save_index(index: dict, ctx: "KBContext | None" = None) -> None:
    """写入 JSON 索引。"""
    ctx = ctx or default_context()
    index["updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    index["total"] = len(index["cards"])
    ctx.index.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _rebuild_index(ctx: "KBContext | None" = None) -> dict:
    """全量扫描 experiences/ 重建索引 dict。"""
    ctx = ctx or default_context()
    cards = []
    for f in sorted(
        ctx.experiences.rglob("*.org"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        d = _card_dict(f, ctx)
        if d:
            cards.append(d)
    return {"version": 1, "updated": "", "total": len(cards), "cards": cards}


def _upsert_card(index: dict, filepath: Path, ctx: "KBContext | None" = None) -> None:
    """增量更新：插入或替换一张卡片到索引。"""
    ctx = ctx or default_context()
    d = _card_dict(filepath, ctx)
    if not d:
        return
    for i, c in enumerate(index["cards"]):
        if c["id"] == d["id"]:
            index["cards"][i] = d
            break
    else:
        index["cards"].insert(0, d)

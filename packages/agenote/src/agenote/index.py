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
from collections import Counter
from datetime import datetime
from pathlib import Path

from agenote.core import (
    DEFAULT_CATEGORY,
    DEFAULT_OWNER,
    DEFAULT_TYPE,
    KBContext,
    SEED_AGENTS,
    SEED_TYPES,
    PublicError,
    STALE_DAYS,
    TYPE_PROMOTE_MIN,
    WEIGHT_STALE_PENALTY,
    WEIGHT_USAGE_BONUS,
    WEIGHT_USAGE_CAP,
    default_context,
)
from agenote.safeio import atomic_write
from agenote.orgserde import (
    _parse_int_prop,
    parse_org_prop,
    read_org_title,
)


class InvalidIndexError(PublicError):
    """已有 JSON 索引损坏或结构非法。"""


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

    # WEIGHT 是派生值（不读文件属性，文件中的遗留 WEIGHT 被忽略）：
    # base(域默认) × 使用系数 × 新鲜度系数，rebuild/upsert 全路径一致重算。
    usage = _parse_int_prop(content, "USAGE_COUNT", 0)
    usage_factor = 1 + WEIGHT_USAGE_BONUS * min(usage, WEIGHT_USAGE_CAP)
    stale_factor = 1.0
    last_used_raw = parse_org_prop(content, "LAST_USED")
    if last_used_raw:
        try:
            lu = datetime.strptime(
                re.sub(r"[\[\]]", "", last_used_raw).split()[0], "%Y-%m-%d"
            )
            if (datetime.now() - lu).days > STALE_DAYS:
                stale_factor = WEIGHT_STALE_PENALTY
        except (ValueError, IndexError):
            pass
    weight = round(ctx.default_weight * usage_factor * stale_factor, 3)

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
        "last_used": last_used_raw,
        "last_verified": parse_org_prop(content, "LAST_VERIFIED"),
        "created": created or "",
        "tags": expanded_tags,
        "weight": weight,
        "usage_count": usage,
    }


def _valid_index_card(card: object) -> bool:
    """判断索引条目是否满足各读路径共同依赖的最小结构。"""
    if not isinstance(card, dict):
        return False
    if not isinstance(card.get("id"), str):
        return False
    string_fields = ("category", "type", "owner", "status", "title", "file")
    if not all(isinstance(card.get(key), str) for key in string_fields):
        return False
    optional_string_fields = ("tech", "source_agent", "last_used", "last_verified", "created")
    if not all(
        key not in card or isinstance(card[key], str)
        for key in optional_string_fields
    ):
        return False
    if "entry_type" in card and card["entry_type"] is not None:
        if not isinstance(card["entry_type"], str):
            return False
    if not isinstance(card.get("tags", []), list):
        return False
    if "weight" in card and (
        not isinstance(card["weight"], (int, float))
        or isinstance(card["weight"], bool)
    ):
        return False
    if "usage_count" in card and (
        not isinstance(card["usage_count"], int)
        or isinstance(card["usage_count"], bool)
    ):
        return False
    return True


def find_legacy_weight_files(ctx: "KBContext | None" = None) -> list[str]:
    """S7：扫描 experiences/ 下仍带 :WEIGHT: 属性的卡片（索引忽略该值，按公式重算）。

    返回相对 ctx.root 的路径列表；reindex 告警提示清理用。
    """
    ctx = ctx or default_context()
    found: list[str] = []
    if not ctx.experiences.exists():
        return found
    for path in sorted(ctx.experiences.rglob("*.org")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if parse_org_prop(content, "WEIGHT"):
            try:
                found.append(str(path.relative_to(ctx.root)))
            except ValueError:
                found.append(str(path))
    return found


def _load_index(ctx: "KBContext | None" = None) -> dict:
    """加载 JSON 索引；已有文件损坏或结构非法时 fail-closed，缺失文件返回空骨架。"""
    ctx = ctx or default_context()
    if not ctx.index.exists():
        return {"version": 1, "updated": "", "total": 0, "cards": []}
    try:
        index = json.loads(ctx.index.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise InvalidIndexError(f"索引损坏: {ctx.index}") from exc
    if not isinstance(index, dict) or not isinstance(index.get("cards"), list):
        raise InvalidIndexError(f"索引结构非法: {ctx.index}")
    if not all(_valid_index_card(card) for card in index["cards"]):
        raise InvalidIndexError(f"索引结构非法: {ctx.index}")
    if (
        not isinstance(index.get("version"), int)
        or isinstance(index.get("version"), bool)
        or not isinstance(index.get("total"), int)
        or isinstance(index.get("total"), bool)
        or index["total"] < 0
        or not isinstance(index.get("updated"), str)
        or index["total"] != len(index["cards"])
    ):
        raise InvalidIndexError(f"索引结构非法: {ctx.index}")
    return index


def type_counts(ctx: "KBContext | None" = None) -> Counter:
    """非归档卡片的 type 计数（门禁/聚拢共用同一口径）。"""
    # 已有索引的公共读路径也必须 fail-closed：不能把损坏索引伪装成空数据。
    # 仅索引文件不存在时返回空骨架，兼容首次使用。
    index = _load_index(ctx)
    return Counter(
        c["type"]
        for c in index["cards"]
        if c.get("type") and c.get("status") != "archived"
    )


def formal_types(ctx: "KBContext | None" = None) -> set[str]:
    """正式 type 集合 = 种子集 ∪ 非归档卡片数达晋升阈值的 type（实时计算）。"""
    return SEED_TYPES | {
        t for t, n in type_counts(ctx).items() if n >= TYPE_PROMOTE_MIN
    }


def known_agents(ctx: "KBContext | None" = None) -> set[str]:
    """已知 agent 集合 = 种子集 ∪ index 中出现过的 source_agent（实时计算）。

    含归档卡片（历史写入者仍是已知 agent）；与 formal_types 同构、无持久状态。
    """
    # 已有索引的公共读路径同样 fail-closed。
    index = _load_index(ctx)
    return SEED_AGENTS | {
        a for a in (c.get("source_agent", "") for c in index["cards"]) if a
    }


def _save_index(index: dict, ctx: "KBContext | None" = None) -> None:
    """写入 JSON 索引。"""
    ctx = ctx or default_context()
    index["updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    index["total"] = len(index["cards"])
    atomic_write(
        ctx.index,
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
    )


def _rebuild_index(ctx: "KBContext | None" = None) -> dict:
    """全量扫描 experiences/ 重建索引 dict（WEIGHT 随之按公式重算）。"""
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
        if c.get("id") == d["id"]:
            index["cards"][i] = d
            break
    else:
        index["cards"].insert(0, d)

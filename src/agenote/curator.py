# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.curator — 知识策展原子工具（归档/恢复/去重/审查）。

从 cards.py 拆出（ADR-0002）：归档状态机、标题相似度去重、单卡审查集中于此。
策展流程（状态重整顺序、去留取舍）由 agent 依据 agenote-curator skill 主导，
CLI 只提供检测报告与原子写命令。_jaccard_similarity 由 health 复用（统一去重算法）。
"""

import argparse
import json
import re
from datetime import datetime

from agenote.core import (
    DEDUP_CATEGORY_BONUS,
    DEDUP_TECH_BONUS,
    DEDUP_THRESHOLD,
    VALID_STATUSES,
    ARCHIVE_THRESHOLD_DAYS,
    die,
    now,
    _resolve_card,
    default_context,
)
from agenote.orgserde import (
    parse_org_prop,
    read_org_title,
)
from agenote.index import (
    _load_index,
    _save_index,
    _upsert_card,
)
from agenote.safeio import atomic_write


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: archive / restore — 归档与恢复
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_archive(args: argparse.Namespace, ctx=None) -> None:
    """归档指定卡片（支持批量），或列出归档候选（只读）。"""
    ctx = ctx or default_context()
    if getattr(args, "list_cards", False):
        _archive_list(ctx, json_output=getattr(args, "json", False))
        return

    if getattr(args, "stale", False):
        _archive_stale_candidates(ctx, json_output=getattr(args, "json", False))
        return

    # 归档指定卡片（agent 审查候选清单后批量执行）
    if not args.id:
        die("请指定卡片 ID 或使用 --stale 查看归档候选")
    archived = []
    for card_id in args.id:
        card = _resolve_card(card_id, ctx)
        if not card:
            die(f"未找到卡片: {card_id}")

        content = card.read_text(encoding="utf-8")
        if ":STATUS:" in content:
            content = re.sub(r":STATUS:\s*.+", ":STATUS:   archived", content)
        else:
            content = content.replace(":END:", ":STATUS:   archived\n:END:", 1)
        if ":ARCHIVED_AT:" not in content:
            content = content.replace(":END:", f":ARCHIVED_AT: [{now()}]\n:END:", 1)
        if getattr(args, "reason", None):
            if ":ARCHIVE_REASON:" not in content:
                content = content.replace(
                    ":END:", f":ARCHIVE_REASON: {args.reason}\n:END:", 1
                )

        atomic_write(card, content)
        index = _load_index(ctx)
        _upsert_card(index, card, ctx)
        _save_index(index, ctx)
        archived.append(card.name)
    print(f"已归档 {len(archived)} 张: {', '.join(archived)}")


def _archive_list(ctx=None, json_output: bool = False) -> None:
    """列出所有归档卡片。"""
    ctx = ctx or default_context()
    index = _load_index(ctx)
    archived = [c for c in index["cards"] if c.get("status") == "archived"]
    if not archived:
        print("无归档卡片")
        return
    if json_output:
        print(json.dumps(archived, ensure_ascii=False, indent=2))
    else:
        for c in archived:
            print(f"  {c['id']}  {c['title'][:60]}")
        print(f"\n共 {len(archived)} 张归档卡片")


def _archive_stale_candidates(ctx=None, json_output: bool = False) -> None:
    """列出归档候选：超过阈值天数未验证的 stale 卡片（只读）。

    去留由 agent 审查后决定，执行用 `agenote archive <id...> --reason`。
    """
    ctx = ctx or default_context()
    index = _load_index(ctx)
    candidates = []
    for card_info in index["cards"]:
        if card_info.get("status") != "stale":
            continue
        last_verified = card_info.get("last_verified", "")
        if not last_verified:
            continue
        try:
            verified_date = re.sub(r"[\[\]]", "", last_verified).split()[0]
            days = (datetime.now() - datetime.strptime(verified_date, "%Y-%m-%d")).days
        except (ValueError, IndexError):
            continue
        if days > ARCHIVE_THRESHOLD_DAYS:
            candidates.append(
                {
                    "id": card_info["id"],
                    "title": card_info.get("title", "")[:60],
                    "days_unverified": days,
                }
            )

    if not candidates:
        print(f"无归档候选（stale 且 >{ARCHIVE_THRESHOLD_DAYS} 天未验证）")
        return
    if json_output:
        print(json.dumps(candidates, ensure_ascii=False, indent=2))
    else:
        for c in candidates:
            print(f"  {c['id']}  {c['title']}  (>{c['days_unverified']}天未验证)")
        print(
            f"\n共 {len(candidates)} 张归档候选——审查后执行: "
            f"agenote archive <id...> --reason \"策展: >{ARCHIVE_THRESHOLD_DAYS}天未验证\""
        )


def cmd_restore(args: argparse.Namespace, ctx=None) -> None:
    """恢复归档卡片。"""
    ctx = ctx or default_context()
    card = _resolve_card(args.id, ctx)
    if not card:
        die(f"未找到卡片: {args.id}")

    new_status = args.status or "stable"
    if new_status not in VALID_STATUSES:
        die(f"无效状态: {new_status}（可选: {', '.join(sorted(VALID_STATUSES))}）")

    content = card.read_text(encoding="utf-8")
    if ":STATUS:" in content:
        content = re.sub(r":STATUS:\s*.+", f":STATUS:   {new_status}", content)
    content = re.sub(r":ARCHIVED_AT:\s*.+\n?", "", content)
    content = re.sub(r":ARCHIVE_REASON:\s*.+\n?", "", content)
    if ":LAST_VERIFIED:" in content:
        content = re.sub(
            r":LAST_VERIFIED:\s*\[.+?\]", f":LAST_VERIFIED: [{now()}]", content
        )
    else:
        content = content.replace(":END:", f":LAST_VERIFIED: [{now()}]\n:END:", 1)

    atomic_write(card, content)
    index = _load_index(ctx)
    _upsert_card(index, card, ctx)
    _save_index(index, ctx)
    print(f"已恢复为 {new_status}: {card.name}")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: deduplicate — 检测重复卡片
# ═══════════════════════════════════════════════════════════════════════════════


def _jaccard_similarity(s1: str, s2: str) -> float:
    """标题词级 Jaccard 相似度。

    抽出为模块级函数供 health._detect_duplicates 复用，统一去重算法。
    """
    w1 = set(s1.casefold().split())
    w2 = set(s2.casefold().split())
    if not w1 or not w2:
        return 0.0
    return len(w1 & w2) / len(w1 | w2)


def cmd_deduplicate(args: argparse.Namespace, ctx=None) -> None:
    """基于标题相似度和 category/tech 匹配检测重复卡片。"""
    ctx = ctx or default_context()
    threshold = args.threshold or DEDUP_THRESHOLD
    index = _load_index(ctx)
    cards_list = [c for c in index["cards"] if c.get("status") != "archived"]

    pairs = []
    for i in range(len(cards_list)):
        for j in range(i + 1, len(cards_list)):
            a, b = cards_list[i], cards_list[j]
            sim = _jaccard_similarity(a.get("title", ""), b.get("title", ""))
            if a.get("category") == b.get("category"):
                sim += DEDUP_CATEGORY_BONUS
            if a.get("tech") and a.get("tech") == b.get("tech"):
                sim += DEDUP_TECH_BONUS
            sim = min(sim, 1.0)
            if sim >= threshold:
                pairs.append((a, b, sim))

    if not pairs:
        print("未检测到重复卡片")
        return

    if getattr(args, "json", False):
        output = [
            {
                "id_a": a["id"],
                "id_b": b["id"],
                "similarity": round(s, 2),
                "title_a": a["title"][:60],
                "title_b": b["title"][:60],
            }
            for a, b, s in pairs
        ]
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        for a, b, sim in sorted(pairs, key=lambda x: -x[2]):
            print(f"  [{sim:.0%}] {a['id']}: {a['title'][:50]}")
            print(f"         {b['id']}: {b['title'][:50]}")
        print(f"\n共 {len(pairs)} 对疑似重复 (阈值={threshold:.0%})")
        print("核实后用 agenote merge <primary> <secondary> --desc 手动合并")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: review — 审查卡片
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_review(args: argparse.Namespace, ctx=None) -> None:
    """审查单张卡片的状态、时效性、关联数和质量。"""
    ctx = ctx or default_context()
    card = _resolve_card(args.id, ctx)
    if not card:
        die(f"未找到卡片: {args.id}")

    content = card.read_text(encoding="utf-8")
    card_id = parse_org_prop(content, "ID") or card.stem.split("-")[0]
    title = read_org_title(content)
    status = parse_org_prop(content, "STATUS") or "done"
    last_verified = parse_org_prop(content, "LAST_VERIFIED")
    created = parse_org_prop(content, "CREATED")

    # 计算时效性
    days_since_verified = None
    if last_verified:
        try:
            vd = re.sub(r"[\[\]]", "", last_verified).split()[0]
            days_since_verified = (
                datetime.now() - datetime.strptime(vd, "%Y-%m-%d")
            ).days
        except (ValueError, IndexError):
            pass

    # 计算关联数
    link_count = len(re.findall(r"\[\[file:", content))

    # 质量检查
    issues = []
    if not parse_org_prop(content, "CATEGORY"):
        issues.append("缺少 CATEGORY")
    if not parse_org_prop(content, "TECH"):
        issues.append("缺少 TECH")
    if link_count == 0:
        issues.append("无关联链接（孤立卡片）")

    # 状态建议
    if status == "done" and days_since_verified is None:
        suggestion = "建议策展后设为 stable"
    elif status == "stale":
        suggestion = "建议验证后设为 stable 或归档"
    elif status == "archived":
        suggestion = "已归档，可恢复或删除"
    else:
        suggestion = "状态良好"

    print(f"=== 卡片审查: {card_id} ===")
    print(f"标题: {title}")
    print(f"状态: {status}")
    print(f"创建: {created or '未知'}")
    print(f"最后验证: {last_verified or '未验证'}")
    if days_since_verified is not None:
        print(f"距上次验证: {days_since_verified} 天")
    print(f"关联数: {link_count}")
    print(f"建议: {suggestion}")
    if issues:
        print("问题:")
        for issue in issues:
            print(f"  ❌ {issue}")
        print("修复用: agenote update <id> --category <值> / --tech <值> / connect 建关联")
    else:
        print("质量检查: ✅ 全部通过")

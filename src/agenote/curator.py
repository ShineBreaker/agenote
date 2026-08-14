# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.curator — 知识策展（归档/恢复/去重/审查/一键策展）。

从 cards.py 拆出（ADR-0002）：done→stable→stale→archived 状态机、
权重重分配、标题相似度去重集中于此。_jaccard_similarity 由 health
复用（统一去重算法），cmd_curate 运行时 lazy import health（无循环）。
"""

import argparse
import json
import re
from datetime import datetime

from agenote.core import (
    VALID_STATUSES,
    STALE_DAYS,
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
    _rebuild_index,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: archive / restore — 归档与恢复
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_archive(args: argparse.Namespace, ctx=None) -> None:
    """归档卡片或自动归档过时卡片。"""
    ctx = ctx or default_context()
    if getattr(args, "list_cards", False):
        _archive_list(ctx, json_output=getattr(args, "json", False))
        return

    if getattr(args, "stale", False):
        _archive_auto_stale(ctx)
        return

    # 归档指定卡片
    if not args.id:
        die("请指定卡片 ID 或使用 --stale")
    card = _resolve_card(args.id, ctx)
    if not card:
        die(f"未找到卡片: {args.id}")

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

    card.write_text(content, encoding="utf-8")
    index = _load_index(ctx)
    _upsert_card(index, card, ctx)
    _save_index(index, ctx)
    print(f"已归档: {card.name}")


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


def _archive_auto_stale(ctx=None) -> None:
    """自动归档超过阈值天数的 stale 卡片。"""
    ctx = ctx or default_context()
    index = _load_index(ctx)
    count = 0
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
            card = _resolve_card(card_info["id"], ctx)
            if card and card.exists():
                content = card.read_text(encoding="utf-8")
                if ":STATUS:" in content:
                    content = re.sub(r":STATUS:\s*.+", ":STATUS:   archived", content)
                if ":ARCHIVED_AT:" not in content:
                    content = content.replace(
                        ":END:", f":ARCHIVED_AT: [{now()}]\n:END:", 1
                    )
                card.write_text(content, encoding="utf-8")
                _upsert_card(index, card, ctx)
                count += 1
                print(f"  自动归档: {card.name} (>{ARCHIVE_THRESHOLD_DAYS}天未验证)")
    if count:
        _save_index(index, ctx)
    print(f"自动归档完成: {count} 张卡片")


def _mark_auto_stale(ctx=None) -> None:
    """自动把超阈值未使用的非终态卡片标记为 stale（done/stable → stale）。

    补全 curator 状态机缺失的一跳：curate 第 2 步权重重分配已对 LAST_USED
    超 STALE_DAYS 的卡片打 0.8 权重惩罚，本函数用同一判定条件同步降级 STATUS，
    使其进入第 4 步 ``_archive_auto_stale`` 的归档候选。不动 archived（终态）
    和已是 stale 的卡片（幂等）。
    """
    ctx = ctx or default_context()
    index = _load_index(ctx)
    count = 0
    now_dt = datetime.now()
    for card_info in index["cards"]:
        if card_info.get("status", "done") not in ("done", "stable"):
            continue
        last_used = card_info.get("last_used", "")
        if not last_used:
            continue
        try:
            lu_date = re.sub(r"[\[\]]", "", last_used).split()[0]
            days = (now_dt - datetime.strptime(lu_date, "%Y-%m-%d")).days
        except (ValueError, IndexError):
            continue
        if days > STALE_DAYS:
            card = _resolve_card(card_info["id"], ctx)
            if card and card.exists():
                content = card.read_text(encoding="utf-8")
                if ":STATUS:" in content:
                    content = re.sub(r":STATUS:\s*.+", ":STATUS:   stale", content)
                else:
                    content = content.replace(
                        ":END:", ":STATUS:   stale\n:END:", 1
                    )
                card.write_text(content, encoding="utf-8")
                _upsert_card(index, card, ctx)
                count += 1
                print(f"  标记 stale: {card.name} (>{STALE_DAYS}天未使用)")
    if count:
        _save_index(index, ctx)
    print(f"状态降级完成: {count} 张卡片 done/stable → stale")


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

    card.write_text(content, encoding="utf-8")
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
    threshold = args.threshold or 0.7
    index = _load_index(ctx)
    cards_list = [c for c in index["cards"] if c.get("status") != "archived"]

    pairs = []
    for i in range(len(cards_list)):
        for j in range(i + 1, len(cards_list)):
            a, b = cards_list[i], cards_list[j]
            sim = _jaccard_similarity(a.get("title", ""), b.get("title", ""))
            if a.get("category") == b.get("category"):
                sim += 0.15
            if a.get("tech") and a.get("tech") == b.get("tech"):
                sim += 0.1
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

    if getattr(args, "merge", False) and pairs:
        print("\n--merge 模式：请用 kb merge 手动合并")


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
    else:
        print("质量检查: ✅ 全部通过")

    # --fix 模式
    if getattr(args, "fix", False) and issues:
        fixes = []
        if "缺少 CATEGORY" in issues:
            content = re.sub(r":CATEGORY:\s*\n", ":CATEGORY: general\n", content)
            fixes.append("已设置 CATEGORY=general")
        if "缺少 TECH" in issues:
            content = re.sub(r":TECH:\s*\n", ":TECH: general\n", content)
            fixes.append("已设置 TECH=general")
        if fixes:
            card.write_text(content, encoding="utf-8")
            for fix in fixes:
                print(f"  🔧 {fix}")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: curate — 一键策展（健康 + 去重 + 归档 + 重建索引 + 权重重分配）
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_curate(args: argparse.Namespace, ctx=None) -> None:
    """一键策展：健康检查 + 权重重分配 + 去重 + 归档陈旧 + 重建索引。"""
    from agenote.core import (
        HUMAN_DEFAULT_WEIGHT,
        AGENT_DEFAULT_WEIGHT,
        WEIGHT_USAGE_BONUS,
        WEIGHT_USAGE_CAP,
        WEIGHT_STALE_PENALTY,
    )

    ctx = ctx or default_context()
    print(f"=== curate ({ctx.name}) ===")

    # 1. 健康检查（cmd_health 在 agenote.health，lazy import 避免循环）
    print("\n── 1. 健康检查 ──")
    from agenote.health import cmd_health

    cmd_health(args, ctx)

    # 2. 权重重分配
    print("\n── 2. 权重重分配 ──")
    base_weight = HUMAN_DEFAULT_WEIGHT if ctx.is_human else AGENT_DEFAULT_WEIGHT
    index = _load_index(ctx)
    now_dt = datetime.now()
    reassigned = 0
    for card in index["cards"]:
        usage = card.get("usage_count", 0)
        last_used = card.get("last_used", "")
        # 使用次数提升：1 + 0.1 × min(usage, 10)
        usage_factor = 1 + WEIGHT_USAGE_BONUS * min(usage, WEIGHT_USAGE_CAP)
        # 新鲜度惩罚：last_used 超 STALE_DAYS 则 ×0.8
        stale_factor = 1.0
        if last_used:
            try:
                lu = datetime.strptime(last_used.strip("[]").split()[0], "%Y-%m-%d")
                if (now_dt - lu).days > STALE_DAYS:
                    stale_factor = WEIGHT_STALE_PENALTY
            except (ValueError, IndexError):
                pass
        new_weight = round(base_weight * usage_factor * stale_factor, 3)
        if abs(new_weight - card.get("weight", base_weight)) > 0.001:
            reassigned += 1
        card["weight"] = new_weight
    _save_index(index, ctx)
    print(f"  重新分配权重: {reassigned}/{len(index['cards'])} 张卡片变化")

    # 2.5 状态降级：长期未使用的 done/stable → stale（与第 2 步同判定条件）
    print("\n── 2.5 状态降级 ──")
    _mark_auto_stale(ctx)

    # 3. 去重检测
    print("\n── 3. 去重检测 ──")
    cmd_deduplicate(args, ctx)

    # 4. 归档陈旧
    print("\n── 4. 归档陈旧 ──")
    archive_args = argparse.Namespace(
        id=None, reason=None, list_cards=False, stale=True, json=False
    )
    cmd_archive(archive_args, ctx)

    # 5. 重建索引
    print("\n── 5. 重建索引 ──")
    new_idx = _rebuild_index(ctx)
    _save_index(new_idx, ctx)
    print(f"  索引已重建: {new_idx['total']} 条")
    print("\n=== curate 完成 ===")

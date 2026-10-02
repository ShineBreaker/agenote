# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
#
"""agenote.distill — workflow packaging（发现可沉淀为 skill 的工作流候选）。

扫 KB 卡片里**被反复使用的工作流模式**，聚类为候选清单。纯只读发现器：
不生成草稿、不落盘——skill 草稿由 agent 评估候选后自行撰写（对齐 dream 哲学：
发现交给 CLI，综合决策交给 agent）。

设计原则（从 MiMoCode `agent/prompt/distill.txt` 提炼）：
1. **No extract without evidence**：只有 ≥2 张同主题卡片才聚类为候选，
   没有就明说"无候选"。**零候选即成功**（distill.txt:39-41）。
2. **不调 LLM 生成正文**：只给出聚类与源卡片清单，不杜撰 skill 步骤。

聚类维度：category + tech（同技术栈的卡片视为同一工作流候选）。
触发条件：`type == ascended`（经过多轮试错验证的最优方案）或 `usage_count >= 2`。
"""

import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field

from agenote import config
from agenote.index import _load_index
from agenote.core import agenote_context

# ═══════════════════════════════════════════════════════════════════════════════
# 常量（默认值见 config.py SCHEMA [distill] 节）
# ═══════════════════════════════════════════════════════════════════════════════

MIN_CLUSTER_SIZE = int(config.get("distill", "min_cluster_size"))  # 同 category+tech 至少 N 张才聚类
MIN_USAGE_FOR_ASCEND = int(config.get("distill", "min_usage_for_ascend"))  # usage_count >= N 视为"反复使用"
ASCENDED_TYPE = "ascended"  # 经多轮试错验证的卡片类型


@dataclass
class DistillCandidate:
    """一个 distill 聚类出的候选 skill（待 agent 评估）。"""

    name: str  # 候选 skill 名（kebab-case，来自 category+tech）
    title: str  # 人类可读标题
    category: str
    tech: str
    card_count: int
    card_ids: list[str]  # 源卡片 id 列表（溯源）
    card_titles: list[str]  # 源卡片标题（供 review 判断）


@dataclass
class DistillReport:
    """一次 distill 运行报告。"""

    total_kb_cards: int = 0
    candidates: list[dict] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助
# ═══════════════════════════════════════════════════════════════════════════════


def _cluster_key(card: dict) -> tuple[str, str]:
    """聚类键：(category, tech)。tech 缺失时回退 category。"""
    cat = (card.get("category") or "general").strip() or "general"
    tech = (card.get("tech") or cat).strip() or cat
    return (cat, tech)


def _to_skill_name(category: str, tech: str) -> str:
    """把 category+tech 转 kebab-case skill 名。"""
    raw = f"{tech}-{category}" if tech != category else category
    # 保留 CJK + ASCII 字母数字，其余转 -
    name = re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]+", "-", raw).strip("-").lower()
    return name or "unnamed-skill"


# ═══════════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════════


def _gather_candidates(cards: list[dict]) -> list[tuple[DistillCandidate, list[dict]]]:
    """从 KB 卡片聚类候选。

    入选条件（任一）：
    - type == ascended（多轮试错验证）
    - usage_count >= MIN_USAGE_FOR_ASCEND（反复使用）

    同 (category, tech) 的入选卡片 ≥ MIN_CLUSTER_SIZE 才成候选。
    """
    # 先筛入选卡片
    eligible: list[dict] = []
    for c in cards:
        if c.get("type") == ASCENDED_TYPE:
            eligible.append(c)
            continue
        try:
            if int(c.get("usage_count", 0) or 0) >= MIN_USAGE_FOR_ASCEND:
                eligible.append(c)
        except (ValueError, TypeError):
            pass

    # 按 (category, tech) 聚类
    clusters: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in eligible:
        clusters[_cluster_key(c)].append(c)

    candidates: list[tuple[DistillCandidate, list[dict]]] = []
    for (cat, tech), group in clusters.items():
        if len(group) < MIN_CLUSTER_SIZE:
            continue
        name = _to_skill_name(cat, tech)
        # 按创建时间排序，取标题作为候选标题
        group.sort(key=lambda c: c.get("created", ""), reverse=True)
        cand = DistillCandidate(
            name=name,
            title=f"{tech} 工作流（{cat}）",
            category=cat,
            tech=tech,
            card_count=len(group),
            card_ids=[c.get("id", "") for c in group],
            card_titles=[c.get("title", "") for c in group],
        )
        candidates.append((cand, group))

    candidates.sort(key=lambda x: x[0].card_count, reverse=True)
    return candidates


def run_distill() -> DistillReport:
    """跑一次 distill（workflow packaging，纯只读）。"""
    report = DistillReport()
    ctx = agenote_context()
    index = _load_index(ctx)
    cards = index.get("cards", [])
    report.total_kb_cards = len(cards)

    if not cards:
        report.message = "KB 为空，无待 distill 工作流（零候选即成功）"
        return report

    candidates = _gather_candidates(cards)

    if not candidates:
        report.message = (
            "无候选：KB 中没有 ≥%d 张同主题的 ascended/高频卡片（零候选即成功）"
            % MIN_CLUSTER_SIZE
        )
        return report

    report.candidates = [
        {**asdict(c), "cards_in_group": len(g)} for c, g in candidates
    ]
    report.message = (
        "%d 个候选工作流——评估源卡片后由 agent 撰写 skill（草稿落盘功能已移除）"
        % len(candidates)
    )
    return report

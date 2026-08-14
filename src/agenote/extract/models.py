# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.extract.models — 抽取层共享数据模型。

零依赖（仅 stdlib dataclasses），供 framework/adapter/reconcile 三方共享，
打破「adapter 从 reconcile 导入模型」的反向依赖。reconcile.py re-export
保持旧引用路径（from agenote.reconcile import ReconciledFact）不变。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 自家 agent（opencode/zcode/omp/hermes）的统一检索权重基准；
# 外部源（codex/claude/crush …）在此基础上减 0.1。
RECONCILE_DEFAULT_WEIGHT = 0.7


@dataclass
class ReconciledFact:
    """从外部 agent memory 抽取的一条只读事实（reconcile 索引项）。

    字段对齐 agenote _card_dict 的结构，便于 agenote_search 统一处理。
    但 reconcile 卡片**没有对应 .org 文件**（file 字段为空），只活在
    .reconcile/index.json 里，是纯检索辅助。
    """

    id: str  # 跨源唯一：f"{source}:{native_id}"
    source: str  # 来源 agent 名（hermes / crush / claude-code …）
    native_id: str  # 源系统的原始 id（hermes 的 fact_id）
    title: str  # 提取的标题（hermes 的【...】）
    category: str  # 映射后的 kb category
    content: str  # 完整正文
    trust_score: float  # 原始信任度（影响 weight）
    weight: float  # 检索权重（trust 越低 weight 越低）
    tags: list[str] = field(default_factory=list)
    retrieved_at: str = ""  # 本次 reconcile 拉取时间
    timestamp: str = (
        ""  # 对话发生时间（ISO 8601，extractor 能取到就填；空=未知，不过滤）
    )

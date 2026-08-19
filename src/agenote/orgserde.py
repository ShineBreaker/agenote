# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.orgserde — Org-mode 序列化/反序列化。

从 core.py 拆出（ADR-0003）：Org 属性解析（读）与 facts→Org 文档渲染（写）
归于一处。零 agenote 依赖（纯文本函数）；orgfmt 保持独立为交互式格式美化器。

- parse_org_prop / _parse_float_prop / _parse_int_prop / read_org_title：PROPERTIES 读取
- render_facts_org：ReconciledFact 列表 → Org 文档（原 run_extract 内联渲染）
"""

from __future__ import annotations

import re
from datetime import datetime

from agenote import config

# Org 渲染正文截断（config [extract].trunc_org_render 覆盖）
ORG_RENDER_TRUNC = int(config.get("extract", "trunc_org_render"))


# ═══════════════════════════════════════════════════════════════════════════════
# 读：PROPERTIES / 标题解析
# ═══════════════════════════════════════════════════════════════════════════════


def parse_org_prop(content: str, key: str) -> str:
    """从 Org 文件内容中提取 PROPERTIES 块中的指定属性值。"""
    m = re.search(rf":{key}:\s*(.+)", content)
    return m.group(1).strip() if m else ""


def _parse_float_prop(content: str, key: str, default: float) -> float:
    """从 PROPERTIES 解析浮点字段，缺失返回 default。"""
    raw = parse_org_prop(content, key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_int_prop(content: str, key: str, default: int) -> int:
    """从 PROPERTIES 解析整数字段，缺失返回 default。"""
    raw = parse_org_prop(content, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def read_org_title(content: str) -> str:
    """从 Org 内容中提取一级标题文本（去掉 DONE/TODO 前缀）。"""
    m = re.search(r"^\* (?:DONE|TODO) (.+)", content, re.MULTILINE)
    return m.group(1).strip() if m else "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# 写：facts → Org 文档
# ═══════════════════════════════════════════════════════════════════════════════


def render_facts_org(
    facts: list,
    *,
    source: str,
    date: str = "",
    limit: int | None = None,
) -> str:
    """把 ReconciledFact（鸭子类型）列表渲染为 Org 文档。

    原 extract/base.py run_extract 的内联渲染段；输出格式保持逐字节兼容：
    #+TITLE/#+DATE/#+SOURCE/#+TOTAL/#+FILTERED_BY_DATE/#+LIMIT 头 + 每条 fact
    一个一级条目（PROPERTIES + 截断 ORG_RENDER_TRUNC 字的正文）。
    """
    lines: list[str] = [
        f"#+TITLE: {source} conversations",
        f"#+DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"#+SOURCE: {source}",
        f"#+TOTAL: {len(facts)}",
        f"#+FILTERED_BY_DATE: {date or 'no'}",
        f"#+LIMIT: {limit if limit else 'unlimited'}",
        "",
    ]
    for f in facts:
        lines.append(f"* {f.title}")
        lines.append(":PROPERTIES:")
        lines.append(f":ID: {f.id}")
        lines.append(f":CATEGORY: {f.category}")
        lines.append(f":WEIGHT: {f.weight}")
        if f.timestamp:
            lines.append(f":TIMESTAMP: {f.timestamp}")
        lines.append(":END:")
        lines.append("")
        lines.append(f.content[:ORG_RENDER_TRUNC])
        lines.append("")
    return "\n".join(lines)

# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""hermes memory extractor (SQLite facts 表, FTS5)。

hermes 是**事实型源**（不走 pair_turns 配对）：每行 fact 直接映射为一条
ReconciledFact。其余 6 个源是对话型（user→assistant 配对）。

真实 schema: facts(fact_id, content, category, tags, trust_score,
retrieval_count, helpful_count, hrr_vector ...) + facts_fts(content, tags) FTS5。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from agenote import config
from agenote.extract import extract_title, resolve_xdg_path
from agenote.extract.base import register
from agenote.extract.models import RECONCILE_DEFAULT_WEIGHT, ReconciledFact

# 与其余 6 源统一：env HERMES_DB > config [extract.sources].hermes_db > 默认路径
HERMES_DB = resolve_xdg_path(
    "HERMES_DB", "~/.local/share/hermes/memory_store.db"
)

# hermes trust→weight 公式参数（config [weights] 覆盖）
_TRUST_BASE = float(config.get("weights", "default_trust"))
_WEIGHT_CAP = float(config.get("weights", "hermes_weight_cap"))

# hermes category → kb category 映射（目前 hermes 只有 general/project/tool）
_HERMES_CATEGORY_MAP = {
    "general": "general",
    "project": "project",
    "tool": "tool",
    "user": "reference",  # 预留：hermes 未来若有 user 类
    "feedback": "feedback",
}


def _open_hermes_ro(db_path: Path) -> sqlite3.Connection:
    """以只读方式打开 hermes memory_store.db。

    三重只读保护（任一失效都能挡住误写）：
    1. file: URI + mode=ro（SQLite 层面拒绝写）
    2. pragma query_only=1（连接层面拒绝 DML/DDL）
    3. 只 SELECT，从不构造写语句
    """
    if not db_path.exists():
        raise FileNotFoundError(f"hermes memory_store.db 不存在: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")  # 连接级写锁
    return conn


def _hermes_to_fact(row: sqlite3.Row) -> ReconciledFact:
    """把 hermes facts 一行转成 ReconciledFact。

    row 列：fact_id, content, category, tags, trust_score, retrieval_count,
            helpful_count（hrr_vector 不取，体积大且检索用不到）
    """
    fact_id = row["fact_id"]
    content = row["content"] or ""
    category = _HERMES_CATEGORY_MAP.get(row["category"] or "general", "general")
    trust = float(row["trust_score"] or _TRUST_BASE)
    # weight：trust 基准 → reconcile_default；trust 越高 weight 越高（封顶，不超过 KB 卡片）
    weight = round(min(_WEIGHT_CAP, RECONCILE_DEFAULT_WEIGHT + (trust - _TRUST_BASE)), 2)
    tags_raw = row["tags"] or ""
    tags = [t.strip() for t in re.split(r"[,，]", tags_raw) if t.strip()]
    return ReconciledFact(
        id=f"hermes:{fact_id}",
        source="hermes",
        native_id=str(fact_id),
        title=extract_title(content),
        category=category,
        content=content,
        trust_score=trust,
        weight=weight,
        tags=tags,
    )


@register("hermes")
def extract_hermes() -> tuple[list[ReconciledFact], list[str]]:
    """从 hermes memory_store.db 抽取全部 facts（只读）。

    返回 (facts, errors)。errors 是非致命问题（如某行解析失败）。
    """
    facts: list[ReconciledFact] = []
    errors: list[str] = []
    try:
        conn = _open_hermes_ro(HERMES_DB)
    except FileNotFoundError as e:
        # hermes 未运行/未初始化 → 返回空 + 一条说明（不算 fatal error）
        return [], [str(e)]
    try:
        rows = conn.execute(
            "SELECT fact_id, content, category, tags, trust_score, "
            "retrieval_count, helpful_count FROM facts ORDER BY fact_id"
        ).fetchall()
        for row in rows:
            try:
                facts.append(_hermes_to_fact(row))
            except Exception as e:  # 单行解析失败不中断整体
                errors.append(f"fact_id={row['fact_id']}: {e}")
    finally:
        conn.close()
    return facts, errors

# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
#
"""agenote.reconcile — 跨 agent memory 只读索引（reconcile）。

把其他 agent 的 memory **只读拉取**进 agenote 检索范围，让所有 agent 都能
搜到彼此的经验，但**绝不写回源文件**、**绝不污染人类权威 KB**。

设计参考 MiMoCode `memory/reconcile.ts` 的 cc_index 模式（只读 + 类型映射 +
不写回），适配到本机的真实数据源：

当前接入的 source（见 KNOWN_SOURCES）：
- hermes：`~/.local/share/hermes/memory_store.db`（holographic store）
  真实 schema：facts(fact_id, content, category, tags, trust_score, ...)
              + facts_fts(content, tags)  FTS5
  映射：content → 卡片正文（提取【...】括号标题）；category → kb category；
        trust_score → 影响检索 weight。

关键约束（抄 MiMoCode 设计意图）：
1. **只读**：sqlite3 用 `file:...?mode=ro` URI 打开 + `pragma query_only=1`
2. **不破坏隔离**：reconcile 的事实进**单独的** `.reconcile/index.json`，
   不写 `experiences/`；agenote_search 把它作为额外检索目标（带 source 标记）
3. **冲突时 KB 优先**：KB 已有同标题卡片则 source 端跳过（不计入 indexed）
4. **低 weight**：reconcile 卡片默认 weight 低于 KB 卡片，避免淹没权威经验
"""

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from agenote.core import AGENOTE_ROOT, is_noise_fact
from agenote.extract.models import RECONCILE_DEFAULT_WEIGHT, ReconciledFact
from agenote import config

# ═══════════════════════════════════════════════════════════════════════════════
# reconcile 索引落盘位置（与 experiences/ 平级，独立目录，绝不混入权威 KB）
# ═══════════════════════════════════════════════════════════════════════════════

_reconcile_cfg = str(config.get("paths", "reconcile_dir"))
RECONCILE_DIR = (
    config.get_path("paths", "reconcile_dir") if _reconcile_cfg else AGENOTE_ROOT / ".reconcile"
)
RECONCILE_INDEX = RECONCILE_DIR / "index.json"
REPORT_ITEMS = int(config.get("reconcile", "report_items"))  # 报告摘要条数
REPORT_ITEMS_ALL = int(config.get("reconcile", "report_items_all"))  # --all 合并报告每源条数

# reconcile 来源卡片默认权重（低于 KB 卡片 1.0/1.5，避免淹没权威经验）
# 定义已迁至 agenote.extract.models（adapter/framework/reconcile 三方共享）


# ═══════════════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class ReconcileReport:
    """单次 reconcile 运行报告（对齐 MiMoCode {indexed, pruned} 结构）。"""

    source: str
    indexed: int = 0  # 新增/更新的条目数
    skipped: int = 0  # 因 KB 已有同标题而跳过的条目数
    pruned: int = 0  # 源已删除、本次清理掉的陈旧索引项
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
    indexed_items: list[dict] = field(default_factory=list)  # 前 N 条摘要

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# hermes 抽取器 — 已迁至 agenote/extract/hermes.py（@register 注册进 SOURCES）
# ═══════════════════════════════════════════════════════════════════════════════

from agenote.extract.hermes import HERMES_DB, extract_hermes  # noqa: F401  (re-export)


# ═══════════════════════════════════════════════════════════════════════════════
# source 分发 — 统一由 agenote.extract.base.SOURCES registry 提供
# ═══════════════════════════════════════════════════════════════════════════════
# 原 KNOWN_SOURCES（7 项 lambda __import__ 分发）已删除：新增 source 只需在
# agenote/extract/<name>.py 写 @register 装饰的 adapter，extract/reconcile/
# dream trace 三条路径自动可用。三重只读保护由 open_sqlite_ro() 保证。


def _known_extractors() -> dict:
    """source → extract callable（从 SOURCES registry 派生，注册即生效）。"""
    from agenote.extract.base import _resolve_extractors

    return _resolve_extractors()


# ═══════════════════════════════════════════════════════════════════════════════
# reconcile 主流程
# ═══════════════════════════════════════════════════════════════════════════════


def _load_reconcile_index() -> dict:
    """加载 .reconcile/index.json，失败返回空骨架。"""
    if RECONCILE_INDEX.exists():
        try:
            return json.loads(RECONCILE_INDEX.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"version": 1, "updated": "", "by_source": {}, "facts": []}


def _save_reconcile_index(index: dict) -> None:
    """写入 .reconcile/index.json。"""
    RECONCILE_DIR.mkdir(parents=True, exist_ok=True)
    index["updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    index["facts"] = sorted(index["facts"], key=lambda f: f["id"])
    by_source: dict[str, int] = {}
    for f in index["facts"]:
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1
    index["by_source"] = dict(sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0])))
    RECONCILE_INDEX.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _kb_titles() -> set[str]:
    """收集 KB（agenote experiences/）已有卡片标题，用于冲突跳过。

    KB 优先原则：reconcile 抽到的事实若与 KB 卡片同名，跳过不索引。
    """
    titles: set[str] = set()
    exp = AGENOTE_ROOT / "experiences"
    if not exp.exists():
        return titles
    for f in exp.rglob("*.org"):
        if f.is_symlink():
            continue
        try:
            txt = f.read_text(encoding="utf-8")
        except OSError:
            continue
        # org 标题行：* DONE <title>
        m = re.search(r"^\* (?:DONE|TODO) (.+)$", txt, re.MULTILINE)
        if m:
            titles.add(m.group(1).strip().casefold())
    return titles


def reconcile_source(source: str = "hermes", dry_run: bool = False) -> ReconcileReport:
    """对单个 source 跑一次只读 reconcile。

    Args:
        source: SOURCES registry 中的 source 名（或 "all" 跑全部）
        dry_run: True 只返回报告不落盘（首次/审核场景）

    Returns:
        ReconcileReport（含 indexed/skipped/pruned/errors）
    """
    if source == "all":
        return reconcile_all(dry_run=dry_run)
    extractors = _known_extractors()
    if source not in extractors:
        raise ValueError(f"未知 source: {source}；已注册: {sorted(extractors)}")

    extractor = extractors[source]
    facts, extract_errors = extractor()

    report = ReconcileReport(source=source)
    report.error_details.extend(extract_errors)
    report.errors = len(extract_errors)

    # 0-fact 警告：extractor 跑通但抽不到任何事实（数据未生成 / schema 漂移）
    if not facts and not extract_errors:
        report.error_details.append(
            f"[warn] {source} 抽取到 0 facts（数据未生成或 schema 漂移）"
        )

    # Dedup：跨 DB 重复（如 crush 全局 + 项目级，或 bind-mount 同源）
    # 按 id 去重，保留先出现的（数据库读取顺序由 extractor 决定）
    seen_ids: set[str] = set()
    deduped: list = []
    dup_count = 0
    for f in facts:
        if f.id in seen_ids:
            dup_count += 1
            continue
        seen_ids.add(f.id)
        deduped.append(f)
    facts = deduped
    if dup_count:
        report.error_details.append(f"[info] {source} 去重跳过 {dup_count} 条重复")

    # KB 优先：跳过与 KB 已有卡片同标题的事实
    kb_titles = _kb_titles()
    kept = [f for f in facts if f.title.casefold() not in kb_titles]
    report.skipped = len(facts) - len(kept)

    # 噪声过滤（元消息/工具提示）：extractor 抽取一切，reconcile 是策展层负责过滤
    noise = [f for f in kept if is_noise_fact(asdict(f))]
    kept = [f for f in kept if not is_noise_fact(asdict(f))]
    if noise:
        report.error_details.append(f"[info] {source} 过滤 {len(noise)} 条元消息噪声")

    # 加载现有 reconcile 索引，剔除该 source 的旧条目（重新填），保留其他 source
    old_index = _load_reconcile_index()
    pruned_old = [f for f in old_index.get("facts", []) if f.get("source") != source]
    report.pruned = sum(
        1 for f in old_index.get("facts", []) if f.get("source") == source
    )

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    new_entries = []
    for f in kept:
        f.retrieved_at = now
        new_entries.append(asdict(f))
    report.indexed = len(new_entries)
    report.indexed_items = [
        {"id": e["id"], "title": e["title"], "category": e["category"]}
        for e in new_entries[:REPORT_ITEMS]  # 报告里只放前 N 条摘要
    ]

    if not dry_run:
        merged = {
            "version": 1,
            "updated": "",
            "by_source": {},
            "facts": pruned_old + new_entries,
        }
        _save_reconcile_index(merged)

    return report


def reconcile_all(dry_run: bool = False) -> ReconcileReport:
    """对所有已注册 source 跑 reconcile，返回合并报告。

    source 字段为 "all"，indexed/skipped/pruned/errors 是各 source 之和，
    indexed_items 是各 source 前 5 条的合并摘要。
    """
    merged = ReconcileReport(source="all")
    for src in _known_extractors():
        sub = reconcile_source(src, dry_run=dry_run)
        merged.indexed += sub.indexed
        merged.skipped += sub.skipped
        merged.pruned += sub.pruned
        merged.errors += sub.errors
        merged.error_details.extend(sub.error_details)
        merged.indexed_items.extend(sub.indexed_items[:REPORT_ITEMS_ALL])
    return merged


def load_reconcile_facts() -> list[dict]:
    """供 agenote_search 调用：返回当前 reconcile 索引里的全部事实。

    search 层把这些事实作为额外检索目标（带 source=hermes 标记），
    权重用 fact 自带的 weight（低于 KB 卡片）。
    """
    idx = _load_reconcile_index()
    return idx.get("facts", [])


# ── trace 溯源（dream 候选 → 回查原始完整对话）─────────────────
# fact_id 三段式："{source}:{session_id}:{msg_id}"（opencode/zcode/omp/claude 等）
# 或两段式："{source}:{native_id}"（hermes/crush 等）。trace 从中拆出 source +
# session_id，按 source 分发到对应 extractor 的 trace_session（不截断回查原始 DB）。
# 未实现 trace_session 的 source 优雅降级：返回索引层 content（截断摘要）+ 说明。


def trace_fact(fact_id: str) -> dict:
    """从 fact_id 回查原始完整对话（dream trace 溯源入口）。

    fact_id 来自 DreamCandidate.source_trace（= reconcile fact 的 id）。
    解析三段式拆出 source + session_id，按 source 分发：
      - opencode/zcode：trace_session 查 SQLite（完整 message+part，不截断）
      - omp：trace_session 读 .jsonl（完整 parentId 树，不截断）
      - 其余（hermes/crush/codex/claude）：暂未实现 trace_session，降级返回
        索引层 content（截断摘要）+ 降级说明

    返回 dict（含 source/session_id/session 元信息 + messages 列表）。
    出错时返回 {"error": ..., "fact_id": ...}。
    """
    parts = fact_id.split(":", 2)
    if len(parts) < 2:
        return {"error": f"fact_id 格式无法解析: {fact_id}", "fact_id": fact_id}
    source = parts[0]
    session_id = parts[1] if len(parts) >= 2 else ""

    # trace 能力分发：Source.trace 由各 adapter 模块注册（opencode/zcode/omp）
    _known_extractors()  # 确保 adapter 模块已 import（@register 已触发）
    from agenote.extract.base import SOURCES

    src_entry = SOURCES.get(source)
    if src_entry is not None and src_entry.trace is not None:
        try:
            result = src_entry.trace(session_id)
            result.setdefault("fact_id", fact_id)
            return result
        except Exception as e:
            return {
                "error": f"trace {source}/{session_id} 失败: {e}",
                "fact_id": fact_id,
            }

    # 未实现 trace_session 的 source：降级返回索引层 content
    idx = _load_reconcile_index()
    for f in idx.get("facts", []):
        if f.get("id") == fact_id:
            return {
                "source": source,
                "fact_id": fact_id,
                "degraded": True,
                "message": (
                    f"{source} 的 trace_session 尚未实现，返回索引层摘要（已截断）。"
                    f"该 content 由 extractor 在建索引时截断，不含完整工具调用/推理。"
                ),
                "content": f.get("content", ""),
                "title": f.get("title", ""),
            }
    return {"error": f"fact_id {fact_id} 在 reconcile 索引中未找到", "fact_id": fact_id}

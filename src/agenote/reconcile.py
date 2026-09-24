# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
#
"""agenote.reconcile — 跨 agent memory 只读索引（reconcile）。

把其他 agent 的 memory **只读拉取**进 agenote 检索范围，让所有 agent 都能
搜到彼此的经验，但**绝不写回源文件**、**绝不污染人类权威 KB**。

设计参考 MiMoCode `memory/reconcile.ts` 的 cc_index 模式（只读 + 类型映射 +
不写回），适配到本机的真实数据源：

当前接入的 source 由 `extract.base.SOURCES` 注册表派生；每个 source 只提供自己的
抽取适配器，reconcile 负责去重、噪声过滤、KB 优先和低权重索引。

关键约束（抄 MiMoCode 设计意图）：
1. **只读**：sqlite3 用 `file:...?mode=ro` URI 打开 + `pragma query_only=1`
2. **不破坏隔离**：reconcile 的事实进**单独的** `.reconcile/index.json`，
   不写 `experiences/`；agenote_search 把它作为额外检索目标（带 source 标记）
3. **冲突时 KB 优先**：KB 已有同标题卡片则 source 端跳过（不计入 indexed）
4. **低 weight**：reconcile 卡片默认 weight 低于 KB 卡片，避免淹没权威经验
"""

import json
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from agenote.core import (
    AGENOTE_ROOT,
    PublicError,
    ReconcileIndexError,
    is_noise_fact,
    safe_error_message,
)
from agenote.extract.base import (
    AdapterMessage,
    AdapterSkip,
    UnknownSourceError,
    safe_adapter_error,
)
from agenote.extract.models import RECONCILE_DEFAULT_WEIGHT, ReconciledFact
from agenote import config
from agenote.safeio import atomic_write

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
    orphaned: int = 0  # S5：本轮抽空、标 orphan 保留的旧事实数
    errors: int = 0
    error_details: list[str] = field(default_factory=list)
    indexed_items: list[dict] = field(default_factory=list)  # 前 N 条摘要

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════════════════════
# source 分发 — 统一由 agenote.extract.base.SOURCES registry 提供
# ═══════════════════════════════════════════════════════════════════════════════
# 原 KNOWN_SOURCES lambda 分发已删除：新增 source 只需在
# agenote/extract/<name>.py 写 @register 装饰的 adapter，extract/reconcile/
# dream trace 三条路径自动可用。三重只读保护由 open_sqlite_ro() 保证。


def _known_extractors() -> dict:
    """source → extract callable（从 SOURCES registry 派生，注册即生效）。"""
    from agenote.extract.base import _resolve_extractors

    return _resolve_extractors()


# ═══════════════════════════════════════════════════════════════════════════════
# reconcile 主流程
# ═══════════════════════════════════════════════════════════════════════════════


def _valid_reconcile_fact(fact: object) -> bool:
    """返回事实元素是否满足 reconcile 读取与写回所需的完整结构。"""
    if not isinstance(fact, dict):
        return False
    string_fields = (
        "id",
        "source",
        "native_id",
        "title",
        "category",
        "content",
        "retrieved_at",
        "timestamp",
    )
    if not all(isinstance(fact.get(key), str) for key in string_fields):
        return False
    if not isinstance(fact.get("tags"), list) or not all(
        isinstance(tag, str) for tag in fact["tags"]
    ):
        return False
    if not all(
        isinstance(fact.get(key), (int, float))
        and not isinstance(fact[key], bool)
        and math.isfinite(fact[key])
        for key in ("trust_score", "weight")
    ):
        return False
    # id 必须由 source 前缀派生（trace 依赖它分发），拒绝跨字段不一致。
    return fact["id"] == fact["source"] or fact["id"].startswith(fact["source"] + ":")


def _empty_reconcile_index() -> dict:
    return {"version": 1, "updated": "", "by_source": {}, "facts": [], "meta": {}}


def _load_reconcile_index() -> dict:
    """加载 .reconcile/index.json；已有文件损坏或事实结构非法时 fail-closed。"""
    if not RECONCILE_INDEX.exists():
        return _empty_reconcile_index()
    try:
        index = json.loads(RECONCILE_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise ReconcileIndexError(f"reconcile 索引损坏: {RECONCILE_INDEX}") from exc
    if not isinstance(index, dict) or not isinstance(index.get("facts"), list):
        raise ReconcileIndexError(f"reconcile 索引结构非法: {RECONCILE_INDEX}")
    if not all(_valid_reconcile_fact(fact) for fact in index["facts"]):
        raise ReconcileIndexError(
            f"reconcile 索引事实结构非法: {RECONCILE_INDEX}"
        )
    # 顶层字段同样 fail-closed；旧格式缺失时按默认值容忍，类型错误必须拒绝。
    if (
        not isinstance(index.get("version", 1), int)
        or isinstance(index.get("version", 1), bool)
        or not isinstance(index.get("updated", ""), str)
        or not isinstance(index.get("by_source", {}), dict)
    ):
        raise ReconcileIndexError(f"reconcile 索引结构非法: {RECONCILE_INDEX}")
    # S5 水位节：旧索引缺失回空 dict；类型错误 fail-closed。
    if "meta" not in index:
        index["meta"] = {}
    elif not isinstance(index["meta"], dict) or not all(
        isinstance(v, dict) for v in index["meta"].values()
    ):
        raise ReconcileIndexError(f"reconcile 索引 meta 非法: {RECONCILE_INDEX}")
    return index


def _save_reconcile_index(index: dict) -> None:
    """写入 .reconcile/index.json；非法事实拒绝落盘，避免污染 LKG。"""
    facts = index.get("facts")
    if not isinstance(facts, list) or not all(
        _valid_reconcile_fact(fact) for fact in facts
    ):
        raise ReconcileIndexError("reconcile 索引事实结构非法（拒绝写入）")
    RECONCILE_DIR.mkdir(parents=True, exist_ok=True)
    index["updated"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    index["facts"] = sorted(index["facts"], key=lambda f: f["id"])
    by_source: dict[str, int] = {}
    for f in index["facts"]:
        by_source[f["source"]] = by_source.get(f["source"], 0) + 1
    index["by_source"] = dict(sorted(by_source.items(), key=lambda kv: (-kv[1], kv[0])))
    meta = index.get("meta", {})
    if not isinstance(meta, dict):
        raise ReconcileIndexError("reconcile 索引 meta 非法（拒绝写入）")
    index["meta"] = meta
    atomic_write(
        RECONCILE_INDEX,
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
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
        # org 标题行：* DONE <title>；首行 UTF-8 BOM 会让 ^\* 失配（对齐
        # orgserde._heading_probe 口径：仅匹配时剥离首字符 BOM，不改写原文）。
        probe = txt[1:] if txt.startswith("\ufeff") else txt
        m = re.search(r"^\* (?:DONE|TODO) (.+)$", probe, re.MULTILINE)
        if m:
            titles.add(m.group(1).strip().casefold())
    return titles


def _reconcile_source(
    source: str,
    extractor,
    old_facts: list[dict],
    *,
    old_meta: dict | None = None,
    prune_orphans: bool = False,
) -> tuple[ReconcileReport, list[dict], dict]:
    """计算单个 source 的结果，但不写入索引。

    返回 (report, entries, src_meta)：entries 是本源应进新索引的事实
    （新鲜抽取 + orphan 保留），src_meta 是本源的新水位。
    """
    try:
        facts, extract_errors = extractor()
    except ValueError:
        # schema/信任边界错误必须传播到 CLI；不能伪装成可恢复的 source 报告。
        raise
    except Exception as exc:
        # 普通 adapter 异常属于 source 级失败：保留报告并阻止索引写入，
        # 由 reconcile_all 汇总为整体失败，而不是向交互终端泄漏 traceback。
        facts, extract_errors = [], [
            AdapterMessage(f"{source}: {safe_error_message(exc)}")
        ]

    report = ReconcileReport(source=source)
    # skip（源未安装/数据不存在）是部分安装机器上的预期状态：进报告但
    # 不计入 errors，不阻塞整批落盘；只有真实错误才计入。
    skip_details = [
        f"[skip] {safe_adapter_error(error, source=source)}"
        for error in extract_errors
        if isinstance(error, AdapterSkip)
    ]
    hard_errors = [
        error for error in extract_errors if not isinstance(error, AdapterSkip)
    ]
    report.error_details.extend(skip_details)
    report.error_details.extend(
        safe_adapter_error(error, source=source) for error in hard_errors
    )
    report.errors = len(hard_errors)

    # S5 判定用原始抽取是否为空（KB 跳过/噪声过滤不算“源空”）。
    raw_empty = not facts

    # 0-fact 提示：extractor 跑通但抽不到任何事实（数据未生成 / 已清空 / schema 漂移）。
    # 空结果不再计为失败；落盘时走下方的 orphan 语义（保留标 orphan 而非静默清空）。
    if not facts and not extract_errors:
        report.error_details.append(
            f"[info] {source} 抽取到 0 facts（数据未生成或源已清空）"
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

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    new_entries = []
    for f in kept:
        f.retrieved_at = now
        new_entries.append(asdict(f))
    report.indexed = len(new_entries)
    report.indexed_items = [
        {"id": e["id"], "title": e["title"], "category": e["category"]}
        for e in new_entries[:REPORT_ITEMS]
    ]

    # S5 水位 + orphan：本轮抽空（raw_empty）且上轮有数 → 保留旧事实并标
    # orphan:true + orphan_detected_at，不静默清空。上轮数取 meta 水位，
    # 缺 meta 的旧索引回落到旧事实计数。已标 orphan 的继续保留（持久保留，
    # 只由 --prune-orphans 显式清理），orphan_detected_at 保留首次检测时间。
    old_src = [f for f in old_facts if f.get("source") == source]
    prev_count = len(old_src)
    prev_meta = (old_meta or {}).get(source)
    if isinstance(prev_meta, dict):
        try:
            prev_count = int(prev_meta.get("last_fact_count", prev_count))
        except (TypeError, ValueError):
            pass
    carried: list[dict] = []
    if not prune_orphans and raw_empty and report.errors == 0 and prev_count > 0:
        for f in old_src:
            g = dict(f)
            g["orphan"] = True
            g.setdefault("orphan_detected_at", now)
            carried.append(g)
    report.orphaned = len(carried)
    report.pruned = len(old_src) - len(carried)
    src_meta = {
        "last_success_at": now,
        "last_fact_count": len(new_entries) + len(carried),
    }
    return report, new_entries + carried, src_meta


def reconcile_source(
    source: str = "all", dry_run: bool = False, prune_orphans: bool = False
) -> ReconcileReport:
    """对单个 source 跑一次只读 reconcile。

    Args:
        source: SOURCES registry 中的 source 名（或 "all" 跑全部）
        dry_run: True 只返回报告不落盘（首次/审核场景）
        prune_orphans: True 则显式清理本源的 orphan 旧事实（单源模式只清本源）

    Returns:
        ReconcileReport（含 indexed/skipped/pruned/orphaned/errors）
    """
    if source == "all":
        return reconcile_all(dry_run=dry_run, prune_orphans=prune_orphans)
    extractors = _known_extractors()
    if source not in extractors:
        raise UnknownSourceError(
            f"未知 source: {source}；已注册: {sorted(extractors)}"
        )

    # dry-run 仍会读取既有 LKG 以计算替换/清理范围；损坏索引必须显式失败，
    # 不能因为“不落盘”就静默降级为空库。
    old_index = _load_reconcile_index()
    old_facts = old_index.get("facts", [])
    old_meta = old_index.get("meta", {})
    report, entries, src_meta = _reconcile_source(
        source, extractors[source], old_facts,
        old_meta=old_meta, prune_orphans=prune_orphans,
    )
    if not dry_run and report.errors == 0:
        new_meta = dict(old_meta)
        new_meta[source] = src_meta
        merged = {
            "version": 1,
            "updated": "",
            "by_source": {},
            "facts": [f for f in old_facts if f.get("source") != source] + entries,
            "meta": new_meta,
        }
        _save_reconcile_index(merged)
    return report


def reconcile_all(dry_run: bool = False, prune_orphans: bool = False) -> ReconcileReport:
    """对所有已注册 source 跑 reconcile，返回合并报告。

    source 字段为 "all"，indexed/skipped/pruned/errors 是各 source 之和，
    indexed_items 是各 source 前 5 条的合并摘要。非 dry-run 会先删除索引里
    已不在 registry 的历史 source，避免退役 adapter 的旧事实继续被 search/dream 消费。
    所有 source 计算成功后才一次性落盘，避免部分失败留下半更新索引。
    prune_orphans 为 True 时清理全部 orphan 标记的旧事实。
    """
    extractors = _known_extractors()
    merged = ReconcileReport(source="all")

    # dry-run 仍会读取既有 LKG 以计算替换/清理范围；损坏索引必须显式失败，
    # 不能因为“不落盘”就静默降级为空库。
    old_index = _load_reconcile_index()
    old_facts = old_index.get("facts", [])
    old_meta = old_index.get("meta", {})
    unregistered_facts = [
        f for f in old_facts if f.get("source") not in extractors
    ]
    merged.pruned = len(unregistered_facts)
    if unregistered_facts:
        sources = sorted({str(f.get("source", "")) for f in unregistered_facts})
        merged.error_details.append(f"[info] 清理未注册 source: {', '.join(sources)}")

    new_facts: list[dict] = []
    new_meta: dict = {}
    for src, extractor in extractors.items():
        sub, entries, src_meta = _reconcile_source(
            src, extractor, old_facts,
            old_meta=old_meta, prune_orphans=prune_orphans,
        )
        new_meta[src] = src_meta
        new_facts.extend(entries)
        merged.indexed += sub.indexed
        merged.skipped += sub.skipped
        merged.pruned += sub.pruned
        merged.orphaned += sub.orphaned
        merged.errors += sub.errors
        merged.error_details.extend(sub.error_details)
        merged.indexed_items.extend(sub.indexed_items[:REPORT_ITEMS_ALL])

    if not dry_run and merged.errors == 0:
        _save_reconcile_index(
            {
                "version": 1,
                "updated": "",
                "by_source": {},
                "facts": new_facts,
                "meta": new_meta,
            }
        )
    return merged


def load_reconcile_facts() -> list[dict]:
    """供 agenote_search 调用：返回当前 reconcile 索引里的全部事实。

    search 层把这些事实作为额外检索目标（带 source 标记），
    权重用 fact 自带的 weight（低于 KB 卡片）。
    """
    idx = _load_reconcile_index()
    return idx.get("facts", [])


# ── trace 溯源（dream 候选 → 回查原始完整对话）─────────────────
# fact_id 三段式："{source}:{session_id}:{msg_id}"（opencode/zcode/omp/claude 等）
# 或两段式：f"{source}:{native_id}"（crush 等）。trace 从中拆出 source +
# session_id，按 source 分发到对应 extractor 的 trace_session（不截断回查原始 DB）。
# 未实现 trace_session 的 source 优雅降级：返回索引层 content（截断摘要）+ 说明。


def trace_fact(fact_id: str) -> dict:
    """从 fact_id 回查原始完整对话（dream trace 溯源入口）。

    fact_id 来自 DreamCandidate.source_trace（= reconcile fact 的 id）。
    解析三段式拆出 source + session_id，按 source 分发：
      - opencode/zcode：trace_session 查 SQLite（完整 message+part，不截断）
      - omp：trace_session 读 .jsonl（完整 parentId 树，不截断）
      - 其余（crush/codex/claude）：暂未实现 trace_session，降级返回
        索引层 content（截断摘要）+ 降级说明

    返回 dict（含 source/session_id/session 元信息 + messages 列表）。
    出错时返回 {"error": ..., "fact_id": ...}。
    """
    parts = fact_id.split(":", 2)
    if len(parts) < 2:
        return {
            "error": AdapterMessage(f"fact_id 格式无法解析: {fact_id}"),
            "fact_id": fact_id,
        }
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
        except Exception as exc:
            return {
                "error": AdapterMessage(
                    f"trace {source}/{session_id} 失败（{type(exc).__name__}）"
                ),
                "fact_id": fact_id,
            }

    # 未实现 trace_session 的 source：降级返回索引层 content；索引损坏时
    # 与其它公共读路径一致 fail-closed，不返回“未找到”来掩盖结构错误。
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
    return {
        "error": AdapterMessage(f"fact_id {fact_id} 在 reconcile 索引中未找到"),
        "fact_id": fact_id,
    }

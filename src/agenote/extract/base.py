# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.extract.base — cross-agent extraction framework.

框架职责（adapter 无需再实现）：
  - SQLite session/message/part → Turn 的通用解析（iter_turns_sqlite）
  - user→assistant 配对状态机 + ReconciledFact 构造（pair_turns）
  - 打开 DB / 遍历 session / 收集 errors 的通用壳（run_sqlite_extractor）
  - source 注册中心（SOURCES / register）与跨 agent 编排（run_extract）

每个 source 的 adapter 只需提供差异部分：
  - db 路径、source 名、weight
  - categorize(session) 启发式（zcode 比 opencode 多一个 "config" 类别）
  - （可选）trace_session

ReconciledFact 仍从 agenote.reconcile 导入；迁到 extract/models.py 是后续阶段的事。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

# ═══════════════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Turn:
    """抽取器处理的最小单位 — user 或 assistant 的一回合（见 CONTEXT.md "turn"）。"""

    role: str  # "user" | "assistant"
    text: str  # 合并后的正文（parts 解析 + 格式化）
    timestamp: str  # 原始时间戳（ISO 8601 / epoch），空串 = 未知
    native_id: str  # 源系统 message id
    session: dict[str, Any]  # 当前 session 元数据（id/title/directory）


# ═══════════════════════════════════════════════════════════════════════════════
# framework 核心：配对状态机 + ReconciledFact 构造
# ═══════════════════════════════════════════════════════════════════════════════


def pair_turns(
    turns: Iterator[Turn],
    *,
    source: str,
    weight: float,
    categorize: Callable[[dict[str, Any]], str],
) -> Iterator[ReconciledFact]:
    """配对状态机：user 回合累积，assistant 回合到达时配对产出一条 ReconciledFact。

    配对逻辑 7 个源里有 6 个完全相同，故由 framework 拥有，消除 ~60 处 current_user 重复。
    事实型源（hermes）不走此函数，直接产出。
    """
    # lazy：避免 import agenote.extract 时拉 reconcile 链（保持 __init__.py 的懒加载设计）
    from agenote.extract import extract_title
    from agenote.reconcile import ReconciledFact

    current_user: Turn | None = None
    for turn in turns:
        if turn.role == "user":
            current_user = turn
        elif turn.role == "assistant" and current_user:
            sess_title = turn.session.get("title") or "Untitled"
            directory = turn.session.get("directory") or ""
            yield ReconciledFact(
                id=f"{source}:{turn.session['id']}:{turn.native_id}",
                source=source,
                native_id=turn.native_id,
                title=extract_title(current_user.text) or sess_title[:80],
                category=categorize(turn.session),
                content=f"USER: {current_user.text[:1000]}\n\nASSISTANT: {turn.text[:2000]}",
                trust_score=0.5,
                weight=weight,
                tags=[directory.split("/")[-1] if directory else "unknown"],
                timestamp=current_user.timestamp,
            )
            current_user = None


# ═══════════════════════════════════════════════════════════════════════════════
# framework 核心：SQLite session/message/part → Turn（opencode/zcode 共享）
# ═══════════════════════════════════════════════════════════════════════════════


def iter_turns_sqlite(conn: Any, session_row: dict[str, Any]) -> Iterator[Turn]:
    """通用 SQLite session/message/part → Turn。

    opencode 与 zcode 共享同一套 schema（session/message/part，part.data.type =
    text|reasoning|tool|patch），故此函数在双胞胎之间一字不差共享。
    """
    messages = conn.execute(
        "SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created",
        (session_row["id"],),
    ).fetchall()
    for msg in messages:
        try:
            md = json.loads(msg["data"])
        except (json.JSONDecodeError, TypeError):
            continue
        role = md.get("role", "")
        parts = conn.execute(
            "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
            (msg["id"],),
        ).fetchall()
        texts: list[str] = []
        for p in parts:
            try:
                pd = json.loads(p["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            ptype = pd.get("type", "")
            # 与原 opencode/zcode 行为一致：user 消息只取 text 部分，
            # assistant 消息格式化全部 4 种类型（text/reasoning/tool/patch）。
            if ptype == "text":
                texts.append(pd.get("text", ""))
            elif role == "assistant":
                if ptype == "reasoning":
                    texts.append(f"[reasoning] {pd.get('text', '')[:200]}")
                elif ptype == "tool":
                    tool_name = pd.get("tool", "?")
                    texts.append(f"[tool: {tool_name}]")
                elif ptype == "patch":
                    files = pd.get("files", [])
                    texts.append(f"[patch: {len(files)} files]")
        content = "\n\n".join(t for t in texts if t).strip()
        if content:
            yield Turn(
                role=role,
                text=content,
                timestamp=str(msg["time_created"] or ""),
                native_id=msg["id"],
                session={
                    "id": session_row["id"],
                    "title": session_row["title"],
                    "directory": session_row["directory"],
                },
            )


# ═══════════════════════════════════════════════════════════════════════════════
# framework 通用 SQLite 抽取壳：open → iterate → pair → (facts, errors)
# ═══════════════════════════════════════════════════════════════════════════════


def run_sqlite_extractor(
    db_path: Path,
    *,
    source: str,
    weight: float,
    categorize: Callable[[dict[str, Any]], str],
) -> tuple[list[ReconciledFact], list[str]]:
    """通用 SQLite 抽取：打开 DB → 遍历 session → pair_turns → (facts, errors)。

    把 open_sqlite_ro / session 查询 / try-except-finally / 错误收集这些在 4 个
    SQLite 源里重复的壳收敛到一处。
    """
    from agenote.extract import open_sqlite_ro  # lazy：避免包初始化时拉 sqlite 链

    facts: list[ReconciledFact] = []
    errors: list[str] = []
    try:
        conn = open_sqlite_ro(db_path)
    except FileNotFoundError as e:
        return [], [str(e)]
    try:
        sessions = conn.execute(
            "SELECT id, title, directory, time_created, time_updated "
            "FROM session ORDER BY time_created"
        ).fetchall()
        for sess in sessions:
            try:
                facts.extend(
                    pair_turns(
                        iter_turns_sqlite(conn, sess),
                        source=source,
                        weight=weight,
                        categorize=categorize,
                    )
                )
            except Exception as e:  # 单个 session 失败不中断整源
                errors.append(f"session={sess['id']}: {e}")
    finally:
        conn.close()
    return facts, errors


# ═══════════════════════════════════════════════════════════════════════════════
# source 注册中心 + 跨 agent 编排
# ═══════════════════════════════════════════════════════════════════════════════


@dataclass
class Source:
    """注册到 SOURCES 的一个抽取源。

    extract: () -> (facts, errors)
    trace:   (session_id) -> dict（可选；有 trace_session 能力的源提供）
    """

    name: str
    extract: Callable[[], tuple[list[ReconciledFact], list[str]]]
    trace: Callable[[str], dict] | None = None


SOURCES: dict[str, Source] = {}


def register(name: str, *, trace: Callable[[str], dict] | None = None) -> Callable:
    """装饰器：把 extract_X() 注册到 SOURCES。

    用法::

        @register("opencode")
        def extract_opencode() -> tuple[list[ReconciledFact], list[str]]:
            ...
    """

    def deco(fn: Callable) -> Callable:
        SOURCES[name] = Source(name=name, extract=fn, trace=trace)
        return fn

    return deco


def _resolve_extractors() -> dict[str, Callable]:
    """返回 source → extract callable 的映射。

    优先从 SOURCES registry 读取（已迁移的 adapter），剩余走 legacy 导入
    （尚未迁移的 crush/codex/claude 与 reconcile.extract_hermes）。
    随着每个源迁移进 SOURCES，legacy 分支自然收缩，最终可删除。
    """
    from agenote.reconcile import extract_hermes

    # 显式 import 带 @register 的 adapter，触发注册到 SOURCES
    from agenote.extract import omp, opencode, zcode  # noqa: F401

    extractors: dict[str, Callable] = {name: src.extract for name, src in SOURCES.items()}
    registered = set(SOURCES)

    # legacy 适配器（尚未迁移进 SOURCES）
    from agenote.extract import claude, codex, crush

    for mod, key in [(crush, "crush"), (codex, "codex"), (claude, "claude")]:
        if key not in registered:
            extractors[key] = getattr(mod, f"extract_{key}")

    extractors["hermes"] = extract_hermes
    return extractors


def run_extract(
    source: str = "all",
    date: str = "",
    output_dir: str = "",
    dry_run: bool = False,
    limit: int = 500,
) -> dict:
    """跨 agent 对话抽取编排：把多个 AI 工具的原始对话抽取为 Org-mode 文件。

    与 reconcile_source 的区别：
      - reconcile_source：抽取已沉淀的经验（agent memory store），写 .reconcile/index.json
      - run_extract：抽取原始对话（DB/JSONL），输出 Org 文件供人/agent 提炼新经验
    """
    extractors = _resolve_extractors()

    if source == "all":
        selected = list(extractors.keys())
    elif source in extractors:
        selected = [source]
    else:
        return {"error": f"未知 source: {source}；可选: {sorted(extractors)}"}

    # 输出目录
    if not output_dir:
        target_date = date or (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        output_dir = f"~/Documents/Org/conversations/{target_date}"
    out_path = Path(output_dir).expanduser()
    if not dry_run:
        out_path.mkdir(parents=True, exist_ok=True)

    # 日期过滤：把 ISO 时间戳归一到 YYYY-MM-DD 比对。
    # extractor 未填 timestamp 的事实（timestamp=""）不过滤，避免静默丢数据。
    def _date_of(ts: str) -> str:
        """把 ISO 8601 / epoch ms 时间戳归一成 YYYY-MM-DD；无法解析返回空串。"""
        if not ts:
            return ""
        s = ts.strip()
        if "T" in s or " " in s and any(c.isdigit() for c in s[:4]):
            return s[:10]
        if s.isdigit():
            n = int(s)
            if n > 1e12:  # ms
                n //= 1000
            try:
                return datetime.utcfromtimestamp(n).strftime("%Y-%m-%d")
            except (OSError, ValueError, OverflowError):
                return ""
        return ""

    files: list[str] = []
    errors: list[str] = []
    total = 0
    filtered_total = 0
    effective_limit = limit if limit and limit > 0 else None
    for src in selected:
        try:
            facts, errs = extractors[src]()
            total += len(facts)
            errors.extend(errs)
            if date:
                facts = [f for f in facts if not f.timestamp or _date_of(f.timestamp) == date]
            filtered_total += len(facts)
            if dry_run:
                continue
            shown = facts if effective_limit is None else facts[:effective_limit]
            truncated = len(facts) - len(shown) if effective_limit else 0
            src_file = out_path / f"{src}.org"
            lines: list[str] = [
                f"#+TITLE: {src} conversations",
                f"#+DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                f"#+SOURCE: {src}",
                f"#+TOTAL: {len(shown)}",
                f"#+FILTERED_BY_DATE: {date or 'no'}",
                f"#+LIMIT: {effective_limit if effective_limit else 'unlimited'}",
                "",
            ]
            for f in shown:
                lines.append(f"* {f.title}")
                lines.append(":PROPERTIES:")
                lines.append(f":ID: {f.id}")
                lines.append(f":CATEGORY: {f.category}")
                lines.append(f":WEIGHT: {f.weight}")
                if f.timestamp:
                    lines.append(f":TIMESTAMP: {f.timestamp}")
                lines.append(":END:")
                lines.append("")
                lines.append(f.content[:3000])
                lines.append("")
            src_file.write_text("\n".join(lines), encoding="utf-8")
            files.append(str(src_file))
            if truncated:
                errors.append(f"{src}: 截断 {truncated} 条（达 limit={effective_limit}）")
        except Exception as e:
            errors.append(f"{src}: {e}")

    return {
        "source": source,
        "total_facts": total,
        "filtered_by_date": filtered_total,
        "output_dir": str(out_path),
        "files": files,
        "errors": errors,
        "dry_run": dry_run,
        "limit": effective_limit,
    }

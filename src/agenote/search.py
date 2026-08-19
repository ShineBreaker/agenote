# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.search — 全文检索（单域 + 跨域加权）。

从 cards.py 拆出（ADR-0002）：检索算法（关键词解析/打分/片段/snippet）
集中于此；core 的 5 个搜索辅助函数一并迁入（只有检索路径使用它们）。

- cmd_search：CLI search 子命令（单域 --regex / 跨域加权两条路径）
- _cross_domain_search：与 MCP agenote_search 行为对齐的跨域加权检索
"""

import argparse
import json
import re
import shlex
import shutil
import subprocess
from pathlib import Path

from agenote import config
from agenote.core import (
    die,
    default_context,
    agenote_context,
)
from agenote.extract.models import RECONCILE_DEFAULT_WEIGHT
from agenote.orgserde import (
    parse_org_prop,
    read_org_title,
)

# 检索评分系数与片段参数（config [weights] / [search] 覆盖）
SCORE_TERM_HIT = int(config.get("weights", "score_term_hit"))  # 每命中词得分
SCORE_TITLE_BONUS = int(config.get("weights", "score_title_bonus"))  # 标题命中加分
SCORE_PHRASE_BONUS = int(config.get("weights", "score_phrase_bonus"))  # 短语命中加分（单域）
SEARCH_LIMIT = int(config.get("search", "limit"))  # 结果默认上限
SNIPPET_MAX_CHARS = int(config.get("search", "snippet_max_chars"))  # 片段截断字符数
SNIPPET_CONTEXT_LINES = int(config.get("search", "snippet_context_lines"))  # 片段窗口行数


# ═══════════════════════════════════════════════════════════════════════════════
# 搜索辅助（自 core.py 迁入）
# ═══════════════════════════════════════════════════════════════════════════════


def _iter_search_targets(ctx=None) -> list[Path]:
    """返回全文检索目标文件。"""
    ctx = ctx or default_context()
    targets = []
    if ctx.experiences.exists():
        targets.extend(
            f
            for f in sorted(ctx.experiences.rglob("*.org"))
            if f.is_file() and not f.is_symlink()
        )
    if ctx.memory_org.exists():
        targets.append(ctx.memory_org)
    return targets


def _query_terms(query: str) -> list[str]:
    """把用户查询拆成适合模糊检索的关键词。"""
    try:
        pieces = shlex.split(query)
    except ValueError:
        pieces = query.split()

    terms = []
    for piece in pieces:
        for term in re.split(r"[/,，、]+", piece):
            term = term.strip()
            if term:
                terms.append(term)
    if not terms and query.strip():
        terms = [query.strip()]

    unique = []
    seen = set()
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(term)
    return unique


def _line_contains_any(line: str, needles: list[str], case_sensitive: bool) -> bool:
    """判断一行是否包含任一关键词。"""
    haystack = line if case_sensitive else line.casefold()
    return any(needle in haystack for needle in needles)


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并上下文行号范围。"""
    if not ranges:
        return []
    ranges = sorted(ranges)
    merged = [ranges[0]]
    for start, end in ranges[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + 1:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def _range_score(
    lines: list[str], start: int, end: int, needles: list[str], case_sensitive: bool
) -> int:
    """计算上下文块与查询词的相关度。"""
    block = "\n".join(lines[start : end + 1])
    haystack = block if case_sensitive else block.casefold()
    matched_terms = [needle for needle in needles if needle in haystack]
    return len(matched_terms) * SCORE_TERM_HIT + sum(
        haystack.count(needle) for needle in matched_terms
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: search — 全文检索
# ═══════════════════════════════════════════════════════════════════════════════


def _make_search_snippet(
    match: dict, normalized_terms: list[str], case_sensitive: bool
) -> str:
    """从匹配项中提取首个 hit 前后两行的片段，最多 200 字符。"""
    lines = match["content"].splitlines()
    hit_indexes = [
        i
        for i, line in enumerate(lines)
        if _line_contains_any(line, normalized_terms, case_sensitive)
    ]
    if not hit_indexes:
        return ""
    start = max(0, hit_indexes[0] - SNIPPET_CONTEXT_LINES)
    end = min(len(lines) - 1, hit_indexes[0] + SNIPPET_CONTEXT_LINES)
    snippet = "\n".join(lines[start : end + 1])
    if len(snippet) > SNIPPET_MAX_CHARS:
        snippet = snippet[:SNIPPET_MAX_CHARS] + "..."
    return snippet


def _cross_domain_search(
    query: str,
    limit: int = SEARCH_LIMIT,
    case_sensitive: bool = False,
) -> list[dict]:
    """跨域加权检索：同时扫人类域 + agenote 域 + reconcile 事实。

    与 MCP agenote_search 行为对齐：各域权重取自 ctx.default_weight,
    reconcile 默认 RECONCILE_DEFAULT_WEIGHT。返回按加权分数降序排列的结果列表。
    """
    terms = _query_terms(query)
    if not terms:
        die("必须提供搜索关键词")

    normalized_terms = terms if case_sensitive else [t.casefold() for t in terms]
    results: list[dict] = []

    for ctx in (default_context(), agenote_context()):
        weight = ctx.default_weight
        for filepath in _iter_search_targets(ctx):
            try:
                content = filepath.read_text(encoding="utf-8")
            except OSError:
                continue
            haystack = content if case_sensitive else content.casefold()
            term_hits = [t for t in normalized_terms if t in haystack]
            if not term_hits:
                continue
            occurrence_count = sum(haystack.count(t) for t in term_hits)
            title = read_org_title(content)
            title_hay = title if case_sensitive else title.casefold()
            title_bonus = SCORE_TITLE_BONUS * sum(1 for t in term_hits if t in title_hay)
            raw_score = len(term_hits) * SCORE_TERM_HIT + occurrence_count + title_bonus
            results.append(
                {
                    "domain": ctx.name,
                    "weight": weight,
                    "raw_score": raw_score,
                    "score": round(raw_score * weight, 1),
                    "title": title,
                    "file": str(filepath),
                    "snippet": _make_search_snippet(
                        {"content": content}, normalized_terms, case_sensitive
                    ),
                }
            )

    # reconcile 事实（其他 agent 的 memory，weight 低于 KB 卡片）
    try:
        from agenote.reconcile import load_reconcile_facts

        for fact in load_reconcile_facts():
            hay = fact.get("content", "")
            haystack = hay if case_sensitive else hay.casefold()
            term_hits = [t for t in normalized_terms if t in haystack]
            if not term_hits:
                continue
            occurrence_count = sum(haystack.count(t) for t in term_hits)
            title = fact.get("title", "")
            title_hay = title if case_sensitive else title.casefold()
            title_bonus = SCORE_TITLE_BONUS * sum(1 for t in term_hits if t in title_hay)
            raw_score = len(term_hits) * SCORE_TERM_HIT + occurrence_count + title_bonus
            fact_weight = fact.get("weight", RECONCILE_DEFAULT_WEIGHT)
            results.append(
                {
                    "domain": "reconcile",
                    "source": fact.get("source", ""),
                    "weight": fact_weight,
                    "raw_score": raw_score,
                    "score": round(raw_score * fact_weight, 1),
                    "title": title,
                    "file": "",
                    "id": fact.get("id", ""),
                    "snippet": hay[:SNIPPET_MAX_CHARS]
                    + ("..." if len(hay) > SNIPPET_MAX_CHARS else ""),
                }
            )
    except Exception:
        pass  # reconcile 索引不可用 → 静默跳过

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def _cmd_cross_domain_search(args: argparse.Namespace) -> None:
    """跨域加权检索的输出与格式化。"""
    results = _cross_domain_search(
        args.query,
        limit=getattr(args, "limit", SEARCH_LIMIT),
        case_sensitive=getattr(args, "case_sensitive", False),
    )

    if not results:
        if getattr(args, "json", False):
            print(json.dumps([], ensure_ascii=False, indent=2))
        else:
            print(f"未找到匹配: {args.query}")
        return

    if getattr(args, "json", False):
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    for idx, r in enumerate(results):
        domain_tag = f"[{r.get('domain', '?')}]"
        if r.get("source"):
            domain_tag = f"[{r['domain']}:{r['source']}]"
        print(f"{domain_tag} {r['title']}  score={r['score']}  {r.get('file', r.get('id', ''))}")
        if r.get("snippet"):
            for line in r["snippet"].splitlines():
                print(f"  | {line}")
        if idx < len(results) - 1:
            print()


def cmd_search(args: argparse.Namespace, ctx=None) -> None:
    """在 experiences/ 和 MEMORY.org 中全文检索。"""
    # 跨域加权检索（default，匹配 MCP agenote_search 行为）
    if getattr(args, "_cross_domain", False) and not getattr(args, "regex", False):
        _cmd_cross_domain_search(args)
        return

    # 单域检索（--domain human/agenote 显式指定或 --regex 模式）
    ctx = ctx or default_context()
    query = args.query
    context = args.context

    if args.json and args.regex:
        die("--json 与 --regex 互斥；--regex 模式只支持人类可读输出")

    if args.regex:
        targets = [str(ctx.experiences), str(ctx.memory_org)]
        if shutil.which("rg"):
            cmd = ["rg", "--color=never", "-n", "-C", str(context), query] + targets
        else:
            cmd = ["grep", "-r", "-n", "-C", str(context), query] + targets

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(result.stdout, end="")
        else:
            print(f"未找到匹配: {query}")
        return

    terms = _query_terms(query)
    if not terms:
        die("必须提供搜索关键词")

    normalized_terms = terms if args.case_sensitive else [t.casefold() for t in terms]
    normalized_phrase = query if args.case_sensitive else query.casefold()
    matches = []

    for filepath in _iter_search_targets(ctx):
        try:
            content = filepath.read_text(encoding="utf-8")
        except OSError:
            continue

        haystack = content if args.case_sensitive else content.casefold()
        term_hits = [
            term for raw, term in zip(terms, normalized_terms) if term in haystack
        ]
        if not term_hits:
            continue
        if args.all_terms and len(term_hits) != len(terms):
            continue

        occurrence_count = sum(haystack.count(term) for term in term_hits)
        phrase_bonus = (
            SCORE_PHRASE_BONUS if normalized_phrase and normalized_phrase in haystack else 0
        )
        title = read_org_title(content)
        title_haystack = title if args.case_sensitive else title.casefold()
        title_bonus = SCORE_TITLE_BONUS * sum(1 for term in term_hits if term in title_haystack)
        score = (
            len(term_hits) * SCORE_TERM_HIT + occurrence_count + phrase_bonus + title_bonus
        )

        matches.append(
            {
                "filepath": filepath,
                "content": content,
                "score": score,
                "matched": [
                    raw
                    for raw, term in zip(terms, normalized_terms)
                    if term in haystack
                ],
                "title": title,
            }
        )

    if not matches:
        if args.json:
            print(json.dumps([], ensure_ascii=False, indent=2))
        else:
            print(f"未找到匹配: {query}")
        return

    matches.sort(key=lambda item: (-item["score"], str(item["filepath"])))
    limit = max(1, args.limit)

    if args.json:
        results = []
        for match in matches[:limit]:
            card_id = (
                parse_org_prop(match["content"], "ID")
                or match["filepath"].stem.split("-")[0]
            )
            results.append(
                {
                    "id": card_id,
                    "title": match["title"],
                    "score": match["score"],
                    "snippet": _make_search_snippet(
                        match, normalized_terms, args.case_sensitive
                    ),
                }
            )
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    for idx, match in enumerate(matches[:limit]):
        filepath = match["filepath"]
        rel = (
            filepath.relative_to(ctx.root)
            if filepath.is_relative_to(ctx.root)
            else filepath
        )
        print(
            f"== {rel} | score={match['score']} | matched={', '.join(match['matched'])} =="
        )
        if match["title"] != "unknown":
            print(f"title: {match['title']}")

        lines = match["content"].splitlines()
        hit_indexes = [
            i
            for i, line in enumerate(lines)
            if _line_contains_any(line, normalized_terms, args.case_sensitive)
        ]
        ranges = _merge_ranges(
            [
                (max(0, i - context), min(len(lines) - 1, i + context))
                for i in hit_indexes
            ]
        )
        selected_ranges = sorted(
            sorted(
                ranges,
                key=lambda r: (
                    -_range_score(
                        lines, r[0], r[1], normalized_terms, args.case_sensitive
                    ),
                    r[0],
                ),
            )[: max(1, args.max_blocks)]
        )
        for start, end in selected_ranges:
            for line_idx in range(start, end + 1):
                sep = ":" if line_idx in hit_indexes else "-"
                print(f"{filepath}{sep}{line_idx + 1}{sep}{lines[line_idx]}")
            if (start, end) != selected_ranges[-1]:
                print("--")
        if idx != min(len(matches), limit) - 1:
            print()

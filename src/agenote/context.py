# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.context — `agenote context` 注入简报（C 线 C1，只读、免锁、零写盘）。

设计规格：AGENOTE_INJECTION_DESIGN.md §3 C1（宿主插件/hook 实时调用本命令，
将输出注入会话上下文；agenote 本身不做常驻进程与自动注入）。

- 两粒度：`--mode session`（开篇简报，选集 U→E→P→F→R）与
  `--mode recall --query Q`（既有 BM25 召回 top-N，带分数下限）。
- 三态：ok / empty / disabled。text 格式下非 ok 一律**零字节输出**——
  空 additionalContext 天然不注入，宿主与注入器零分支；json 带
  `status` 字段供排障（D5）。
- 预算恒为字符（非 token）：保头降级先裁 R→F→P→E→U，单条截断附 `…`，
  总输出（含 marker）硬保证 ≤ budget（D3）。codex 的 tokens 折算在注入器侧。
- marker 首行 `<!-- agenote-context v1 mode=<m> budget=<n> -->`：版本号
  v1 是代码常量（不进配置），**无时间戳**——时间戳会破坏注入器「指纹未变
  则不注」的判定（D2/D8）。
- F 类「weight top-K」：记忆条目无独立 WEIGHT 属性，权重以时效链
  （VALIDATED_AT → UPDATED → CREATED，与 entry_freshness_tag 同一优先级）
  为代理排序——最近验证的记忆即当前最可信的行为准则。
- 类型化条目读取走 memory._iter_memory_entries（六节五类型单一解析路径），
  BM25 参数与 search 共用 [search] 的 k1/b，不另起旁路。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agenote import config
from agenote.core import agenote_context, die
from agenote.memory import (
    _iter_memory_entries,
    _read_memory_org_text,
    resolve_machine_key,
    scope_for_type,
)
from agenote.orgserde import parse_memory_date
from agenote.ranking import BM25, tokenize

MARKER_VERSION = "v1"  # 代码常量：marker 版本（不进配置、无时间戳）
HOSTS = ("zcode", "claude", "codex", "pi", "opencode", "hermes")
ENTRY_TYPES = ("U", "F", "P", "E", "R")
# 预算裁剪的「先裁」顺序（保头降级：U 画像最后裁）
TRIM_ORDER = ("R", "F", "P", "E", "U")
# session 单行正文截断（纯展示层截断，刻意不进配置）
LINE_MAX_CHARS = 160
# recall 两行式正文摘要长度（设计 C1 规定值）
RECALL_SUMMARY_CHARS = 200
# 简报末尾固定指引（T8 先目录后正文；仅 session 模式附加）
MORE_GUIDE = "更多：agenote memory --list --type X / agenote search <kw>"


def _cfg_bool(section: str, key: str) -> bool:
    """布尔配置取值（env 覆盖会以字符串抵达，与 projector._truthy 同口径）。"""
    val = config.get(section, key)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() not in ("", "0", "false", "no", "off")


def _cfg_num(section: str, key: str, cast):
    """数值配置取值；env/file 覆盖非法时受控退出而非裸 traceback。"""
    try:
        return cast(str(config.get(section, key)))
    except (TypeError, ValueError):
        die(f"配置键 [{section}].{key} 需为数值，得到: {config.get(section, key)!r}")


# ═══════════════════════════════════════════════════════════════════════════════
# 条目读取与选集
# ═══════════════════════════════════════════════════════════════════════════════


def _load_entries(ctx, types_set: set[str]) -> list[dict]:
    """读 MEMORY.org 类型化条目（deprecated 终态排除）。

    项目索引行（PATH 指针）保留——P 类路径匹配的载体；session/recall
    选集阶段再按「指针不是事实」排除。
    """
    if not ctx.memory_org.exists():
        return []
    rows = []
    for e in _iter_memory_entries(_read_memory_org_text(ctx)):
        if e["section"].lower() == "deprecated":
            continue
        if e["type"] not in types_set:
            continue
        rows.append(e)
    return rows


def _entry_scope(e: dict) -> str:
    return (e["props"].get("SCOPE") or "").strip().lower() or scope_for_type(e["type"] or "")


def _machine_matches(e: dict, machine: str) -> bool:
    """E 类本机判定：:MACHINE: 缺失视为本机遗留条目（与 --revalidate 判定互补）。"""
    m = (e["props"].get("MACHINE") or "").strip()
    return not m or m == machine


def _entry_weight_key(e: dict):
    """F 类排序权重：时效链 VALIDATED_AT → UPDATED → CREATED（新者在前）。"""
    for key in ("VALIDATED_AT", "UPDATED", "CREATED"):
        d = parse_memory_date((e["props"].get(key) or "").strip("[] "))
        if d is not None:
            return (d.isoformat(), e["id"])
    return ("", e["id"])


def _match_project_entries(entries: list[dict], cwd: Path, project_arg: str | None) -> list[dict]:
    """P 类确定性三步匹配（不做模糊最近邻——错误的项目记忆比没有更糟）：

    ①KB 内 P 条目/项目路径与 cwd 精确相等 → 命中；
    ②cwd 的 git root basename 与 P 条目名（索引行 id / :PROJECT: / 标题）相等 → 命中；
    ③都不中 → 返回空（简报不含 P 段，不猜）。
    索引行（PATH 指针）参与路径匹配：索引行命中即项目命中，同项目名下的
    P 条目（:PROJECT: 指针指向它）一并带出——指针不是事实，事实靠它定位。
    """
    try:
        cwd_resolved = cwd.resolve()
    except OSError:
        cwd_resolved = cwd
    path_targets = {cwd_resolved}
    name_targets: set[str] = set()
    if project_arg:
        name_targets.add(project_arg)
        if "/" in project_arg:
            try:
                path_targets.add(Path(project_arg).expanduser().resolve())
            except (OSError, RuntimeError):
                pass
    git_name = _git_root_name(cwd)
    if git_name:
        name_targets.add(git_name)

    def names_of(e: dict) -> set[str]:
        return {v for v in (e["id"], e["title"],
                            (e["props"].get("PROJECT") or "").strip()) if v}

    def path_hit(e: dict) -> bool:
        paths = [(e["props"].get(k) or "").strip() for k in ("PATH", "FILE")]
        if "/" in e["id"]:
            paths.append(e["id"])
        for pval in paths:
            if not pval:
                continue  # 空 PATH 展开成 "." 会误等 cwd，必须跳过
            try:
                rp = Path(pval).expanduser().resolve()
            except (OSError, RuntimeError):
                rp = Path(pval).expanduser()
            if rp in path_targets:
                return True
        return False

    candidates = [e for e in entries
                  if e["type"] == "P" and e["section"].lower() != "deprecated"]
    matched: list[dict] = []
    seen: set[int] = set()
    project_names: set[str] = set()
    for e in candidates:
        if path_hit(e) or (name_targets & names_of(e)):
            matched.append(e)
            seen.add(id(e))
            project_names |= names_of(e)
    if project_names:  # 索引行命中 → 同项目名 P 条目跟随命中
        for e in candidates:
            if id(e) not in seen and names_of(e) & project_names:
                matched.append(e)
                seen.add(id(e))
    return matched


def _git_root_name(cwd: Path) -> str:
    """cwd 所在 git 仓库根的 basename（含 cwd 自身；不在仓库内返回 ""）。"""
    try:
        cur = cwd.resolve()
    except OSError:
        cur = cwd
    if (cur / ".git").exists():
        return cur.name
    for parent in cur.parents:
        if (parent / ".git").exists():
            return parent.name
    return ""


def _select_session(entries: list[dict], cwd: Path, project_arg: str | None,
                    f_topk: int) -> list[tuple[str, list[dict]]]:
    """session 简报选集：U 全部 → E 本机 → P 当前项目 → F 权重 top-K → R 全部。"""
    machine = resolve_machine_key()
    by_type: dict[str, list[dict]] = {}
    for e in entries:
        if e["kind"] == "index":
            continue  # 指针行不当事实注入（与投影器同口径）
        by_type.setdefault(e["type"] or "?", []).append(e)
    p_hits = [e for e in _match_project_entries(entries, cwd, project_arg)
              if e["kind"] == "entry"]
    f_sorted = sorted(by_type.get("F", []), key=_entry_weight_key, reverse=True)
    groups: list[tuple[str, list[dict]]] = [
        ("U", by_type.get("U", [])),
        ("E", [e for e in by_type.get("E", []) if _machine_matches(e, machine)]),
        ("P", p_hits),
        ("F", f_sorted[: max(0, f_topk)]),
        ("R", by_type.get("R", [])),
    ]
    return [(t, rows) for t, rows in groups if rows]


# ═══════════════════════════════════════════════════════════════════════════════
# 渲染与预算裁剪
# ═══════════════════════════════════════════════════════════════════════════════


def _first_line(e: dict) -> str:
    """正文首行：钩子行优先（import/--add 恒写），降级正文首段。"""
    text = e["hook"] or e["body"]
    return text.split("\n")[0].strip()


def _session_line(e: dict) -> str:
    body = _first_line(e)
    if len(body) > LINE_MAX_CHARS:
        body = body[: LINE_MAX_CHARS - 1] + "…"
    return f"[{e['type']}|{_entry_scope(e)}] {e['title']} — {body}".rstrip()


def _recall_block(e: dict, score: float) -> list[str]:
    summary = (e["body"] or e["hook"] or e["title"])[:RECALL_SUMMARY_CHARS]
    return [f"[{e['type']}|{_entry_scope(e)}] {e['id']} {e['title']} (score {score:.2f})",
            f"  {summary}"]


def _assemble(marker: str, groups: list[tuple[str, list[tuple[dict, list[str]]]]],
              tail: str, budget: int) -> tuple[str, bool, list[dict]]:
    """组装 + 保头降级裁剪。返回 (text, truncated, 保留条目)。

    超预算时按 TRIM_ORDER（R→F→P→E→U）整条丢弃组尾条目重渲染；
    条目裁尽仍超（极端小预算）才硬截整段文本并以 `…` 收尾——
    硬保证 ≤ budget 恒成立。
    """
    def render(gs) -> tuple[str, list[dict]]:
        lines = [marker] if marker else []
        kept: list[dict] = []
        for _t, items in gs:
            for entry, block in items:
                lines.extend(block)
                kept.append(entry)
        if tail:
            lines.append(tail)
        text = "\n".join(lines) + "\n" if lines else ""
        return text, kept

    text, kept = render(groups)
    truncated = False
    while len(text) > budget:
        victim = None
        for t in TRIM_ORDER:  # 保头降级按 R→F→P→E→U，与组在文中的排列顺序无关
            for g in groups:
                if g[0] == t and g[1]:
                    victim = g
                    break
            if victim is not None:
                break
        if victim is None:
            break
        victim[1].pop()
        truncated = True
        text, kept = render(groups)
    if len(text) > budget:
        text = text[: budget - 1] + "…" if budget >= 1 else ""
        truncated = True
    return text, truncated, kept


def _result(status: str, mode: str, budget: int, host: str, *,
            text: str = "", entries: list[dict] | None = None,
            truncated: bool = False) -> dict:
    """统一三态结果；marker 仅 status=ok 时存在（无时间戳，注入器指纹友好）。"""
    marker = ""
    if status == "ok":
        marker = f"<!-- agenote-context {MARKER_VERSION} mode={mode} budget={budget} -->"
    return {
        "status": status, "marker": marker, "mode": mode, "budget": budget,
        "host": host, "truncated": truncated,
        "entries": entries or [], "content": text,
    }


def _entry_json(e: dict, score: float | None = None) -> dict:
    row = {"id": e["id"], "type": e["type"], "scope": _entry_scope(e),
           "title": e["title"]}
    if score is not None:
        row["score"] = round(score, 2)
    return row


# ═══════════════════════════════════════════════════════════════════════════════
# 两模式主体
# ═══════════════════════════════════════════════════════════════════════════════


def _session_brief(ctx, args) -> dict:
    types_set = _parse_types(getattr(args, "types", None))
    budget = _budget_of(args)
    entries = _load_entries(ctx, types_set)
    if not entries:
        return _result("empty", "session", budget, args.host)
    groups = _select_session(entries, Path.cwd(), getattr(args, "project", None),
                             _cfg_num("injection", "session_f_topk", int))
    if not groups:
        return _result("empty", "session", budget, args.host)
    rendered = [(t, [(e, [_session_line(e)]) for e in rows]) for t, rows in groups]
    text, truncated, kept = _assemble(_marker_of("session", budget), rendered,
                                      MORE_GUIDE, budget)
    return _result("ok", "session", budget, args.host,
                   text=text, entries=[_entry_json(e) for e in kept],
                   truncated=truncated)


def _recall_brief(ctx, args) -> dict:
    budget = _budget_of(args)
    query = getattr(args, "query", None)
    if not query:
        die("recall 模式必须提供 --query（如: agenote context --mode recall --query <词>）")
    min_query = _cfg_num("injection", "recall_min_query", int)
    topk = _cfg_num("injection", "recall_topk", int)
    min_score = _cfg_num("injection", "recall_min_score", float)
    types_set = _parse_types(getattr(args, "types", None))
    entries = _load_entries(ctx, types_set)
    # 短 query 门槛与注入器预检同款（D2）：「继续/ok」类 prompt 不值得注入
    if not entries or len(query) < min_query:
        return _result("empty", "recall", budget, args.host)
    machine = resolve_machine_key()
    corpus = [e for e in entries
              if e["kind"] != "index"
              and (e["type"] != "E" or _machine_matches(e, machine))]
    # 语料键用位置索引而非条目 id：id 理论可撞（手写条目），键冲突会静默塌缩语料
    bm25 = BM25(
        {i: tokenize(" ".join([e["title"], e["hook"], e["body"]]))
         for i, e in enumerate(corpus)},
        k1=float(config.get("search", "bm25_k1")),
        b=float(config.get("search", "bm25_b")),
    )
    q_tokens = tokenize(query)
    scored = sorted(
        ((bm25.score(i, q_tokens), e) for i, e in enumerate(corpus)),
        key=lambda pair: (-pair[0], pair[1]["id"]),
    )
    # 分数下限过滤在前、top-N 截断在后：低分结果即使填不满 N 也不输出
    hits = [(s, e) for s, e in scored if s >= min_score][: max(0, topk)]
    if not hits:
        return _result("empty", "recall", budget, args.host)
    rendered = [("R", [(e, _recall_block(e, s)) for s, e in hits])]
    text, truncated, kept = _assemble(_marker_of("recall", budget), rendered, "", budget)
    kept_scores = {e["id"]: s for s, e in hits}
    return _result("ok", "recall", budget, args.host,
                   text=text, truncated=truncated,
                   entries=[_entry_json(e, kept_scores.get(e["id"])) for e in kept])


# ═══════════════════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════════════════


def _marker_of(mode: str, budget: int) -> str:
    return f"<!-- agenote-context {MARKER_VERSION} mode={mode} budget={budget} -->"


def _parse_types(raw: str | None) -> set[str]:
    spec = raw if raw else str(config.get("injection", "types_default"))
    types = {t.strip().upper() for t in spec.split(",") if t.strip()}
    bad = types - set(ENTRY_TYPES)
    if bad:
        die(f"--types 含未知类型: {','.join(sorted(bad))}（可选 {'/'.join(ENTRY_TYPES)}）")
    return types or set(ENTRY_TYPES)


def _budget_of(args) -> int:
    budget = getattr(args, "budget", None)
    if budget is None:
        return _cfg_num("injection", "default_budget", int)
    if int(budget) <= 0:
        die("--budget 需为正整数（恒为字符）")
    return int(budget)


def build_context_result(args: argparse.Namespace, ctx=None) -> dict:
    """三态判定 + 两模式分发（供 cmd_context 与测试）。"""
    ctx = ctx or agenote_context()
    host = (getattr(args, "host", None) or "generic").lower()
    mode = getattr(args, "mode", None) or "session"
    # 开关判定链：总开关 → per-host 开关（generic 无专属键恒走默认）
    if not _cfg_bool("injection", "enabled"):
        return _result("disabled", mode, _budget_of(args), host)
    if host != "generic" and not _cfg_bool("injection.hosts", f"{host}_enabled"):
        return _result("disabled", mode, _budget_of(args), host)
    if mode == "recall":
        return _recall_brief(ctx, args)
    return _session_brief(ctx, args)


def cmd_context(args: argparse.Namespace, ctx=None) -> None:
    """`agenote context`：只读免锁；text 非 ok 态零字节，json 恒带 status。"""
    result = build_context_result(args, ctx)
    if getattr(args, "format", "text") == "json":
        print(json.dumps(result, ensure_ascii=False))
        return
    # 原样写出（content 已含结尾换行）；空串即零字节输出
    sys.stdout.write(result["content"])

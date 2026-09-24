# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.memory_import — N2 `memory import` 摄取管道（聚合方向）。

七步：read（复用 memscan，已走 S8 safe_read）→ gate（secret 高置信前缀
子集 + is_noise_fact，命中只记类别不记值）→ echo（[memories.targets]
非空路径前缀下跳过）→ normalize（N1 模型映射，type 缺失启发式 + 标需
复核）→ dedupe（ORIGIN_ID 幂等跳过；标题 Jaccard ≥ 阈值 + 同 TYPE+SCOPE
→ suspected_dup）→ conflict（同 TYPE+SCOPE + 主题似 + body 异 → 冲突
队列，不自动写）→ write（atomic_write；锁由 CLI 层持有）。

语义裁决一律交 agent/人：CLI 只出结构化候选。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agenote import config
from agenote.core import default_context, is_noise_fact, today
from agenote.safeio import atomic_write

CONFLICTS_FILE = ".memory-conflicts.json"

# gate：高置信密钥前缀子集（v1 不承诺完备，宁可误拦）。命中只记类别名。
_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}")),
    ("openai_key", re.compile(r"sk-(?:proj|live|test)-[A-Za-z0-9_-]{8,}")),
    ("github_token", re.compile(r"(?:ghp_|gho_|github_pat_)[A-Za-z0-9_]{8,}")),
    ("slack_token", re.compile(r"xox[bpas]-[A-Za-z0-9-]{8,}")),
    ("aws_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google_key", re.compile(r"AIza[0-9A-Za-z_-]{10,}")),
    ("hf_token", re.compile(r"hf_[A-Za-z0-9]{8,}")),
    ("gitlab_token", re.compile(r"glpat-[A-Za-z0-9_-]{8,}")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
]

_TYPE_WORDS = {
    "u": "U", "user": "U",
    "f": "F", "feedback": "F",
    "p": "P", "project": "P",
    "e": "E", "environment": "E",
    "r": "R", "reference": "R",
}
TYPE_TO_SECTION = {"U": "user", "F": "feedback", "P": "project", "E": "environment", "R": "reference"}
SCOPE_FOR_TYPE = {"E": "machine", "P": "project"}

# ponytail: 固定 body 相似阈值 0.5，配 SCHEMA 阈值调参若不够再进配置
_BODY_SIM_THRESHOLD = 0.5

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", re.UNICODE)


def scan_secret(text: str) -> str:
    """返回命中的密钥类别名；无命中返回 \"\"（只记类别不记值）。"""
    for category, pat in _SECRET_PATTERNS:
        if pat.search(text):
            return category
    return ""


def jaccard(a: str, b: str) -> float:
    """标题/正文相似度：token 集合 Jaccard（CJK 按单字切分）。"""
    sa, sb = set(_TOKEN_RE.findall(a.lower())), set(_TOKEN_RE.findall(b.lower()))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _echo_prefixes() -> list[Path]:
    """非空 [memories.targets] 路径（展开 ~ 与 XDG 占位符）。"""
    out = []
    for key in ("zcode_dir", "claude_dir", "codex_suggest_dir"):
        val = str(config.get("memories.targets", key)).strip()
        if val:
            out.append(config.get_path("memories.targets", key))
    return out


def _is_echo(path: str, prefixes: list[Path]) -> bool:
    p = str(Path(path))
    return any(p == str(t) or p.startswith(str(t).rstrip("/") + "/") for t in prefixes)


def _heuristic_type(title: str, body: str) -> tuple[str, bool]:
    """源无类型时的关键词启发式；返回 (TYPE, needs_review=True)。"""
    text = f"{title}\n{body}"
    if re.search(r"环境|machine|主机|Guix|guix|系统版本|内核", text):
        return "E", True
    if re.search(r"偏好|回复用|用户要求|习惯", text):
        return "U", True
    if re.search(r"参考|文档|手册|路径|命令速查", text):
        return "R", True
    if re.search(r"项目|project|提交前|构建", text):
        return "P", True
    return "F", True


def normalize(entry: dict) -> dict:
    """字段映射到 N1 模型。type 缺失时启发式 + needs_review。"""
    raw = (entry.get("type") or "").strip().lower()
    if raw in _TYPE_WORDS:
        mem_type, review = _TYPE_WORDS[raw], False
    else:
        mem_type, review = _heuristic_type(entry.get("name", ""), entry.get("body", ""))
    scope = SCOPE_FOR_TYPE.get(mem_type, "user")
    # lazy：memory 薄转发循环依赖，helpers 在调用时导入
    from agenote.memory import origin_id

    return {
        "source": entry.get("source", ""),
        "path": entry.get("path", ""),
        "title": entry.get("name", "") or "(untitled)",
        "body": entry.get("body", ""),
        "type": mem_type,
        "scope": scope,
        "needs_review": review,
        "origin_id": origin_id(entry.get("source", ""), entry.get("path", ""), entry.get("name", "")),
    }


def _existing_state(ctx) -> tuple[set[str], list[dict]]:
    """MEMORY.org 现有 ORIGIN_ID 集 + (TYPE/SCOPE/标题/正文) 候选比对表。"""
    from agenote.memory import _iter_memory_entries

    if not ctx.memory_org.exists():
        return set(), []
    text = ctx.memory_org.read_text(encoding="utf-8")
    entries = _iter_memory_entries(text)
    origins = {e["props"].get("ORIGIN_ID", "") for e in entries} - {""}
    rows = [
        {"id": e["id"], "title": e["title"], "type": e["type"] or "",
         "scope": (e["props"].get("SCOPE") or "").strip().lower(),
         "body": _entry_body(text, e["id"])}
        for e in entries if e["kind"] == "entry"
    ]
    return origins, rows


def _entry_body(text: str, entry_id: str) -> str:
    """取 MEMORY.org 某条目正文（去 props/hook 行，供 conflict 比对）。"""
    from agenote.orgserde import is_memory_boundary

    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines)
                  if re.match(rf"^\*\*\s+{re.escape(entry_id)}(?:\s|$)", ln)), None)
    if start is None:
        return ""
    buf = []
    for ln in lines[start + 1:]:
        if is_memory_boundary(ln):
            break
        hm = re.match(r"\s*#\s?(.*)", ln)
        if hm and hm.group(1).strip():
            buf.append(hm.group(1).strip())  # 钩子行是条目摘要，纳入比对
            continue
        if re.match(r"\s*:\w+:", ln) or ln.strip() in (":PROPERTIES:", ":END:"):
            continue
        if ln.strip():
            buf.append(ln.strip())
    return "\n".join(buf)


def _conflicts_path(ctx) -> Path:
    return ctx.root / CONFLICTS_FILE


def load_conflicts(ctx) -> list[dict]:
    p = _conflicts_path(ctx)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("conflicts", [])
    except (OSError, ValueError):
        return []


def run_import(source: str = "all", dry_run: bool = False, ctx=None) -> dict:
    """执行七步摄取管道，返回五类清单报告。写盘经 atomic_write；锁由 CLI 持有。"""
    from agenote.memscan import scan_memories

    ctx = ctx or default_context()
    threshold = float(str(config.get("memories", "import_dedup_threshold")))
    _raw_secret = config.get("memories", "secret_scan_enabled")
    secret_on = _raw_secret if isinstance(_raw_secret, bool) else str(_raw_secret).lower() in ("1", "true", "yes")
    prefixes = _echo_prefixes()
    origins, existing = _existing_state(ctx)

    report: dict = {
        "source": source, "dry_run": dry_run,
        "imported": [], "skipped": [], "suspected_dup": [],
        "conflicted": [], "secret_blocked": [],
    }
    pending: list[dict] = []  # 待写候选
    queued: list[dict] = []  # 待写冲突队列项

    for entry in scan_memories(source).get("entries", []):
        title = entry.get("name", "") or "(untitled)"
        blob = f"{title}\n{entry.get('body', '')}"
        if secret_on and (cat := scan_secret(blob)):
            report["secret_blocked"].append({"title": title, "category": cat,
                                             "source": entry.get("source", "")})
            continue
        if is_noise_fact({"content": entry.get("body", ""), "title": title}):
            report["skipped"].append({"title": title, "reason": "noise"})
            continue
        if prefixes and _is_echo(entry.get("path", ""), prefixes):
            report["skipped"].append({"title": title, "reason": "echo"})
            continue
        cand = normalize(entry)
        if cand["origin_id"] in origins:
            report["skipped"].append({"title": title, "reason": "origin_id"})
            continue
        # ⑤⑥：同 TYPE+SCOPE 下标题 Jaccard ≥ 阈值 → body 似为 dup，异为 conflict
        match = next((e for e in existing
                      if e["type"] == cand["type"] and e["scope"] == cand["scope"]
                      and jaccard(cand["title"], e["title"]) >= threshold), None)
        if match is None:
            match = next((e for e in pending
                          if e["type"] == cand["type"] and e["scope"] == cand["scope"]
                          and jaccard(cand["title"], e["title"]) >= threshold), None)
            if match is not None:
                match = {**match, "id": "(pending)"}
        if match is not None:
            item = {"title": title, "existing": match["id"], "source": cand["source"]}
            if jaccard(cand["body"], match.get("body", "")) >= _BODY_SIM_THRESHOLD:
                report["suspected_dup"].append(item)
            else:
                report["conflicted"].append(item)
                queued.append({"candidate": {k: cand[k] for k in
                                             ("source", "path", "title", "type", "scope", "origin_id")},
                               "existing": {"id": match["id"], "title": match["title"]},
                               "reason": "same type+scope, similar topic, different body",
                               "at": today()})
            continue
        pending.append(cand)
        origins.add(cand["origin_id"])
        existing.append({"id": "(new)", "title": cand["title"], "type": cand["type"],
                         "scope": cand["scope"], "body": cand["body"]})
        report["imported"].append({"title": title, "type": cand["type"],
                                   "scope": cand["scope"], "origin_id": cand["origin_id"],
                                   "needs_review": cand["needs_review"]})

    if not dry_run:
        _write_entries(pending, ctx)
        if queued:
            all_q = load_conflicts(ctx) + queued
            atomic_write(_conflicts_path(ctx), json.dumps(all_q, ensure_ascii=False, indent=2) + "\n")
    return report


def _write_entries(cands: list[dict], ctx) -> None:
    """候选追加进 MEMORY.org 对应节（复用 N1 id/节定位逻辑）。"""
    if not cands:
        return
    from agenote.core import _init_memory_template_for_ctx
    from agenote.memory import (
        _find_section_end, _next_memory_id, _parse_memory_sections, _read_memory_org_text,
    )

    if not ctx.memory_org.exists():
        _init_memory_template_for_ctx(ctx)
    text = _read_memory_org_text(ctx)
    lines = text.split("\n")
    for cand in cands:
        sections = _parse_memory_sections("\n".join(lines))
        want = TYPE_TO_SECTION[cand["type"]]
        target = next((e for n, es in sections.items() if want in n.lower() for e in es), None)
        if target is not None:
            new_id = _next_memory_id(target[2], cand["type"])
            at = _find_section_end(lines, target[0])
        else:
            new_id, secs = f"{cand['type']}001", "\n".join(lines)
            lines = (secs + f"\n* {want}\n").split("\n")
            at = len(lines)
        block = (f"\n** {new_id} {cand['title']}\n   :PROPERTIES:\n"
                 f"   :CREATED:  [{today()}]\n   :UPDATED:  [{today()}]\n"
                 f"   :TYPE:     {cand['type']}\n   :SCOPE:    {cand['scope']}\n"
                 f"   :ORIGIN_ID: {cand['origin_id']}\n"
                 f"   :ORIGIN_AGENT: {cand['source']}\n   :END:\n")
        if cand["body"].strip():
            block += f"   {cand['body'].strip()}\n"
        lines.insert(at, block)
    atomic_write(ctx.memory_org, "\n".join(lines))

# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""omp conversation extractor (JSONL event stream, parentId reconstruction).

omp 是 pi-coding-agent 的下游 fork,会话存储在 XDG_CONFIG_HOME/omp/sessions/,
按项目分子目录存放 .jsonl 文件。

Schema: JSONL 事件流,每条 {type, id, parentId, timestamp, ...}
  type=session  → {id, timestamp (ISO UTC), cwd}
  type=message  → {id, parentId, message: {role, content, ...}}

消息顺序由 parentId 链重建（NOT timestamp）。配对/构造事实走 framework
pair_turns,adapter 只提供:JSONL 解析、parentId 重建、递归目录遍历。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from agenote.extract import extract_title, resolve_xdg_path
from agenote.extract.base import Turn, pair_turns, register
from agenote.reconcile import RECONCILE_DEFAULT_WEIGHT

OMP_SESSIONS_DIR = resolve_xdg_path(
    "OMP_SESSIONS_DIR",
    "$XDG_CONFIG_HOME/omp/sessions",
)


def _normalize_content(content) -> str:
    """Extract text from omp message content (str, list of parts)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                ptype = part.get("type", "")
                if ptype == "text":
                    texts.append(part.get("text", ""))
                elif ptype == "tool_use":
                    texts.append(
                        f"[tool_use: {part.get('name', '?')}] "
                        f"{json.dumps(part.get('input', {}), ensure_ascii=False)[:300]}"
                    )
                elif ptype == "tool_result":
                    c = part.get("content", "")
                    if isinstance(c, str):
                        texts.append(f"[tool_result] {c[:500]}")
                    else:
                        texts.append(
                            f"[tool_result] {json.dumps(c, ensure_ascii=False)[:500]}"
                        )
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(t for t in texts if t)
    return str(content)


def _load_events(jsonl_path: Path) -> tuple[dict, list[dict]]:
    """读一个 .jsonl 会话文件 → (session_meta, messages)，坏行跳过。"""
    session_meta: dict = {}
    messages: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("type") == "session":
                session_meta = {
                    "id": evt.get("id", jsonl_path.stem),
                    "cwd": evt.get("cwd", ""),
                }
            elif evt.get("type") == "message":
                messages.append(evt)
    return session_meta, messages


def _rebuild_order(messages: list[dict]) -> list[dict]:
    """按 parentId 链重建消息顺序（迭代 DFS，extract 与 trace 共用）。"""
    msg_by_id: dict[str, dict] = {m.get("id", ""): m for m in messages if m.get("id")}
    children: dict[str, list[str]] = {}
    for m in messages:
        pid = m.get("parentId", "") or ""
        if pid:
            children.setdefault(pid, []).append(m.get("id", ""))
    root_ids = [
        m.get("id", "")
        for m in messages
        if m.get("id") and (not (m.get("parentId") or "") or m.get("parentId") not in msg_by_id)
    ]

    ordered: list[dict] = []
    for rid in root_ids:
        stack: list[str] = [rid]
        while stack:
            mid = stack.pop()
            if mid not in msg_by_id:
                continue
            ordered.append(msg_by_id[mid])
            # 逆序压栈保证最左子消息先处理
            for cid in reversed(children.get(mid, [])):
                stack.append(cid)
    return ordered


def _iter_turns(jsonl_path: Path) -> Iterator[Turn]:
    """一个 .jsonl 会话文件 → Turn 流（parentId 重建后的 user/assistant 回合）。"""
    session_meta, messages = _load_events(jsonl_path)
    if not messages:
        return
    ordered = _rebuild_order(messages)

    # session 级标题（第一user消息），作为 pair_turns 里逐对标题提取的 fallback
    session_title = "Untitled"
    for m in ordered:
        msg = m.get("message", {}) if isinstance(m.get("message"), dict) else {}
        if msg.get("role") == "user":
            session_title = extract_title(_normalize_content(msg.get("content", "")).strip()) or "Untitled"
            break

    session_id = session_meta.get("id", jsonl_path.stem)
    for m in ordered:
        msg = m.get("message", {}) if isinstance(m.get("message"), dict) else {}
        role = msg.get("role", "")
        if role not in ("user", "assistant"):
            continue
        yield Turn(
            role=role,
            text=_normalize_content(msg.get("content", "")).strip(),
            timestamp=str(m.get("timestamp") or msg.get("timestamp") or ""),
            native_id=m.get("id", ""),
            session={"id": session_id, "title": session_title, "directory": session_meta.get("cwd", "")},
        )


@register("omp")
def extract_omp() -> tuple[list, list[str]]:
    """Extract from omp sessions/ (read-only, parentId-rebuilt, recursive).

    递归遍历 **/*.jsonl,跳过 __advisor.jsonl 子对话文件。
    """
    facts = []
    errors: list[str] = []
    if not OMP_SESSIONS_DIR.exists():
        return [], [f"omp sessions dir 不存在: {OMP_SESSIONS_DIR}"]
    for jsonl_path in sorted(OMP_SESSIONS_DIR.glob("**/*.jsonl")):
        if jsonl_path.name == "__advisor.jsonl":
            continue
        try:
            facts.extend(
                pair_turns(
                    _iter_turns(jsonl_path),
                    source="omp",
                    weight=RECONCILE_DEFAULT_WEIGHT,
                    categorize=lambda session: "general",
                )
            )
        except OSError as e:
            errors.append(str(e))
    return facts, errors


def trace_session(session_id: str) -> dict:
    """回查一个 omp session 的完整原始对话(dream trace 溯源用,不截断)。

    omp 的 session_id 对应一个 .jsonl 文件,但文件可能在 sessions/ 的某个子目录中。
    用 glob 递归匹配文件名尾部。
    """
    matches = [m for m in sorted(OMP_SESSIONS_DIR.glob(f"**/*{session_id}.jsonl"))
               if m.name != "__advisor.jsonl"]
    if not matches:
        return {
            "error": f"omp session 文件不存在: {session_id}",
            "session_id": session_id,
        }
    jsonl_path = matches[0]

    try:
        session_meta, messages = _load_events(jsonl_path)
    except OSError as e:
        return {"error": str(e), "session_id": session_id}

    if not messages:
        return {"source": "omp", "session_id": session_id, "session": session_meta, "messages": []}

    msgs_out: list[dict] = []
    for m in _rebuild_order(messages):
        msg = m.get("message", {}) if isinstance(m.get("message"), dict) else {}
        if not msg:
            continue
        msgs_out.append(
            {
                "role": msg.get("role", ""),
                "ts": str(m.get("timestamp") or msg.get("timestamp") or ""),
                "content": msg.get("content", ""),  # 原样保留,不做 [:1000]/[:2000] 截断
            }
        )
    return {
        "source": "omp",
        "session_id": session_id,
        "session": session_meta,
        "messages": msgs_out,
    }

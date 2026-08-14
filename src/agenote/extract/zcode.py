# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""zcode conversation extractor (SQLite, ~/.zcode/cli/db/db.sqlite).

Schema (verified ~/.zcode/cli/db/db.sqlite):
  session(id, title, directory, time_created, time_updated, project_id, ...)
  message(id, session_id, time_created, data JSON)
    data.role = 'user' | 'assistant'
    data.agent = agent name
    data.model.modelID = model identifier
  part(id, message_id, session_id, time_created, data JSON)
    data.type = 'text' | 'reasoning' | 'tool' | 'patch'

通过 agenote.extract.base.run_sqlite_extractor 共享通用 SQLite 抽取壳；
adapter 只提供差异：db 路径、source 名、categorize 启发式（比 opencode 多 "config"）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from agenote.extract import resolve_xdg_path
from agenote.extract.base import register, run_sqlite_extractor
from agenote.reconcile import RECONCILE_DEFAULT_WEIGHT

# Path resolution: env override → ~/.zcode/cli/db/db.sqlite
ZCODE_DB = Path(
    os.environ.get(
        "ZCODE_DB",
        str(resolve_xdg_path("ZCODE_DB", "~/.zcode/cli/db/db.sqlite")),
    )
).expanduser()


def _categorize(session_row: dict) -> str:
    """Derive category from session.title heuristics."""
    title = (session_row.get("title") or "").lower()
    if any(k in title for k in ("chat", "对话")):
        return "chat"
    if any(k in title for k in ("tool", "工具", "tooling")):
        return "tool"
    if any(k in title for k in ("fix", "bug", "修复", "错误")):
        return "fix"
    if any(k in title for k in ("config", "配置", "设置")):
        return "config"
    return "general"


@register("zcode")
def extract_zcode() -> tuple[list, list[str]]:
    """Extract all conversation turns from zcode db.sqlite (read-only)."""
    return run_sqlite_extractor(
        ZCODE_DB,
        source="zcode",
        weight=RECONCILE_DEFAULT_WEIGHT,
        categorize=_categorize,
    )


def trace_session(session_id: str) -> dict:
    """回查一个 session 的完整原始对话（dream trace 溯源用，不截断）。

    schema 与 opencode 一致（session/message/part），逻辑同 opencode.trace_session。
    三重只读保护复用 open_sqlite_ro。
    """
    from agenote.extract import open_sqlite_ro

    try:
        conn = open_sqlite_ro(ZCODE_DB)
    except FileNotFoundError as e:
        return {"error": str(e), "session_id": session_id}
    try:
        sess = conn.execute(
            "SELECT id, title, directory, time_created FROM session WHERE id = ?",
            (session_id,),
        ).fetchone()
        if sess is None:
            return {"error": f"session {session_id} 不存在", "session_id": session_id}
        messages_raw = conn.execute(
            "SELECT id, time_created, data FROM message " "WHERE session_id = ? ORDER BY time_created",
            (session_id,),
        ).fetchall()
        msgs: list[dict] = []
        for m in messages_raw:
            try:
                md = json.loads(m["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            parts_out: list[dict] = []
            parts = conn.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created",
                (m["id"],),
            ).fetchall()
            for p in parts:
                try:
                    pd = json.loads(p["data"])
                except (json.JSONDecodeError, TypeError):
                    continue
                ptype = pd.get("type", "")
                entry: dict = {"type": ptype}
                if ptype == "text":
                    entry["text"] = pd.get("text", "")
                elif ptype == "reasoning":
                    entry["text"] = pd.get("text", "")
                elif ptype == "tool":
                    entry["tool"] = pd.get("tool", "?")
                    entry["input"] = pd.get("input", {})
                elif ptype == "patch":
                    entry["files"] = pd.get("files", [])
                parts_out.append(entry)
            msgs.append(
                {
                    "role": md.get("role", ""),
                    "ts": str(m["time_created"] or ""),
                    "parts": parts_out,
                }
            )
        return {
            "source": "zcode",
            "session_id": session_id,
            "session": {
                "title": sess["title"],
                "directory": sess["directory"],
                "time_created": str(sess["time_created"] or ""),
            },
            "messages": msgs,
        }
    finally:
        conn.close()

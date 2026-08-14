# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""crush conversation extractor (SQLite, global + project-level DB scan).

Schema (verified ~/.config/crush/.crush/crush.db):
  sessions(id, title, parent_session_id, message_count, ...)
  messages(id, session_id, role, parts JSON list, model, created_at, ...)
    parts: [{"type": "text", "data": {"text": "..."}}, {"type": "finish", ...}]

与 opencode/zcode 的三表 schema 不同（crush 是 sessions/messages 两表 +
parts 内嵌 JSON 列），故不走 run_sqlite_extractor，但配对仍共享框架
pair_turns。adapter 提供：多 DB 发现、parts 解析、内容关键词分类。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agenote.extract import open_sqlite_ro, resolve_xdg_path
from agenote.extract.base import Turn, pair_turns, register
from agenote.extract.models import RECONCILE_DEFAULT_WEIGHT

CRUSH_GLOBAL_DB = resolve_xdg_path(
    "CRUSH_GLOBAL_DB", "~/.config/crush/.crush/crush.db"
)

CRUSH_SEARCH_ROOTS = [
    "~/Documents",
    "~/Documents/Repo",
    "~/Documents/Org",
    "~/.emacs.d",
    "/data/Documents",
]


def find_crush_dbs() -> list[Path]:
    """Scan all project-level Crush databases (dedup, skip nix store/Trash)."""
    found: set[Path] = {CRUSH_GLOBAL_DB}
    for root_str in CRUSH_SEARCH_ROOTS:
        root = Path(root_str).expanduser()
        if not root.exists():
            continue
        for db_path in root.rglob(".crush/crush.db"):
            spath = str(db_path)
            if "/nix/store/" not in spath and "/Trash/" not in spath:
                found.add(db_path)
    return sorted(found)


def _parts_to_text(parts_raw: str) -> str:
    """crush messages.parts 是 JSON list，逐项提取 type=text.data.text 拼接。

    Skip types: finish, tool_use, tool_result 等非纯文本 part。
    """
    if not parts_raw:
        return ""
    try:
        parts = json.loads(parts_raw)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("type") != "text":
            continue
        data = part.get("data") or {}
        if isinstance(data, dict):
            t = data.get("text", "")
            if t:
                texts.append(t)
    return "\n".join(texts).strip()


def _categorize(session_row: dict, user_text: str = "", assistant_text: str = "") -> str:
    """crush 分类基于对话内容关键词（user+assistant 拼合）。"""
    content = (user_text + " " + assistant_text or "").lower()[:200]
    if any(k in content for k in ("error", "fix", "bug", "错误", "修复")):
        return "fix"
    if any(k in content for k in ("tool", "工具")):
        return "tool"
    return "general"


def _iter_turns_crush(conn: Any, sess: Any, db_name: str, project_dir: str) -> Iterator[Turn]:
    """一个 crush session → Turn 流。

    session["id"] 带 db 文件名前缀，保证多 project DB 之间 session id 不碰撞
    （fact id 三段式 crush:{db}:{session}:{message} 与旧格式一致）。
    """
    # global DB 没有项目目录，directory 用 "crush-global" 作为 tag 代理值
    directory = project_dir if project_dir != "(global)" else "crush-global"
    session = {
        "id": f"{db_name}:{sess['id']}",
        "title": sess["title"] or "Untitled",
        "directory": directory,
    }
    messages = conn.execute(
        "SELECT id, role, parts, created_at FROM messages "
        "WHERE session_id = ? ORDER BY created_at",
        (sess["id"],),
    ).fetchall()
    for msg in messages:
        role = msg["role"] or ""
        if role not in ("user", "assistant"):
            continue
        yield Turn(
            role=role,
            text=_parts_to_text(msg["parts"] or ""),
            timestamp=str(msg["created_at"] or ""),
            native_id=str(msg["id"]),
            session=session,
        )


@register("crush")
def extract_crush() -> tuple[list, list[str]]:
    """Extract from all Crush databases (global + project-level)."""
    facts = []
    errors: list[str] = []
    for db_path in find_crush_dbs():
        try:
            conn = open_sqlite_ro(db_path)
        except FileNotFoundError as e:
            errors.append(str(e))
            continue
        try:
            if ".config/crush" in str(db_path):
                project_dir = "(global)"
            else:
                project_dir = str(db_path).rsplit("/.crush/", 1)[0]
            sessions = conn.execute(
                "SELECT id, title, created_at, updated_at FROM sessions"
            ).fetchall()
            for sess in sessions:
                try:
                    facts.extend(
                        pair_turns(
                            _iter_turns_crush(conn, sess, db_path.name, project_dir),
                            source="crush",
                            weight=RECONCILE_DEFAULT_WEIGHT,
                            categorize=_categorize,
                        )
                    )
                except Exception as e:  # 单个 session 失败不中断整库
                    errors.append(f"session={sess['id']}: {e}")
        finally:
            conn.close()
    return facts, errors

# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""codex conversation extractor (JSONL, history.jsonl + sessions/YYYY/MM/).

XDG path (from source/config.org:1837):
  CODEX_HOME = $XDG_CONFIG_HOME/codex = ~/.config/codex

Schema:
  - history.jsonl: each line {session_id, ts, text, cwd, ...}
    → builds session_id → {title, cwd, ts} index
  - sessions/YYYY/MM/rollout-*.jsonl: each line {timestamp, type, payload, ...}
    type ∈ session_meta | response_item ...
    response_item → payload.{type:"message", role, content:[...]}

消息顺序按 timestamp 排序（NOT parentId like omp）。配对/构造事实走框架
pair_turns；adapter 提供：history 索引、payload 解析、时间排序。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from agenote import config
from agenote.extract import resolve_xdg_path
from agenote.extract.base import Turn, pair_turns, register
from agenote.extract.models import RECONCILE_DEFAULT_WEIGHT

CODEX_HOME = resolve_xdg_path("CODEX_HOME", "$XDG_CONFIG_HOME/codex")
HISTORY_JSONL = CODEX_HOME / "history.jsonl"
SESSIONS_ROOT = CODEX_HOME / "sessions"

# codex 外部源：trust 0.5 → weight 0.6（略低于 hermes/omp；external_delta 默认 -0.1）
EXTERNAL_RECONCILE_WEIGHT = round(
    RECONCILE_DEFAULT_WEIGHT + float(config.get("weights", "external_delta")), 2
)


def _load_history_index() -> dict[str, dict]:
    """Build session_id → {title, cwd, ts} index from history.jsonl."""
    index: dict[str, dict] = {}
    if not HISTORY_JSONL.exists():
        return index
    try:
        with open(HISTORY_JSONL, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = entry.get("session_id", "")
                if sid and sid not in index:
                    index[sid] = {
                        "title": (entry.get("text", "Untitled") or "Untitled")[:80],
                        "cwd": entry.get("cwd", ""),
                        "ts": entry.get("ts", 0),
                    }
    except OSError:
        pass
    return index


def _normalize_message(msg) -> str:
    """Extract plain text from message which may be str, list of parts, or dict."""
    if isinstance(msg, str):
        return msg.strip()
    if isinstance(msg, list):
        texts: list[str] = []
        for part in msg:
            if isinstance(part, dict):
                if part.get("type") == "text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "input_text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(t for t in texts if t).strip()
    if isinstance(msg, dict):
        return _normalize_message(msg.get("content", msg.get("text", "")))
    return str(msg).strip()


def _ts_key(evt: dict) -> float:
    """timestamp（ms 或 ISO 字符串）→ 排序键；无法解析回退 0.0。"""
    ts = evt.get("timestamp", evt.get("ts", 0))
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            ts = ts.replace("Z", "+00:00")
            return datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            return 0.0
    return 0.0


def _iter_turns(jsonl_path: Path, history_idx: dict[str, dict]) -> Iterator[Turn]:
    """一个 rollout-*.jsonl → Turn 流（timestamp 排序后的 user/assistant 回合）。"""
    # Filename: rollout-<uuid>.jsonl → session_id = uuid
    session_id = jsonl_path.stem.replace("rollout-", "")
    meta = history_idx.get(session_id, {})

    events: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    events.sort(key=_ts_key)

    session_id_evt: str | None = None
    cwd_evt: str | None = None
    for i, evt in enumerate(events):
        etype = evt.get("type", "")
        payload = evt.get("payload") if isinstance(evt.get("payload"), dict) else {}

        if etype == "session_meta":
            sid_meta = payload.get("id", "")
            if sid_meta and session_id_evt is None:
                session_id_evt = sid_meta
            cwd_meta = payload.get("cwd", "")
            if cwd_meta and cwd_evt is None:
                cwd_evt = cwd_meta

        elif etype == "response_item" and payload.get("type") == "message":
            evt_ts = str(evt.get("timestamp", ""))
            yield Turn(
                role=payload.get("role", ""),
                text=_normalize_message(payload.get("content", "")),
                timestamp=evt_ts,
                native_id=evt_ts or str(i),
                session={
                    "id": session_id_evt or session_id,
                    "title": meta.get("title", "Untitled"),
                    "directory": cwd_evt or meta.get("cwd", ""),
                },
            )


@register("codex")
def extract_codex() -> tuple[list, list[str]]:
    """Extract from codex history + sessions/YYYY/MM (read-only)."""
    facts = []
    errors: list[str] = []
    if not CODEX_HOME.exists():
        return [], [f"CODEX_HOME 不存在: {CODEX_HOME}"]
    history_idx = _load_history_index()
    if SESSIONS_ROOT.exists():
        for jsonl_path in sorted(SESSIONS_ROOT.rglob("rollout-*.jsonl")):
            try:
                facts.extend(
                    pair_turns(
                        _iter_turns(jsonl_path, history_idx),
                        source="codex",
                        weight=EXTERNAL_RECONCILE_WEIGHT,
                        categorize=lambda session, user_text, assistant_text: "general",
                    )
                )
            except OSError as e:
                errors.append(str(e))
    return facts, errors

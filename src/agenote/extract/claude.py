# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""claude code conversation extractor (JSONL, transcripts/ses_*.jsonl).

Dual-XDG paths (verified, important!):
  CLAUDE_CONFIG_DIR = $XDG_CONFIG_HOME/claude   -- stores config/settings/skills
  transcripts: $XDG_DATA_HOME/claude/transcripts/  -- **NOT under CLAUDE_CONFIG_DIR**

Schema: transcripts/ses_<hex>_<base64>.jsonl, each line {type, timestamp, ...}
  type ∈ user | tool_use | tool_result (no 'assistant' event type!)

  user:        {type, timestamp, content}  — user message (may include system prompts)
  tool_use:    {type, timestamp, tool_name, tool_input}  — assistant's tool call
  tool_result: {type, timestamp, tool_name, tool_input, tool_output}  — result

claude 没有显式 assistant 事件：一个 user 后跟的 tool_use(s)+tool_result(s)
序列在本模块合成为**伪 assistant Turn**，再交给框架 pair_turns 配对。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from agenote.extract import extract_title, resolve_xdg_path
from agenote.extract.base import Turn, pair_turns, register
from agenote.extract.models import RECONCILE_DEFAULT_WEIGHT

# IMPORTANT: transcripts lives under XDG_DATA_HOME, NOT CLAUDE_CONFIG_DIR
CLAUDE_TRANSCRIPTS_DIR = resolve_xdg_path(
    "CLAUDE_TRANSCRIPTS_DIR",
    "$XDG_DATA_HOME/claude/transcripts",
)

# claude 外部源：trust 0.5 → weight 0.6
EXTERNAL_RECONCILE_WEIGHT = round(RECONCILE_DEFAULT_WEIGHT - 0.1, 2)


def _normalize_message(msg) -> str:
    """Extract text from claude message (str, list of parts with type=text/tool_use/tool_result)."""
    if isinstance(msg, str):
        return msg.strip()
    if isinstance(msg, list):
        texts: list[str] = []
        for part in msg:
            if isinstance(part, dict):
                ptype = part.get("type", "")
                if ptype == "text":
                    texts.append(part.get("text", ""))
                elif ptype == "tool_use":
                    tool_name = part.get("name", "unknown")
                    tool_input = json.dumps(
                        part.get("input", {}), ensure_ascii=False
                    )[:300]
                    texts.append(f"[tool_use: {tool_name}] {tool_input}")
                elif ptype == "tool_result":
                    content = part.get("content", "")
                    if isinstance(content, str):
                        texts.append(f"[tool_result] {content[:500]}")
                    else:
                        texts.append(
                            f"[tool_result] {json.dumps(content, ensure_ascii=False)[:500]}"
                        )
            elif isinstance(part, str):
                texts.append(part)
        return "\n".join(t for t in texts if t).strip()
    if isinstance(msg, dict):
        return _normalize_message(msg.get("content", msg.get("text", "")))
    return str(msg).strip()


def _iter_turns(jsonl_path: Path) -> Iterator[Turn]:
    """一个 ses_*.jsonl → Turn 流（tool 序列合成伪 assistant Turn）。

    user 事件开启新对；其间累积的 tool_use/tool_result 在下一个 user 或
    文件结束时 flush 为一个 assistant Turn。没有 tool 调用的 user 消息
    不产 assistant Turn，配对自然跳过（对齐旧 _flush_pair 语义）。
    """
    session_id = jsonl_path.stem

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

    # 第一条 user 消息做 session 级标题（逐对标题提取的 fallback）
    session_title = "Untitled"
    for evt in events:
        if evt.get("type") == "user":
            text = _normalize_message(evt.get("message", evt.get("content", "")))
            if text:
                session_title = extract_title(text) or "Untitled"
                break

    session = {
        "id": session_id,
        "title": session_title,
        "directory": "claude-code",  # transcript 无目录信息，作为 tag 代理值
    }

    def _make_assistant_turn(tool_calls: list[dict], tool_results: list[str]) -> Turn | None:
        """把累积的 tool_use/tool_result 合成为伪 assistant Turn。"""
        if not tool_calls:
            return None
        parts: list[str] = []
        for tc in tool_calls:
            tool_name = tc.get("tool_name", "unknown")
            tool_input = json.dumps(tc.get("tool_input", {}), ensure_ascii=False)[:300]
            parts.append(f"[tool_use: {tool_name}] {tool_input}")
        if tool_results:
            parts.append("\n".join(f"[tool_result] {r[:500]}" for r in tool_results))
        ts = tool_calls[0].get("timestamp", "")
        return Turn(role="assistant", text="\n".join(parts), timestamp=str(ts), native_id=str(ts), session=session)

    tool_calls: list[dict] = []
    tool_results: list[str] = []
    for evt in events:
        etype = evt.get("type", "")
        if etype == "user":
            if tool_calls:  # 上一对的工具序列 flush 为 assistant 回合
                turn = _make_assistant_turn(tool_calls, tool_results)
                if turn:
                    yield turn
                tool_calls, tool_results = [], []
            yield Turn(
                role="user",
                text=_normalize_message(evt.get("message", evt.get("content", ""))),
                timestamp=str(evt.get("timestamp", "")),
                native_id=str(evt.get("timestamp", "")),
                session=session,
            )
        elif etype == "tool_use":
            tool_calls.append(evt)
        elif etype == "tool_result":
            output = evt.get("tool_output", "")
            if not isinstance(output, str):
                output = json.dumps(output, ensure_ascii=False)
            tool_results.append(output)

    if tool_calls:  # 文件结尾 flush 最后一对
        turn = _make_assistant_turn(tool_calls, tool_results)
        if turn:
            yield turn


@register("claude")
def extract_claude() -> tuple[list, list[str]]:
    """Extract from claude transcripts/ (read-only)."""
    facts = []
    errors: list[str] = []
    if not CLAUDE_TRANSCRIPTS_DIR.exists():
        return [], [f"transcripts dir 不存在: {CLAUDE_TRANSCRIPTS_DIR}"]
    for jsonl_path in sorted(CLAUDE_TRANSCRIPTS_DIR.glob("ses_*.jsonl")):
        try:
            facts.extend(
                pair_turns(
                    _iter_turns(jsonl_path),
                    source="claude",
                    weight=EXTERNAL_RECONCILE_WEIGHT,
                    categorize=lambda session, user_text, assistant_text: "general",
                )
            )
        except OSError as e:
            errors.append(str(e))
    return facts, errors

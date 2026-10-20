# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.inject — 状态化注入决策（追加型注入器的单一实现）。

背景：指纹缓存、recall 门槛、单会话累计预算与状态文件这四件事，原先在
`injectors/lib.sh`（bash）、`agenote-zcode/hooks/lib.mjs`（JS）、
`agenote-hermes/__init__.py`（Python）各实现一遍（ADR 0005 记录的复制债）。
本模块把它们收成 CLI 的一个子命令，宿主插件只负责「读事件 → 调本命令 →
把 content 封给宿主协议」。

语义（与迁移前的四个实现逐条对齐）：

- `--event session`：会话/compact 边界。清累计与 recall 键；简报按 KB 指纹
  缓存重放（指纹未变零内容生成），空结果不缓存（开关转开后立即生效）。
- `--event prompt`：每轮通道。三件套——①指纹 + query 未变不重注（含被分数
  下限滤空的情况）②短 prompt 跳过 ③单会话累计触顶停 recall（顺带记键防
  重复生成）。query = prompt 折叠空白取前 200 字符 + 当前目录 basename 伪词。
- 状态文件：`${AGENOTE_INJECTOR_STATE_DIR:-~/.cache/agenote/injectors}/<host>-<sid>.json`
  （session_id 取不到退化为 `<host>.json`；整目录可清理，无副作用）。
- 语义开关仍由 `context` 裁决（总开关 / per-host）：disabled 或 empty 时
  content 零字节。

输出：`--format json` 给 `{status, action, content, cached}`；text 只写 content
（非 inject 时零字节，宿主零分支）。任何异常都不应让注入器崩溃——调用方
按「空输出 = 不注入」降级。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

from agenote import config
from agenote.context import build_context_result

# prompt 注入的固定标记：带它的消息是插件自注入回声，整轮跳过（断反馈环）。
HOOK_MARKER = "<agenote-hook>"
# query 取 prompt 前 N 字符（设计 C4 规定值；跨宿主一致，不是每宿主预算）。
QUERY_MAX_CHARS = 200


def _state_dir() -> Path:
    env = os.environ.get("AGENOTE_INJECTOR_STATE_DIR")
    if env:
        return Path(env).expanduser()
    cache = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(cache).expanduser() / "agenote" / "injectors"


def _state_path(host: str, session_id: str) -> Path:
    # session_id 来自宿主钩子（不可信输入）：只留字母数字与 `-_`，去掉路径语义。
    fields = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:64]
    return _state_dir() / (f"{host}-{fields}.json" if fields else f"{host}.json")


def _read_state(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 临时名必须唯一：同一会话的 session/prompt 事件可能并发写同一文件，
        # 固定 `.tmp` 会让两个进程互相截断、丢一次更新。
        fd, tmp = tempfile.mkstemp(
            dir=path.parent, prefix=path.name + ".", suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, ensure_ascii=False)
            os.replace(tmp, path)
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        # 状态写失败宁可下轮重复注入，也不中断本轮。
        pass


def _safe_int(value, fallback: int = 0) -> int:
    """状态文件可能被外部改坏；坏值按 0 处理，不让注入器整轮崩掉。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _fingerprint(ctx) -> str:
    """KB 指纹：MEMORY.org 与 memories/ 的 mtime_ns+size；缺失返回空串。

    与 pi/opencode/dsh 的宿主侧缓存口径一致；这里是**唯一实现**，插件侧的
    同名函数只作为「省一次 spawn」的镜像（pi/opencode 重写型）。
    """
    mem = getattr(ctx, "memory_org", None)
    try:
        st = mem.stat()
    except (OSError, AttributeError):
        return ""
    fp = f"{st.st_mtime_ns}:{st.st_size}"
    memories = getattr(ctx, "memories", None)
    try:
        dst = memories.stat()
        fp += f"|{dst.st_mtime_ns}:{dst.st_size}"
    except (OSError, AttributeError):
        fp += "|-"
    return fp


def _context_args(host: str, mode: str, budget: int | None, query: str | None = None):
    return argparse.Namespace(
        mode=mode, query=query, project=None, types=None,
        host=host, format="json", budget=budget,
    )


def _emit(args, action: str, status: str, content: str, *, cached: bool = False) -> None:
    if getattr(args, "format", "text") == "json":
        print(json.dumps(
            {"status": status, "action": action, "content": content, "cached": cached},
            ensure_ascii=False,
        ))
        return
    sys.stdout.write(content)


def _config_int(section: str, key: str, fallback: int) -> int:
    try:
        return int(str(config.get(section, key)))
    except (TypeError, ValueError):
        return fallback


def _build_query(prompt: str) -> str | None:
    """prompt 折叠空白取前 200 字符 + cwd basename；短门槛不过返回 None。"""
    q_text = " ".join((prompt or "").split())[:QUERY_MAX_CHARS]
    min_query = _config_int("injection", "recall_min_query", 6)
    if len(q_text.strip()) < min_query:
        return None
    pseudo = Path.cwd().name
    return f"{q_text} {pseudo}".strip() if pseudo and pseudo != "." else q_text


def _session_event(args, ctx, state: dict, path: Path) -> None:
    # 会话边界无条件重置：即使 KB 暂时不可用（指纹为空），也不能把上一会话的
    # 累计与去重键带进下一会话——否则 KB 恢复后同 query 会被误判 duplicate。
    state.pop("recall_key", None)
    state["cumulative"] = 0

    fingerprint = _fingerprint(ctx)
    if not fingerprint:
        # KB 缺失：不生成内容、不缓存简报（建好后立即生效）；重置仍照常落盘。
        _write_state(path, state)
        _emit(args, "skip", "empty", "")
        return

    cached_brief = state.get("brief_content")
    if (state.get("brief_key") == fingerprint
            and isinstance(cached_brief, str) and cached_brief):
        _write_state(path, state)
        _emit(args, "inject", "ok", cached_brief, cached=True)
        return

    res = build_context_result(_context_args(args.host, "session", args.budget), ctx)
    content = res["content"] if res["status"] == "ok" else ""
    if content:
        state["brief_key"] = fingerprint
        state["brief_content"] = content
    else:
        # 空/关闭不缓存：开关从 disabled 转开后立即生效，不必等指纹变化。
        state.pop("brief_key", None)
        state.pop("brief_content", None)
    _write_state(path, state)
    _emit(args, "inject" if content else "skip", res["status"], content)


def _prompt_event(args, ctx, state: dict, path: Path) -> None:
    prompt = args.prompt or ""
    if not prompt or HOOK_MARKER in prompt:
        _emit(args, "skip", "skip", "")
        return

    query = _build_query(prompt)
    if query is None:  # 三件套②：短 prompt 不值得注入
        _emit(args, "skip", "short", "")
        return

    fingerprint = _fingerprint(ctx)
    if not fingerprint:
        # KB 缺失：指纹无意义，直接跳过（与迁移前注入器的预检一致）。
        _emit(args, "skip", "empty", "")
        return
    key = f"{fingerprint}::{query}"
    if state.get("recall_key") == key:  # 三件套①：指纹+query 未变（含滤空）
        _emit(args, "skip", "duplicate", "")
        return

    cumulative = _safe_int(state.get("cumulative"))
    limit = _config_int("injection", "session_cumulative_budget", 24000)
    if cumulative >= limit:  # 三件套③：触顶停 recall，记键防重复生成
        state["recall_key"] = key
        _write_state(path, state)
        _emit(args, "skip", "budget", "")
        return

    res = build_context_result(
        _context_args(args.host, "recall", args.budget, query), ctx,
    )
    content = res["content"] if res["status"] == "ok" else ""
    state["recall_key"] = key
    if content:
        state["cumulative"] = cumulative + len(content)
    _write_state(path, state)  # 滤空也落账：同 query 下轮零生成
    _emit(args, "inject" if content else "skip", res["status"], content)


def cmd_inject(args: argparse.Namespace, ctx=None) -> None:
    """`agenote inject`：状态化注入决策；text 非 inject 态零字节。"""
    from agenote.core import agenote_context

    ctx = ctx or agenote_context()
    host = (getattr(args, "host", None) or "generic").lower()
    path = _state_path(host, getattr(args, "session_id", "") or "")
    state = _read_state(path)
    if getattr(args, "event", "session") == "prompt":
        _prompt_event(args, ctx, state, path)
    else:
        _session_event(args, ctx, state, path)

# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.extract — cross-agent conversation extraction common layer.

Provide XDG-aware path resolution, SQLite read-only open helper, title extractor.
Each source (opencode/zcode/omp/crush/codex/claude/hermes) lives in its own file.

全部 7 个 adapter 均已通过 @register 注册到 agenote.extract.base.SOURCES；
SOURCES 是 extract 编排 / reconcile / dream trace 三条分发路径的唯一真相源。
编排（run_extract）与分发表（_resolve_extractors）由 base.py 拥有。
本模块保留三个公共 helper 并 re-export 编排函数，
保持对外接口（from agenote.extract import run_extract）不变。
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from agenote import config

# 从 base.py re-export（run_extract 是 extract 子命令/ag_note_extract MCP tool 的单一真相源）
from agenote.extract.base import _resolve_extractors, run_extract

# ── XDG-aware path resolution ──────────────────────────────────


def resolve_xdg_path(env_var: str, default: str) -> Path:
    """Resolve path respecting env var, config file, then XDG base dirs.

    Lookup order:
      1. os.environ[env_var] (direct override)
      2. config.toml [extract.sources].<env_var.lower()>（替代 default）
      3. $XDG_DATA_HOME / $XDG_CONFIG_HOME from default placeholder
      4. expanduser fallback (~/...)

    default may use $XDG_DATA_HOME/$XDG_CONFIG_HOME placeholders:
        resolve_xdg_path('CODEX_HOME', '$XDG_CONFIG_HOME/codex')
    """
    val = os.environ.get(env_var)
    if val:
        return Path(val).expanduser()

    # config.toml 覆盖层：键名 = env var 的小写形式（如 OPENCODE_DB → opencode_db）。
    # 展开复用 config._expand（$XDG_* 占位符未设 env 时回落规范默认，与 get_path 一致）。
    cfg_val = config.get("extract.sources", env_var.lower())
    if isinstance(cfg_val, str) and cfg_val and cfg_val != default:
        return config._expand(cfg_val)

    # Resolve $XDG_*_HOME placeholders in default
    m = re.match(r"\$XDG_(\w+)_HOME", default)
    if m:
        xdg_key = f"XDG_{m.group(1)}_HOME"
        xdg_val = os.environ.get(xdg_key)
        if xdg_val:
            remainder = default.split("/", 1)[1] if "/" in default else ""
            return Path(xdg_val) / remainder

    return Path(default.replace("~", str(Path.home()))).expanduser()


# ── SQLite read-only open (triple protection) ─────────────────


def open_sqlite_ro(db_path: Path) -> sqlite3.Connection:
    """Triple read-only SQLite protection:
    1. file: URI + mode=ro (SQLite layer rejects writes)
    2. PRAGMA query_only = 1 (connection layer rejects DML/DDL)
    3. Caller never constructs write statements (convention)
    """
    if not db_path.exists():
        raise FileNotFoundError(f"DB 不存在: {db_path}")
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = 1")
    return conn


# ── Title extraction helper ───────────────────────────────────

_TITLE_BRACKET_RE = re.compile(r"^【([^】]+)】")
_TITLE_SENT_RE = re.compile(r"[^。\n!?:;]+")
TITLE_MAX_LEN = int(config.get("extract", "title_max_len"))


def extract_title(content: str, max_len: int = TITLE_MAX_LEN) -> str:
    """Unified title extractor: 【...】 bracket → first sentence → truncate."""
    m = _TITLE_BRACKET_RE.match(content.strip())
    if m:
        return m.group(1).strip()[:max_len]
    first = _TITLE_SENT_RE.match(content.strip())
    raw = (first.group(0).strip() if first else content.strip())[:40]
    return raw or "(untitled)"

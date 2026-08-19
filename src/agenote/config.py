# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

"""agenote.config — 配置层：SCHEMA 单一真相源 + 三层解析。

优先级：环境变量 > config.toml > SCHEMA 内置默认值。

- SCHEMA 定义全部配置键（默认值、可选 env 覆盖、注释），同时驱动：
  各模块常量初始化（config.get）、`agenote config init` 模板生成
  （render_template）、`agenote config show` 展示（iter_resolved）。
- 配置文件位置：$XDG_CONFIG_HOME/agenote/config.toml（默认 ~/.config/agenote/）。
- 刻意不进配置（代码即配置/跨工具契约）：CARD_TEMPLATES、NOISE_MARKERS、停用词表、
  VALID_* 枚举、域内子目录名（experiences/memories/…）、纯展示层截断、
  health 报告的 recent_7d/30d 窗口、标题首句截断（[:40]）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

try:
    import tomllib  # Python >= 3.11
except ModuleNotFoundError:  # Python 3.10（pyproject 声明 tomli 条件依赖）
    import tomli as tomllib

CONFIG_PATH = (
    Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    / "agenote"
    / "config.toml"
)


@dataclass(frozen=True)
class Key:
    """一个配置键的定义：默认值（同时是类型模板）、env 覆盖名、模板注释。

    default 为 callable 时在取值/渲染时求值（用于依赖其他键的动态默认）。
    """

    default: object
    env: str = ""
    comment: str = ""


# ═══════════════════════════════════════════════════════════════════════════════
# SCHEMA — 全部配置键的单一真相源
# ═══════════════════════════════════════════════════════════════════════════════

SCHEMA: dict[str, dict[str, Key]] = {
    "paths": {
        "kb_root": Key(
            "~/Documents/Org", env="KB_ROOT", comment="知识库根目录（卡片/MEMORY/索引都住这）"
        ),
        "agenote_dir": Key("agenote", comment="agent 域子目录名（KB_ROOT 下）"),
        "conversations_root": Key("", comment="extract 对话输出目录（空 = KB_ROOT/conversations）"),
        "distill_dir": Key("", comment="distill skill 草稿目录（空 = agent 域/.distill）"),
        "reconcile_dir": Key("", comment="reconcile 只读事实索引目录（空 = agent 域/.reconcile）"),
        "viz_output": Key("", comment="viz 可视化输出文件（空 = KB_ROOT/kb-viz.html）"),
    },
    "agent": {
        "default_name": Key(
            "omp", env="AGENOTE_AGENT", comment="卡片 SOURCE_AGENT 默认写入者标签"
        ),
    },
    "weights": {
        "human_default": Key(1.5, comment="人类域卡片默认检索权重"),
        "agent_default": Key(1.0, comment="agent 域卡片默认检索权重"),
        "usage_bonus": Key(0.1, comment="每次 touch 的权重提升系数"),
        "usage_cap": Key(10, comment="usage 计数封顶（bonus×cap = 最大加成）"),
        "stale_penalty": Key(0.8, comment="超 stale_days 未用的权重惩罚系数"),
        "weight_epsilon": Key(0.001, comment="权重变化判定 epsilon（curate 跳过无变化卡片）"),
        "reconcile_default": Key(0.7, comment="reconcile 自家 agent 源基准权重"),
        "external_delta": Key(-0.1, comment="外部 reconcile 源相对基准的偏移"),
        "default_trust": Key(0.5, comment="对话型源默认 trust 分"),
        "hermes_weight_cap": Key(1.0, comment="hermes trust→weight 公式封顶"),
        "score_term_hit": Key(100, comment="检索评分：每命中词得分"),
        "score_title_bonus": Key(25, comment="检索评分：标题命中加分"),
        "score_phrase_bonus": Key(50, comment="检索评分（单域）：短语命中加分"),
        "dedup_category_bonus": Key(0.15, comment="去重相似度：category 相同加成"),
        "dedup_tech_bonus": Key(0.1, comment="去重相似度：tech 相同加成"),
    },
    "health": {
        "card_stale_days": Key(90, comment="gaps 时间维陈旧阈值（天）"),
        "isolated_warn": Key(15, comment="孤立率 warn 阈值（%）"),
        "isolated_bad": Key(25, comment="孤立率 bad 阈值（%）"),
        "stale_warn": Key(10, comment="过时率 warn 阈值（%）"),
        "stale_bad": Key(20, comment="过时率 bad 阈值（%）"),
        "skew_warn": Key(45, comment="类型偏斜 warn 阈值（%）"),
        "skew_bad": Key(60, comment="类型偏斜 bad 阈值（%）"),
        "weak_category_min": Key(3, comment="health 薄弱类别判定（卡片数 < 此值）"),
        "gaps_weak_max": Key(2, comment="gaps 薄弱类别判定（卡片数 <= 此值；与上刻意不同）"),
    },
    "curation": {
        "stale_days": Key(30, comment="memory/卡片「陈旧」统一阈值（天）"),
        "archive_days": Key(90, comment="stale→archived 状态机阈值（天，按 LAST_VERIFIED）"),
        "memory_archive_days": Key(60, comment="feedback stale→MEMORY-ARCHIVE.org 阈值（天）"),
        "project_curate_days": Key(60, comment="memory --auto-update 项目建议策展阈值（天）"),
        "dedup_threshold": Key(0.7, comment="去重相似度阈值（0-1，越高越严）"),
    },
    "dream": {
        "min_term_freq": Key(5, comment="候选词频下限"),
        "default_limit": Key(5, comment="dream 默认返回候选数"),
        "window_days": Key(90, comment="dream 回看窗口（天）"),
        "min_term_len": Key(3, comment="关键词最短长度（ASCII 词）"),
        "min_cjk_len": Key(2, comment="关键词最短长度（CJK 词）"),
        "morph_hyphen_bonus": Key(2.0, comment="形态学评分：连字符词加成"),
        "morph_longascii_bonus": Key(1.0, comment="形态学评分：长 ASCII 词加成"),
        "morph_cjk2_penalty": Key(0.4, comment="形态学评分：2 字 CJK 词惩罚"),
        "min_valid_year": Key(2020, comment="时间戳有效年份下限"),
        "tf_cap_per_fact": Key(3, comment="单事实词频截断（TF 上限）"),
    },
    "distill": {
        "min_cluster_size": Key(2, comment="聚类最小卡片数"),
        "min_usage_for_ascend": Key(2, comment="反复使用判定（USAGE_COUNT 下限）"),
        "window_days": Key(30, comment="distill 回看窗口（天）"),
    },
    "extract": {
        "date_offset_days": Key(1, comment="默认抽取偏移（1 = 抽昨天）"),
        "limit": Key(500, comment="每源抽取上限（0 = 不限）"),
        "trunc_user": Key(1000, comment="索引层 user 正文截断（trace 回查不截断的前提，勿轻易改小）"),
        "trunc_assistant": Key(2000, comment="索引层 assistant 正文截断"),
        "trunc_reasoning": Key(200, comment="reasoning 段截断"),
        "trunc_tool_input": Key(300, comment="tool_use input 截断"),
        "trunc_tool_result": Key(500, comment="tool_result 截断"),
        "trunc_org_render": Key(3000, comment="Org 渲染正文截断"),
        "title_max_len": Key(60, comment="抽取标题最大长度"),
    },
    "extract.sources": {
        # 键名 = 对应 env var 的小写形式；配置值替代各源的 XDG 默认路径
        "opencode_db": Key(
            "~/.local/share/opencode/opencode-stable.db",
            env="OPENCODE_DB",
            comment="opencode SQLite 源",
        ),
        "zcode_db": Key("~/.zcode/cli/db/db.sqlite", env="ZCODE_DB", comment="zcode SQLite 源"),
        "omp_sessions_dir": Key(
            "$XDG_CONFIG_HOME/omp/sessions", env="OMP_SESSIONS_DIR", comment="omp JSONL 会话目录"
        ),
        "claude_transcripts_dir": Key(
            "$XDG_DATA_HOME/claude/transcripts", env="CLAUDE_TRANSCRIPTS_DIR",
            comment="claude transcripts 目录",
        ),
        "codex_home": Key(
            "$XDG_CONFIG_HOME/codex", env="CODEX_HOME", comment="codex 主目录（含 history.jsonl 与 sessions/）"
        ),
        "crush_global_db": Key(
            "~/.config/crush/.crush/crush.db", env="CRUSH_GLOBAL_DB", comment="crush 全局库"
        ),
        "crush_search_roots": Key(
            ["~/Documents", "~/Documents/Repo", "~/Documents/Org", "~/.emacs.d", "/data/Documents"],
            comment="项目级 crush.db 扫描根列表",
        ),
        "hermes_db": Key(
            "~/.local/share/hermes/memory_store.db", env="HERMES_DB", comment="hermes 事实库"
        ),
    },
    "reconcile": {
        "min_fact_len": Key(15, comment="事实最短长度（更短视为噪声，dream 同用）"),
        "noise_scan_chars": Key(250, comment="噪声标记扫描窗口（USER 提问区字符数）"),
        "report_items": Key(10, comment="reconcile 报告摘要条数"),
        "report_items_all": Key(5, comment="--all 合并报告每源条数"),
    },
    "search": {
        "limit": Key(20, comment="检索结果默认上限"),
        "context_lines": Key(3, comment="命中行上下文行数"),
        "max_blocks": Key(4, comment="每卡片最多展示命中块数"),
        "snippet_max_chars": Key(200, comment="片段截断字符数"),
        "snippet_context_lines": Key(2, comment="片段窗口行数"),
    },
    "add": {
        "default_category": Key("general", comment="新卡片默认 category"),
        "default_type": Key("workflow", comment="新卡片默认 type"),
        "default_owner": Key("ai", comment="新卡片默认 owner"),
    },
    "commit": {
        "curated_paths": Key(
            lambda: [  # 动态默认：跟随 paths.agenote_dir
                "agenote/experiences",
                "agenote/index.json",
                "agenote/.reconcile",
                "conversations",
                "kb-viz.html",
                "MEMORY.org",
                "agenote/MEMORY.org",
                "agenda",
            ],
            comment="agenote commit 精准 add 的策展产物路径清单（相对 git 仓库根）",
        ),
    },
    "viz": {
        "port": Key(8765, comment="viz serve 端口"),
        "theme": Key("auto", comment="viz 主题（light/dark/auto）"),
        "top_techs_limit": Key(8, comment="技术栈 top N"),
        "serve_probe_timeout": Key(5.0, comment="serve 就绪探测超时（秒）"),
        "serve_probe_interval": Key(0.1, comment="serve 就绪探测间隔（秒）"),
    },
}

# 动态默认依赖的键（curated_paths 前缀跟随 agenote_dir）；渲染/取值时替换
_DYNAMIC_PREFIX = {"commit": {"curated_paths": ("paths", "agenote_dir")}}


# ═══════════════════════════════════════════════════════════════════════════════
# 解析 — env > file > default
# ═══════════════════════════════════════════════════════════════════════════════

_MISSING = object()
_file_config: dict | None = None  # 进程内缓存（CLI 单次运行，无失效问题）


def _load_file() -> dict:
    """读取并缓存 config.toml（保留 tomllib 原始嵌套结构）；坏 TOML 直接退出。"""
    global _file_config
    if _file_config is None:
        try:
            with open(CONFIG_PATH, "rb") as f:
                data = tomllib.load(f)
        except FileNotFoundError:
            data = {}
        except tomllib.TOMLDecodeError as e:
            print(f"错误: 配置文件解析失败 {CONFIG_PATH}: {e}", file=sys.stderr)
            sys.exit(1)
        _warn_unknown_keys(data)
        _file_config = data
        _validate_types()
    return _file_config


def _validate_types() -> None:
    """文件值与 SCHEMA 默认值类型不符时报键名并退出（避免消费点裸 traceback）。

    float 键宽容 int 值（TOML 的 7 与 7.0 等价）；bool 是 int 子类，先判 bool。
    """
    for section, keys in SCHEMA.items():
        for key, spec in keys.items():
            val = _file_value(section, key)
            if val is _MISSING:
                continue
            expected = _resolve_default(section, key)
            if isinstance(expected, bool):
                ok = isinstance(val, bool)
            elif isinstance(expected, int):
                ok = isinstance(val, int) and not isinstance(val, bool)
            elif isinstance(expected, float):
                ok = isinstance(val, (int, float)) and not isinstance(val, bool)
            elif isinstance(expected, (str, list)):
                ok = isinstance(val, type(expected))
            else:
                ok = True
            if not ok:
                print(
                    f"错误: 配置键 [{section}].{key} 类型应为 "
                    f"{type(expected).__name__}，得到 {type(val).__name__}（{val!r}）",
                    file=sys.stderr,
                )
                sys.exit(1)


def _file_value(section: str, key: str) -> object:
    """在嵌套 dict 里按点号节名逐层下钻取键；不存在返回 _MISSING。

    TOML 的 [extract.sources] 会被 tomllib 解析成 {"extract": {"sources": {...}}}，
    故 "extract.sources" 类节名需逐层查找而非顶层键。
    """
    node: object = _load_file()
    for part in section.split("."):
        if not isinstance(node, dict):
            return _MISSING
        node = node.get(part, _MISSING)
    if isinstance(node, dict):
        return node.get(key, _MISSING)
    return _MISSING


def _warn_unknown_keys(data: dict) -> None:
    """文件里存在 SCHEMA 未定义的节/键时警告（提示拼写错误），不阻塞。

    子表（如 [extract.sources]）视为嵌套节递归检查：完整路径是已知节则继续，
    否则按未知节警告。
    """

    def _walk(node: dict, path: str) -> None:
        for key, val in node.items():
            full = f"{path}.{key}" if path else key
            if isinstance(val, dict):
                if full in SCHEMA:
                    _walk(val, full)
                else:
                    print(f"警告: 配置文件存在未知节 [{full}]（已忽略）", file=sys.stderr)
            elif not path:
                print(f"警告: 配置文件存在顶层散键 {key}（已忽略）", file=sys.stderr)
            elif path not in SCHEMA:
                print(f"警告: 配置文件存在未知节 [{path}]（已忽略）", file=sys.stderr)
            elif key not in SCHEMA[path]:
                print(
                    f"警告: 配置文件存在未知键 [{path}].{key}（已忽略）", file=sys.stderr
                )

    _walk(data, "")


def _resolve_default(section: str, key: str) -> object:
    """求键默认值；callable 默认在此时求值（动态默认）。"""
    default = SCHEMA[section][key].default
    if callable(default):
        dep_sec, dep_key = _DYNAMIC_PREFIX[section][key]
        prefix = str(get(dep_sec, dep_key))
        return [p.replace("agenote", prefix) if p.startswith("agenote") else p for p in default()]
    return default


def get(section: str, key: str) -> object:
    """取配置键的生效值（env > file > default）。"""
    spec = SCHEMA[section][key]
    if spec.env:
        val = os.environ.get(spec.env)
        if val:
            return val
    file_val = _file_value(section, key)
    if file_val is not _MISSING:
        return file_val
    return _resolve_default(section, key)


def get_path(section: str, key: str) -> Path:
    """取路径型配置：展开 ~ 与 $XDG_*_HOME 占位符，返回 Path。"""
    return _expand(str(get(section, key)))


def _expand(val: str) -> Path:
    """展开 $XDG_CONFIG_HOME/$XDG_DATA_HOME 占位符与 ~。"""
    for xdg_key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        placeholder = f"${xdg_key}"
        if val.startswith(placeholder):
            base = os.environ.get(xdg_key) or {
                "XDG_CONFIG_HOME": str(Path.home() / ".config"),
                "XDG_DATA_HOME": str(Path.home() / ".local" / "share"),
            }[xdg_key]
            return Path(base) / val[len(placeholder):].lstrip("/")
    return Path(val).expanduser()


def origin(section: str, key: str) -> str:
    """标注键当前生效值的来源（env / file / default），供 config show。"""
    spec = SCHEMA[section][key]
    if spec.env and os.environ.get(spec.env):
        return f"env {spec.env}"
    if _file_value(section, key) is not _MISSING:
        return "file"
    return "default"


def iter_resolved() -> list[tuple[str, object, str]]:
    """遍历 SCHEMA，产出 (section.key, 生效值, 来源)，供 config show。"""
    rows = []
    for section, keys in SCHEMA.items():
        for key in keys:
            rows.append((f"{section}.{key}", get(section, key), origin(section, key)))
    return rows


# ═══════════════════════════════════════════════════════════════════════════════
# 模板渲染 — config init
# ═══════════════════════════════════════════════════════════════════════════════


def _toml_repr(val: object) -> str:
    """标量/列表/内联表的 TOML 字面量渲染。"""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        return '"' + val.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(val, list):
        return "[" + ", ".join(_toml_repr(v) for v in val) + "]"
    if isinstance(val, dict):
        return "{ " + ", ".join(f"{k} = {_toml_repr(v)}" for k, v in val.items()) + " }"
    raise TypeError(f"无法渲染为 TOML: {type(val)}")


def render_template() -> str:
    """从 SCHEMA 生成带注释的配置模板（键全部注释掉，取消注释即启用）。"""
    lines = [
        "# agenote 配置文件",
        "# 优先级：环境变量 > 本文件 > 内置默认值（各键注释标注默认值）",
        "# 查看当前生效配置: agenote config show",
        f"# 由 agenote config init 生成于 {datetime.now().strftime('%Y-%m-%d')}",
        "",
    ]
    for section, keys in SCHEMA.items():
        lines.append(f"[{section}]")
        for key, spec in keys.items():
            env_note = f"；env {spec.env} 优先" if spec.env else ""
            default_repr = _toml_repr(_resolve_default(section, key))
            lines.append(f"# {spec.comment}（默认 {default_repr}{env_note}）")
            lines.append(f"# {key} = {default_repr}")
        lines.append("")
    return "\n".join(lines)

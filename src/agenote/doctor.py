# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.doctor — 环境自诊断（移植 claude-obsidian 的能力检测惯例）。

每个检测项三态 ok / warn / missing，缺失时列出受影响的命令与降级行为；
PATH 探测只证明二进制存在、不证明行为正确，这类项显式标注
verification_reason（「没有验证器」是一等信息公开声明，而非留空）。
纯只读，不写任何文件。

宿主自带记忆检测（C 线 C3，小节「宿主自带记忆（注入接管前置）」）：
只读探测六宿主的自带记忆开关——写侧禁用是注入接管的硬前提，否则双真相源。
宿主未安装（配置根不存在）→ 整项跳过不计分；agenote 绝不代改宿主配置，
只检测与指引。探测函数接受显式路径参数供测试注入，None 时走真实默认。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from agenote import config
from agenote.core import agenote_context, default_context
from agenote.index import _load_index

# 外部命令 → (受影响能力, 缺失时的降级行为)。检测方式为 shutil.which（PATH
# 探测），无行为验证器——verification_reason 统一注明。
_EXTERNAL_TOOLS: dict[str, tuple[str, str]] = {
    "rg": ("标签检索(tags)/正则检索(search --regex)", "自动降级 grep，功能可用但更慢"),
    "git": ("版本控制(init/commit)", "无降级，相关命令不可用"),
    "xdg-open": ("可视化自动打开(viz)", "用 --no-open 生成后手动打开"),
}

_PATH_PROBE_REASON = "PATH 探测（shutil.which），未运行行为验证"


def _check_python() -> dict:
    ver = sys.version_info
    return {
        "name": "python",
        "status": "ok" if ver >= (3, 10) else "warn",
        "detail": f"{ver.major}.{ver.minor}.{ver.micro}",
        "affects": "低于 3.10 无 tomllib 需 tomli 兜底" if ver < (3, 11) else "",
    }


def _check_sqlite() -> dict:
    return {
        "name": "sqlite3",
        "status": "ok",
        "detail": sqlite3.sqlite_version,
        "affects": "extract/reconcile 读取各 agent 对话库",
    }


def _check_external_tools() -> list[dict]:
    checks = []
    for tool, (affects, fallback) in _EXTERNAL_TOOLS.items():
        found = shutil.which(tool)
        checks.append(
            {
                "name": tool,
                "status": "ok" if found else "warn",
                "detail": found or "未找到",
                "affects": affects if not found else "",
                "fallback": fallback if not found else "",
                "verification_reason": _PATH_PROBE_REASON,
            }
        )
    return checks


def _check_config() -> dict:
    try:
        with open(config.CONFIG_PATH, "rb") as f:
            data = config.tomllib.load(f)
    except FileNotFoundError:
        return {
            "name": "config.toml",
            "status": "ok",
            "detail": f"未创建（全部用默认值）: {config.CONFIG_PATH}",
        }
    except config.tomllib.TOMLDecodeError:
        return {
            "name": "config.toml",
            "status": "missing",
            "detail": f"解析失败: {config.CONFIG_PATH}",
            "affects": "所有命令（配置层加载即退出）",
        }
    except (OSError, UnicodeError):
        return {
            "name": "config.toml",
            "status": "missing",
            "detail": f"读取失败: {config.CONFIG_PATH}",
            "affects": "所有命令（配置层加载即退出）",
        }
    unknown = config._unknown_keys(data)
    detail = (
        "; ".join(f"{kind} {desc}" for kind, desc in unknown[:3])
        + ("…" if len(unknown) > 3 else "")
        if unknown
        else f"正常: {config.CONFIG_PATH}"
    )
    return {
        "name": "config.toml",
        "status": "warn" if unknown else "ok",
        "detail": detail,
        "affects": "拼写错误的键会被忽略（用 config show 核对）" if unknown else "",
    }


def _check_kb(ctx) -> dict:
    """单域体检：目录存在性 + index.json 可解析且条目数与卡片文件数一致。"""
    domain = ctx.name
    if not ctx.root.exists():
        return {
            "name": f"kb[{domain}]",
            "status": "warn",
            "detail": f"目录不存在: {ctx.root}（首次 add/init 会自建）",
        }
    org_count = (
        len(list(ctx.experiences.rglob("*.org")))
        if ctx.experiences.exists()
        else 0
    )
    index = _load_index(ctx)
    mismatch = index["total"] != org_count
    return {
        "name": f"kb[{domain}]",
        "status": "warn" if mismatch else "ok",
        "detail": f"index {index['total']} 条 / 磁盘 {org_count} 张卡片",
        "affects": "索引与磁盘不一致，search/list 走文件层不受影响，"
        "list/stats 基于 index 的统计会失真（运行 reindex 重建）"
        if mismatch
        else "",
    }


def _check_kb_secrets() -> dict:
    """事后审计：SSOT 高流量面上是否已混入密钥形态内容。

    写侧门禁（add/memory --add/inbox-archive/update）只拦新增；存量文件
    （门禁前写入/手工编辑）靠本项兜底发现——MEMORY.org 经 context 注入
    每轮外发、卡片经 git 随库同步，泄漏面都不小。只报类别与文件名，
    不回显值。
    """
    from agenote.core import scan_secret_categories

    hits: list[str] = []
    cats_seen: set[str] = set()
    scanned = 0
    for ctx in (default_context(), agenote_context()):
        targets = [ctx.memory_org, ctx.memory_archive, ctx.inbox]
        if ctx.experiences.exists():
            targets.extend(
                p for p in sorted(ctx.experiences.rglob("*.org"))
                if not p.is_symlink()
            )
        for p in targets:
            if not p.exists() or p.is_symlink():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            scanned += 1
            cats = scan_secret_categories(text)
            if cats:
                cats_seen.update(cats)
                hits.append(f"{ctx.name}/{p.name}")
    if not hits:
        return {
            "name": "kb-secrets",
            "status": "ok",
            "detail": f"KB 高流量面无密钥形态内容（扫描 {scanned} 个文件）",
        }
    preview = "、".join(hits[:5]) + ("…" if len(hits) > 5 else "")
    return {
        "name": "kb-secrets",
        "status": "warn",
        "detail": f"{len(hits)} 个文件命中 secret 类别 "
                  f"（{', '.join(sorted(cats_seen))}）: {preview}",
        "affects": "密钥可经 context 注入/export 投影外发；逐条人工裁决清理",
    }


def run_checks() -> list[dict]:
    checks = [_check_python(), _check_sqlite(), _check_config()]
    checks.extend(_check_external_tools())
    checks.append(_check_kb(default_context()))
    checks.append(_check_kb(agenote_context()))
    checks.append(_check_kb_secrets())
    checks.extend(_host_memory_checks())
    return checks


# ═══════════════════════════════════════════════════════════════════════════════
# C3 宿主自带记忆检测（注入接管前置）——只读探测；宿主未安装则跳过不计分
# ═══════════════════════════════════════════════════════════════════════════════

_HOST_SECTION = "宿主自带记忆（注入接管前置）"


def _host_check(name: str, status: str, detail: str, affects: str = "") -> dict:
    return {"name": f"host-memory[{name}]", "status": status, "detail": detail,
            "affects": affects, "section": _HOST_SECTION}


def _yaml_section_vals(text: str, section: str) -> dict[str, str]:
    """宽松 YAML 节扫描：`section:` 之下缩进的 `key: value` 行。

    doctor 探测专用（omp/hermes 配置）；仓库无 yaml 依赖，判定开关布尔
    按行前缀宽松匹配足够，不为探测新增依赖。
    """
    vals: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        top = re.match(r"^(\S[^:]*):\s*(.*)$", line)
        if top:
            inside = top.group(1).strip() == section
            continue
        if inside:
            sub = re.match(r"^\s+(\S[^:]*):\s*(.*?)\s*$", line)
            if sub:
                vals[sub.group(1)] = sub.group(2).strip().strip("\"'")
    return vals


def _switch_on(val: str) -> bool:
    return str(val).strip().lower() in ("true", "yes", "on", "1")


def _read_json(path: Path) -> dict:
    """只读解析 JSON 配置；损坏/不可读抛 OSError/ValueError 由调用方定态。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _check_zcode_memory(path: Path | None = None) -> list[dict]:
    """zcode：features.memory / memory.use 任一 false 即合规（默认开，缺失视为开）。"""
    p = path or Path.home() / ".zcode" / "cli" / "config.json"
    if not p.exists():
        return []  # 未安装 → 跳过不计分
    try:
        data = _read_json(p)
    except (OSError, UnicodeError, ValueError):
        return [_host_check("zcode", "missing", f"配置不可解析: {p}")]
    feats = data.get("features") if isinstance(data.get("features"), dict) else {}
    mem = data.get("memory") if isinstance(data.get("memory"), dict) else {}
    if feats.get("memory") is False or mem.get("use") is False:
        return [_host_check(
            "zcode", "ok", "自带记忆已关闭（features.memory=false 或 memory.use=false）")]
    return [_host_check(
        "zcode", "warn", "自带记忆开启（features.memory / memory.use 均非 false）",
        "双真相源：宿主本地沉淀与 agenote KB 并行。关闭指引："
        "~/.zcode/cli/config.json 设 features.memory=false 或 memory.use=false")]


def _check_claude_memory(home: Path | None = None) -> list[dict]:
    """claude：autoMemoryEnabled 键缺失视为开 → warn；false 合规；dream 键随附提示。"""
    home = home or Path.home() / ".claude"
    if not home.exists():
        return []
    data: dict = {}
    p = home / "settings.json"
    if p.exists():
        try:
            data = _read_json(p)
        except (OSError, UnicodeError, ValueError):
            return [_host_check("claude", "missing", f"配置不可解析: {p}")]
    auto = data.get("autoMemoryEnabled")
    dream = data.get("autoDreamEnabled")
    guide = ("settings.json 设 autoMemoryEnabled:false + autoDreamEnabled:false"
             "（或 env CLAUDE_CODE_DISABLE_AUTO_MEMORY=1）")
    if auto is False:
        if dream is not False and dream is not None:
            return [_host_check(
                "claude", "warn", "autoMemoryEnabled 已关，但 autoDreamEnabled 仍开启",
                f"双通道残留：{guide}")]
        return [_host_check("claude", "ok", "自带记忆已关闭（autoMemoryEnabled=false）")]
    detail = ("自带记忆开启（autoMemoryEnabled 键缺失视为开）"
              if auto is None else "自带记忆开启（autoMemoryEnabled=true）")
    return [_host_check("claude", "warn", detail, f"关闭指引：{guide}")]


def _check_codex_memory(path: Path | None = None) -> list[dict]:
    """codex：features.memories 默认关（缺失合规不告警）；仅显式 true → warn。"""
    p = path or Path.home() / ".codex" / "config.toml"
    if not p.exists():
        return []
    try:
        with open(p, "rb") as f:
            data = config.tomllib.load(f)
    except (OSError, config.tomllib.TOMLDecodeError, UnicodeError):
        return [_host_check("codex", "missing", f"配置不可解析: {p}")]
    feats = data.get("features") if isinstance(data.get("features"), dict) else {}
    if feats.get("memories") is True:
        return [_host_check(
            "codex", "warn", "自带记忆开启（[features] memories=true）",
            "双真相源：关闭指引：~/.codex/config.toml 设 [features] memories=false")]
    return [_host_check("codex", "ok", "自带记忆关闭或默认关（合规）")]


def _check_omp_memory(path: Path | None = None) -> list[dict]:
    """omp：memory.backend 非 off 或 autolearn.enabled=true → warn。"""
    if path is None:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        path = base / "omp" / "config.yml"
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [_host_check("omp", "missing", f"配置不可读: {path}")]
    backend = _yaml_section_vals(text, "memory").get("backend", "")
    autolearn_on = _switch_on(_yaml_section_vals(text, "autolearn").get("enabled", ""))
    hits = []
    if backend and backend.lower() != "off":
        hits.append(f"memory.backend={backend}")
    if autolearn_on:
        hits.append("autolearn.enabled=true")
    if hits:
        return [_host_check(
            "omp", "warn", "自带记忆开启（" + "；".join(hits) + "）",
            "关闭指引：~/.config/omp/config.yml 设 memory.backend: off 与 autolearn.enabled: false")]
    return [_host_check("omp", "ok", "自带记忆关闭（backend=off 且 autolearn 关，合规）")]


def _check_hermes_memory(config_path: Path | None = None) -> list[dict]:
    """hermes：cli 配置 memory.memory_enabled / user_profile_enabled 任一 true → warn。

    配置根复用 [memories.sources].hermes_home 单一来源（$HERMES_HOME 可覆盖）。
    """
    if config_path is None:
        config_path = config.get_path("memories.sources", "hermes_home") / "config.yaml"
    if not config_path.exists():
        return []
    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return [_host_check("hermes", "missing", f"配置不可读: {config_path}")]
    vals = _yaml_section_vals(text, "memory")
    hits = []
    if _switch_on(vals.get("memory_enabled", "")):
        hits.append("memory.memory_enabled=true")
    if _switch_on(vals.get("user_profile_enabled", "")):
        hits.append("user_profile_enabled=true")
    if hits:
        return [_host_check(
            "hermes", "warn", "自带记忆开启（" + "；".join(hits) + "）",
            "双真相源：关闭指引：hermes 配置 memory 节设 "
            "memory_enabled: false 与 user_profile_enabled: false")]
    return [_host_check("hermes", "ok", "自带记忆已关闭（store 与 profile 双键关，合规）")]


def _check_dual_channel() -> dict:
    """双通道并存：[memories.targets] 任一非空 且 injection.enabled → warn。

    同一份记忆将经投影与注入双份进上下文；投影已配置层面 deprecated，
    退役指引 = 清空 targets（projector 代码保留）。
    """
    from agenote.context import _cfg_bool  # lazy：注入开关判定同口径
    from agenote.projector import target_dirs

    targets = target_dirs()
    if targets and _cfg_bool("injection", "enabled"):
        return {
            "name": "dual-channel", "status": "warn",
            "detail": "投影与注入双通道并存（targets: "
                      + ", ".join(sorted(targets)) + "）",
            "affects": "同一份记忆将经投影与注入双份进上下文。退役指引：清空 "
                      "[memories.targets] 各键（projector 代码保留，配置层面 deprecated）",
            "section": _HOST_SECTION,
        }
    return {
        "name": "dual-channel", "status": "ok",
        "detail": "无双通道并存（投影 targets 空或注入关闭）", "section": _HOST_SECTION,
    }


def _host_memory_checks() -> list[dict]:
    """六项检测汇聚；宿主探测函数对未安装宿主返回空列表（不计分）。"""
    checks: list[dict] = []
    checks.extend(_check_zcode_memory())
    checks.extend(_check_claude_memory())
    checks.extend(_check_codex_memory())
    checks.extend(_check_omp_memory())
    checks.extend(_check_hermes_memory())
    checks.append(_check_dual_channel())
    return checks


def cmd_doctor(args: argparse.Namespace, ctx=None) -> None:
    """环境自诊断：外部工具/配置/KB 结构三块，纯只读。"""
    checks = run_checks()
    if getattr(args, "json", False):
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return

    problems = 0
    current_section = ""
    for c in checks:
        section = c.get("section", "")
        if section and section != current_section:
            print(f"\n── {section} ──")
            current_section = section
        icon = {"ok": "✅", "warn": "⚠️ ", "missing": "❌"}[c["status"]]
        print(f"{icon} {c['name']}: {c['detail']}")
        if c.get("affects"):
            print(f"   影响: {c['affects']}")
        if c.get("fallback"):
            print(f"   降级: {c['fallback']}")
        if c.get("verification_reason"):
            print(f"   验证: {c['verification_reason']}")
        if c["status"] in ("warn", "missing"):
            problems += 1
    print(
        f"\n{len(checks)} 项检查，{problems} 项需关注"
        if problems
        else f"\n{len(checks)} 项检查全部通过"
    )

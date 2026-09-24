# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.doctor — 环境自诊断（移植 claude-obsidian 的能力检测惯例）。

每个检测项三态 ok / warn / missing，缺失时列出受影响的命令与降级行为；
PATH 探测只证明二进制存在、不证明行为正确，这类项显式标注
verification_reason（「没有验证器」是一等信息公开声明，而非留空）。
纯只读，不写任何文件。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys

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


def run_checks() -> list[dict]:
    checks = [_check_python(), _check_sqlite(), _check_config()]
    checks.extend(_check_external_tools())
    checks.append(_check_kb(default_context()))
    checks.append(_check_kb(agenote_context()))
    return checks


def cmd_doctor(args: argparse.Namespace, ctx=None) -> None:
    """环境自诊断：外部工具/配置/KB 结构三块，纯只读。"""
    checks = run_checks()
    if getattr(args, "json", False):
        print(json.dumps(checks, ensure_ascii=False, indent=2))
        return

    problems = 0
    for c in checks:
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

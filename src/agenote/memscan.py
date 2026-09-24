# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.memscan — 跨 agent 记忆库只读扫描（scan-memories 子命令）。

与 extract/ 的分工：extract 读各 agent 的会话对话（reconcile/dream 管道），
本模块读各 agent 的持久记忆库（agent 已提炼的结构化知识），输出条目清单
供策展 agent 审查后显式导入（agenote add / memory --add），不自动写 KB。

源注册表（root 解析走 resolve_xdg_path：专属 env > config.toml > XDG 占位符 > ~/）：

  name      env                      default                    布局
  zcode     ZCODE_MEMORIES_DIR       ~/.zcode/cli/memories      projects/<slug>/memory/*.md
  claude    CLAUDE_CONFIG_DIR        ~/.claude                  projects/<slug>/memory/*.md
  codex     CODEX_HOME               ~/.codex                   memories/**/*.md（官方未定死格式）
  pi        PI_CODING_AGENT_DIR      $XDG_CONFIG_HOME/omp       agent/memory/**/*.md
  reasonix  REASONIX_HOME            $XDG_DATA_HOME/reasonix    projects/<slug>/memory/*.md
  hermes    HERMES_HOME              $XDG_DATA_HOME/hermes      memories/{MEMORY,USER}.md（§ 分隔）

统一约定：目录式源跳过 MEMORY.md 索引；frontmatter 宽容解析（仅取
name/title/description/metadata.type，缺字段用文件名兜底，无 frontmatter 照常收）；
目录缺失返回 ([], [note]) 不抛异常；纯只读，绝不写外部 agent 数据。
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from agenote.extract import resolve_xdg_path
from agenote.safeio import safe_read_text


@dataclass
class MemorySourceSpec:
    name: str
    env: str
    default: str
    # 目录式源：相对 root 的 glob；分节文件源：files 列出相对路径
    pattern: str = ""
    files: tuple[str, ...] = field(default=())


SOURCES: dict[str, MemorySourceSpec] = {
    "zcode": MemorySourceSpec(
        "zcode", "ZCODE_MEMORIES_DIR", "~/.zcode/cli/memories",
        pattern="projects/*/memory/*.md",
    ),
    "claude": MemorySourceSpec(
        "claude", "CLAUDE_CONFIG_DIR", "~/.claude",
        pattern="projects/*/memory/*.md",
    ),
    "codex": MemorySourceSpec(
        "codex", "CODEX_HOME", "~/.codex", pattern="memories/**/*.md",
    ),
    "pi": MemorySourceSpec(
        "pi", "PI_CODING_AGENT_DIR", "$XDG_CONFIG_HOME/omp",
        pattern="agent/memory/**/*.md",
    ),
    "reasonix": MemorySourceSpec(
        "reasonix", "REASONIX_HOME", "$XDG_DATA_HOME/reasonix",
        pattern="projects/*/memory/*.md",
    ),
    "hermes": MemorySourceSpec(
        "hermes", "HERMES_HOME", "$XDG_DATA_HOME/hermes",
        files=("memories/MEMORY.md", "memories/USER.md"),
    ),
}


# ── frontmatter 轻量解析（无 pyyaml 依赖，只取各源实际用到的键） ──

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 frontmatter 标量键 + metadata.type 一层嵌套，返回 (meta, body)。"""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    in_metadata = False
    for line in m.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in (" ", "\t"):
            if in_metadata:
                k, _, v = line.strip().partition(":")
                if k.strip() == "type" and v.strip():
                    meta["type"] = v.strip()
            continue
        in_metadata = False
        k, _, v = line.partition(":")
        k, v = k.strip(), v.strip().strip("'\"")
        if not k:
            continue
        if k == "metadata":
            in_metadata = True
        elif v:
            meta[k] = v
    return meta, text[m.end():]


def _mtime(path: Path) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))


def _scan_dir_source(spec: MemorySourceSpec) -> tuple[list[dict], list[str]]:
    root = resolve_xdg_path(spec.env, spec.default, section="memories.sources")
    if not root.is_dir():
        return [], [f"{spec.name} 记忆目录不存在: {root}"]
    entries: list[dict] = []
    errors: list[str] = []
    for path in sorted(root.glob(spec.pattern)):
        if path.name == "MEMORY.md":
            continue
        try:
            text = safe_read_text(path, errors="replace")
        except OSError as exc:
            errors.append(f"记忆文件读取失败（{type(exc).__name__}）")
            continue
        fm, body = _parse_frontmatter(text)
        # projects/<slug>/memory/x.md 取 slug 作 project；扁平布局（pi/codex）留空
        project = path.parent.parent.name if path.parent.name == "memory" else ""
        entries.append({
            "source": spec.name,
            "path": str(path),
            "project": project,
            "name": fm.get("name") or fm.get("title") or path.stem,
            "type": fm.get("type", ""),
            "description": fm.get("description", ""),
            "body": body.strip(),
            "modified": _mtime(path),
        })
    return entries, errors


def _section_title(chunk: str) -> str:
    """hermes 条目标题：[标签] 前缀，否则首句截断。"""
    m = re.match(r"\[([^\]]+)\]", chunk.strip())
    if m:
        return m.group(1).strip()
    return chunk.strip().split("。", 1)[0][:40] or "(untitled)"


def _scan_section_files(spec: MemorySourceSpec) -> tuple[list[dict], list[str]]:
    root = resolve_xdg_path(spec.env, spec.default, section="memories.sources")
    entries: list[dict] = []
    errors: list[str] = []
    for rel in spec.files:
        path = root / rel
        if not path.exists():
            errors.append(f"{spec.name} 记忆文件不存在: {path}")
            continue
        try:
            text = safe_read_text(path, errors="replace")
        except OSError as exc:
            errors.append(f"记忆文件读取失败（{type(exc).__name__}）")
            continue
        for chunk in text.split("§"):
            if not chunk.strip():
                continue
            entries.append({
                "source": spec.name,
                "path": str(path),
                "project": "",
                "name": _section_title(chunk),
                "type": "",
                "description": "",
                "body": chunk.strip(),
                "modified": _mtime(path),
            })
    return entries, errors


def scan_memories(source: str = "all") -> dict:
    """扫描（多个）记忆源，返回 {total, by_source, entries, notes}。"""
    specs = [SOURCES[source]] if source != "all" else list(SOURCES.values())
    entries: list[dict] = []
    notes: list[str] = []
    for spec in specs:
        scanner = _scan_section_files if spec.files else _scan_dir_source
        e, n = scanner(spec)
        entries.extend(e)
        notes.extend(n)
    by_source = {name: 0 for name in SOURCES}
    for e in entries:
        by_source[e["source"]] += 1
    return {
        "total": len(entries),
        "by_source": by_source,
        "entries": entries,
        "notes": notes,
    }


def cmd_scan_memories(args: argparse.Namespace, ctx=None) -> None:
    report = scan_memories(args.source)
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    for note in report["notes"]:
        print(f"[note] {note}")
    current = ""
    for e in report["entries"]:
        if e["source"] != current:
            current = e["source"]
            print(f"\n── {current} ──")
        suffix = f" @{e['project']}" if e["project"] else ""
        print(f"[{e['type'] or '-'}] {e['name']}{suffix}")
        if e["description"]:
            print(f"    {e['description']}")
        print(f"    {e['path']}")
    print(f"\n共 {report['total']} 条 | " + " ".join(
        f"{k}:{v}" for k, v in report["by_source"].items() if v
    ))

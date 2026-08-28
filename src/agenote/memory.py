# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

"""kb_memory — 知识库记忆系统：MEMORY.org 管理、项目记忆、模式管理"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from agenote.core import (
    PROJECT_CURATE_DAYS,
    STALE_DAYS,
    die,
    ensure_dirs,
    today,
    now,
    _init_memory_template_for_ctx,
    default_context,
)
from agenote.safeio import atomic_write


def _parse_memory_sections(content: str) -> dict[str, list[tuple[int, int, str]]]:
    """解析 MEMORY.org 的 `*` 顶级节。

    返回 {节名: [(起始行号, 结束行号, 节文本)]}，行号从 0 开始。
    """
    lines = content.split("\n")
    sections: dict[str, list[tuple[int, int, str]]] = {}
    cur_name = None
    cur_start = 0
    for i, line in enumerate(lines):
        m = re.match(r"^\*\s+(.+)", line)
        if m:
            if cur_name is not None:
                sections.setdefault(cur_name, []).append(
                    (cur_start, i, "\n".join(lines[cur_start:i]))
                )
            cur_name = m.group(1).strip()
            cur_start = i
    if cur_name is not None:
        sections.setdefault(cur_name, []).append(
            (cur_start, len(lines), "\n".join(lines[cur_start:]))
        )
    return sections


def _next_memory_id(section_content: str, prefix: str) -> str:
    """扫描已有 F/R 序号，返回下一个（如 F015）。"""
    existing = re.findall(rf"^\*\* {prefix}(\d+)", section_content, re.MULTILINE)
    if not existing:
        return f"{prefix}001"
    max_id = max(int(n) for n in existing)
    return f"{prefix}{max_id + 1:03d}"


def _find_section_end(lines: list[str], section_start: int) -> int:
    """找到指定 `*` 节的最后一行（下一个 `*` 节之前）。"""
    for i in range(section_start + 1, len(lines)):
        if re.match(r"^\*\s+", lines[i]):
            return i
    return len(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: memory — 管理记忆系统
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_memory(args: argparse.Namespace, ctx=None) -> None:
    """管理 MEMORY.org 中的反馈、项目和参考记忆。"""
    ctx = ctx or default_context()
    ensure_dirs(ctx)

    # --get：输出 MEMORY.org 全文或指定节
    if getattr(args, "get", False):
        if not ctx.memory_org.exists():
            print("(记忆文件不存在)")
            return
        text = ctx.memory_org.read_text(encoding="utf-8")
        mem_type = getattr(args, "type", None)
        if mem_type:
            # 只输出指定节的内容
            sections = _parse_memory_sections(text)
            for sec_name, entries in sections.items():
                if mem_type in sec_name.lower():
                    for _start, _end, sec_content in entries:
                        print(sec_content, end="")
                    return
            print(f"(未找到 {mem_type} 节)")
        else:
            print(text, end="")
        return

    # --touch：更新时间戳
    if getattr(args, "touch", None):
        _memory_touch(args.touch, ctx)
        return

    # --archive：归档到 deprecated 节
    if getattr(args, "archive", None):
        _memory_archive(args.archive, ctx)
        return

    # --archive-to-file：归档到 MEMORY-ARCHIVE.org
    if getattr(args, "archive_to_file", None):
        _memory_archive_to_file(args.archive_to_file, ctx)
        return

    # --project-touch：更新项目 LAST_ACTIVE
    if getattr(args, "project_touch", None):
        _memory_project_touch(args.project_touch, ctx)
        return

    # --stale：列出陈旧记忆（归档由 agent 审查后逐条 --archive-to-file 执行）
    if getattr(args, "stale", False):
        _memory_stale(ctx)
        return

    # --add：添加记忆（优先于 --project 检索）
    if getattr(args, "add", False):
        _memory_add(args, ctx)
        return

    # --project：按项目名或路径检索（含 PATH/UPDATED 只读健康提示）
    if getattr(args, "project", None):
        _memory_project(args.project, ctx)
        return

    # 默认：列出所有记忆概览
    _memory_overview(args, ctx)


def _memory_overview(args: argparse.Namespace, ctx=None) -> None:
    """列出所有记忆概览或按类型过滤。"""
    ctx = ctx or default_context()
    if not ctx.memory_org.exists():
        print("(记忆文件不存在)")
        return

    text = ctx.memory_org.read_text(encoding="utf-8")
    sections = _parse_memory_sections(text)

    mem_type = getattr(args, "type", None)
    if mem_type:
        # 映射类型到节名
        type_to_section = {
            "feedback": "feedback",
            "project": "project",
            "reference": "reference",
        }
        section_name = type_to_section.get(mem_type)
        if not section_name:
            die(f"未知记忆类型: {mem_type}")
        for sec_name, entries in sections.items():
            if section_name in sec_name.lower():
                for _start, _end, content in entries:
                    # 列出 ** 二级标题
                    for line in content.split("\n"):
                        m = re.match(r"^\*\*\s+(.+)", line)
                        if m:
                            print(m.group(1))
                return
        print(f"(未找到 {mem_type} 条目)")
        return

    # 无过滤：概览统计
    print("═══ MEMORY 概览 ═══")
    for sec_name, entries in sections.items():
        total = 0
        for _s, _e, content in entries:
            total += len(re.findall(r"^\*\* ", content, re.MULTILINE))
        print(f"  {sec_name}: {total} 条")


def _memory_add(args: argparse.Namespace, ctx=None) -> None:
    """添加记忆条目。"""
    ctx = ctx or default_context()
    mem_type = getattr(args, "type", None) or "feedback"
    title = getattr(args, "title", "") or ""
    use_stdin = getattr(args, "stdin", False)
    project_name = getattr(args, "project", None)

    body = ""
    if use_stdin and not sys.stdin.isatty():
        body = sys.stdin.read()

    if not title:
        # 从 stdin 内容第一行生成 title
        if body:
            first_line = body.strip().split("\n")[0].strip()
            title = first_line[:80] if first_line else "(无标题)"
        else:
            die("添加记忆需要 --title")

    if mem_type == "project":
        _memory_add_project(title, body, project_name, ctx)
        return

    if not ctx.memory_org.exists():
        _init_memory_template_for_ctx(ctx)

    text = ctx.memory_org.read_text(encoding="utf-8")
    lines = text.split("\n")
    sections = _parse_memory_sections(text)

    type_to_section = {
        "feedback": "feedback",
        "reference": "reference",
    }
    section_name = type_to_section.get(mem_type)
    if not section_name:
        die(f"未知记忆类型: {mem_type}")

    # 找到目标节
    target_section = None
    for sec_name, entries in sections.items():
        if section_name in sec_name.lower():
            target_section = entries[0]
            break

    prefix = "F" if mem_type == "feedback" else "R"

    if target_section:
        _start, _end, sec_content = target_section
        new_id = _next_memory_id(sec_content, prefix)
        insert_line = _find_section_end(lines, _start)
    else:
        # 节不存在，创建
        new_id = f"{prefix}001"
        # 在文件末尾追加新节
        insert_line = len(lines)
        # 先添加节标题
        section_header = f"\n* {section_name}"
        lines.append(section_header)
        insert_line = len(lines)

    # 构建条目
    entry_lines = [f"\n** {new_id} {title}"]
    entry_lines.append("   :PROPERTIES:")
    entry_lines.append(f"   :CREATED:  [{today()}]")
    entry_lines.append(f"   :UPDATED:  [{today()}]")
    if mem_type == "feedback" and getattr(args, "ref", None):
        entry_lines.append(f"   :REF:      {args.ref}")
    entry_lines.append("   :END:")
    if body.strip():
        entry_lines.append(f"   {body.strip()}")

    entry_text = "\n".join(entry_lines) + "\n"
    lines.insert(insert_line, entry_text)

    atomic_write(ctx.memory_org, "\n".join(lines))
    print(f"已添加 {mem_type} 记忆: {new_id} {title}")


def _memory_add_project(
    title: str, body: str, project_name: str | None, ctx=None
) -> None:
    """添加 project 记忆到 memories/projects/<name>.org。"""
    ctx = ctx or default_context()
    if not project_name:
        die("添加 project 记忆需要 --project <项目名>")

    ctx.projects.mkdir(parents=True, exist_ok=True)
    proj_file = ctx.projects / f"{project_name}.org"

    if not proj_file.exists():
        atomic_write(
            proj_file, f"#+title: {project_name}\n#+date: [{today()}]\n\n"
        )

    # 追加条目到项目文件
    proj_text = proj_file.read_text(encoding="utf-8")
    proj_lines = proj_text.split("\n")

    entry_lines = [f"\n** {title}"]
    entry_lines.append("   :PROPERTIES:")
    entry_lines.append(f"   :CREATED:  [{today()}]")
    entry_lines.append(f"   :UPDATED:  [{today()}]")
    entry_lines.append("   :END:")
    if body.strip():
        entry_lines.append(f"   {body.strip()}")
    entry_lines.append("")

    proj_lines.extend(entry_lines)
    atomic_write(proj_file, "\n".join(proj_lines))

    # 同步更新 MEMORY.org 索引
    _memory_sync_project_index(project_name, proj_file, ctx)

    print(f"已添加 project 记忆: {title} → {project_name}")


def _memory_sync_project_index(name: str, proj_file: Path, ctx=None) -> None:
    """同步项目到 MEMORY.org 的 * project 节索引。"""
    ctx = ctx or default_context()
    if not ctx.memory_org.exists():
        _init_memory_template_for_ctx(ctx)

    text = ctx.memory_org.read_text(encoding="utf-8")

    # 检查是否已存在索引
    if f"** {name}" in text:
        return

    lines = text.split("\n")
    sections = _parse_memory_sections(text)

    # 找到或创建 * project 节
    proj_section = None
    for sec_name, entries in sections.items():
        if "project" in sec_name.lower():
            proj_section = entries[0]
            break

    if proj_section:
        insert_line = _find_section_end(lines, proj_section[0])
    else:
        # 追加新节
        insert_line = len(lines)
        lines.append("")
        lines.append("* project")
        insert_line = len(lines)

    index_entry = (
        f"\n** {name}\n"
        f"   :PROPERTIES:\n"
        f"   :PATH:     {proj_file}\n"
        f"   :FILE:     {proj_file}\n"
        f"   :UPDATED:  [{today()}]\n"
        f"   :END:\n"
    )
    lines.insert(insert_line, index_entry)
    atomic_write(ctx.memory_org, "\n".join(lines))


def _memory_touch(entry_id: str, ctx=None) -> None:
    ctx = ctx or default_context()
    """更新指定条目的 UPDATED 时间戳。"""
    if not ctx.memory_org.exists():
        die("记忆文件不存在")

    text = ctx.memory_org.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 找到条目位置
    found = False
    for i, line in enumerate(lines):
        if re.match(rf"^\*\* {re.escape(entry_id)}\b", line):
            found = True
            # 向下查找 :UPDATED: 属性行
            for j in range(i + 1, min(i + 10, len(lines))):
                if ":UPDATED:" in lines[j]:
                    lines[j] = re.sub(
                        r":UPDATED:\s*\[\d{4}-\d{2}-\d{2}\]",
                        f":UPDATED:  [{today()}]",
                        lines[j],
                    )
                    break
                if ":END:" in lines[j]:
                    break
            break

    if not found:
        die(f"未找到条目: {entry_id}")

    atomic_write(ctx.memory_org, "\n".join(lines))
    print(f"已更新 {entry_id} 时间戳 → {today()}")


def _memory_archive(entry_id: str, ctx=None) -> None:
    ctx = ctx or default_context()
    """将指定条目移入 * deprecated 节。"""
    if not ctx.memory_org.exists():
        die("记忆文件不存在")

    text = ctx.memory_org.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 找到条目起始行
    entry_start = None
    for i, line in enumerate(lines):
        if re.match(rf"^\*\* {re.escape(entry_id)}\b", line):
            entry_start = i
            break

    if entry_start is None:
        die(f"未找到条目: {entry_id}")

    # 找到条目结束行（下一个 ** 或 * 之前）
    entry_end = entry_start + 1
    while entry_end < len(lines):
        if re.match(r"^\*\*?\s+", lines[entry_end]):
            break
        entry_end += 1

    # 提取条目文本
    entry_lines = lines[entry_start:entry_end]

    # 从原位置删除
    del lines[entry_start:entry_end]

    # 找到或创建 * deprecated 节
    sections = _parse_memory_sections("\n".join(lines))
    dep_section = None
    for sec_name, entries in sections.items():
        if "deprecated" in sec_name.lower():
            dep_section = entries[0]
            break

    if dep_section:
        insert_line = _find_section_end(lines, dep_section[0])
    else:
        # 追加 deprecated 节
        lines.append("")
        lines.append("* deprecated")
        insert_line = len(lines)

    # 插入到 deprecated 节末尾
    for k, el in enumerate(entry_lines):
        lines.insert(insert_line + k, el)

    atomic_write(ctx.memory_org, "\n".join(lines))
    print(f"已归档 {entry_id} → deprecated")


def _memory_stale(ctx=None) -> None:
    ctx = ctx or default_context()
    """列出超过 STALE_DAYS 天未更新的条目。"""
    if not ctx.memory_org.exists():
        print("(记忆文件不存在)")
        return

    text = ctx.memory_org.read_text(encoding="utf-8")
    lines = text.split("\n")
    stale_count = 0

    for i, line in enumerate(lines):
        if not re.match(r"^\*\* ", line):
            continue
        # 向下查找 UPDATED
        for j in range(i + 1, min(i + 10, len(lines))):
            m = re.match(r"\s*:UPDATED:\s*\[(\d{4}-\d{2}-\d{2})\]", lines[j])
            if m:
                try:
                    updated = datetime.strptime(m.group(1), "%Y-%m-%d")
                    if (datetime.now() - updated).days > STALE_DAYS:
                        print(f"  {line.strip()} (更新于 {m.group(1)})")
                        stale_count += 1
                except ValueError:
                    pass
                break
            if ":END:" in lines[j]:
                break

    if stale_count == 0:
        print("无陈旧记忆")
    else:
        print(f"\n共 {stale_count} 条 >{STALE_DAYS} 天未更新")


def _memory_project(identifier: str, ctx=None) -> None:
    ctx = ctx or default_context()
    """按项目名或路径检索项目记忆。"""
    if not ctx.memory_org.exists():
        print("(记忆文件不存在)")
        return

    text = ctx.memory_org.read_text(encoding="utf-8")
    sections = _parse_memory_sections(text)

    # 收集所有项目条目
    proj_entries = []
    for sec_name, entries in sections.items():
        if "project" in sec_name.lower():
            for _s, _e, content in entries:
                # 解析 ** 条目及其属性
                entry_lines_list = content.split("\n")
                cur_entry = None
                cur_props: dict[str, str] = {}
                for el in entry_lines_list:
                    m_entry = re.match(r"^\*\*\s+(.+)", el)
                    if m_entry:
                        if cur_entry:
                            proj_entries.append((cur_entry, cur_props))
                        cur_entry = m_entry.group(1)
                        cur_props = {}
                    else:
                        pm = re.match(r"\s*:(\w+):\s*(.+)", el)
                        if pm and cur_entry:
                            cur_props[pm.group(1)] = pm.group(2).strip()
                if cur_entry:
                    proj_entries.append((cur_entry, cur_props))
            break

    if not proj_entries:
        print("未找到匹配项目。")
        sys.exit(1)

    # 处理 "." → 用 PWD
    if identifier == ".":
        identifier = os.getcwd()

    # 尝试匹配：1) 项目名  2) 路径前缀
    matched = None

    # 按项目名匹配
    for entry_title, props in proj_entries:
        if entry_title.strip() == identifier.strip():
            matched = props
            break

    # 按路径前缀匹配
    if not matched:
        try:
            id_path = str(Path(identifier).expanduser().resolve())
        except (OSError, RuntimeError):
            id_path = identifier

        for entry_title, props in proj_entries:
            path_val = props.get("PATH", props.get("FILE", ""))
            if path_val:
                try:
                    resolved = str(Path(path_val).expanduser().resolve())
                    if resolved.startswith(id_path) or id_path.startswith(resolved):
                        matched = props
                        break
                except (OSError, RuntimeError):
                    if path_val == identifier:
                        matched = props
                        break

    if not matched:
        known = ", ".join(e[0] for e in proj_entries)
        print(f"未找到匹配项目。已知项目：{known}")
        sys.exit(1)

    # 健康检查（只读提示，dormant/归档等策展决策由 agent 执行）
    path_val = matched.get("PATH", "")
    if path_val and not Path(path_val).expanduser().exists():
        print(f"[!] PATH 已失效: {path_val}（建议评估是否标注 dormant）")
    updated_str = matched.get("UPDATED", "").strip("[]")
    if updated_str:
        try:
            delta = (datetime.now().date() - datetime.strptime(
                updated_str.split()[0], "%Y-%m-%d"
            ).date()).days
            if delta > PROJECT_CURATE_DAYS:
                print(f"[!] 距上次更新 {delta} 天 (>{PROJECT_CURATE_DAYS})，建议策展")
        except ValueError:
            pass

    # 读取并输出项目文件内容
    file_path = matched.get("FILE", matched.get("PATH", ""))
    proj_path = (
        ctx.root / file_path if not Path(file_path).is_absolute() else Path(file_path)
    )
    if proj_path.exists():
        print(proj_path.read_text(encoding="utf-8"), end="")
    else:
        print(f"项目文件不存在: {file_path}")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# 0.9 增强: MEMORY 归档分离 — 支持归档到 MEMORY-ARCHIVE.org
# ═══════════════════════════════════════════════════════════════════════════════


def _memory_archive_to_file(entry_id: str, ctx=None) -> None:
    ctx = ctx or default_context()
    """将 feedback 条目从 MEMORY.org 移到 MEMORY-ARCHIVE.org。

    project 和 reference 类型不自动归档到文件。
    """
    if not ctx.memory_org.exists():
        die("记忆文件不存在")

    text = ctx.memory_org.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 找到条目
    entry_start = None
    for i, line in enumerate(lines):
        if re.match(rf"^\*\* {re.escape(entry_id)}\b", line):
            entry_start = i
            break

    if entry_start is None:
        die(f"未找到条目: {entry_id}")

    # 找到条目结束行
    entry_end = entry_start + 1
    while entry_end < len(lines):
        if re.match(r"^\*\*?\s+", lines[entry_end]):
            break
        entry_end += 1

    # 提取条目文本
    entry_text = "\n".join(lines[entry_start:entry_end])

    # 添加 ARCHIVED_AT 属性
    if ":ARCHIVED_AT:" not in entry_text:
        entry_text = entry_text.replace(":END:", f":ARCHIVED_AT: [{now()}]\n:END:", 1)

    # 从 MEMORY.org 删除
    del lines[entry_start:entry_end]
    # 清理多余空行
    while entry_start < len(lines) and lines[entry_start].strip() == "":
        del lines[entry_start]
    atomic_write(ctx.memory_org, "\n".join(lines))

    # 追加到 MEMORY-ARCHIVE.org
    archive = ctx.memory_archive
    if not archive.exists():
        atomic_write(
            archive, f"#+title: MEMORY-ARCHIVE\n#+date: [{now()}]\n\n* archived\n"
        )

    archive_text = archive.read_text(encoding="utf-8")
    # 找到或创建 * archived 节
    if "* archived" in archive_text:
        archive_text = archive_text.rstrip("\n") + f"\n\n{entry_text}\n"
    else:
        archive_text = archive_text.rstrip("\n") + f"\n\n* archived\n\n{entry_text}\n"

    atomic_write(archive, archive_text)
    print(f"已归档到 MEMORY-ARCHIVE.org: {entry_id}")


# ═══════════════════════════════════════════════════════════════════════════════
# 0.10 增强: 项目记忆元数据 — LAST_ACTIVE, LAST_CURATED, STATUS
# ═══════════════════════════════════════════════════════════════════════════════


def _memory_project_touch(project_name: str, ctx=None) -> None:
    ctx = ctx or default_context()
    """更新项目记忆的 LAST_ACTIVE 时间戳。"""
    if not ctx.memory_org.exists():
        die("记忆文件不存在")

    text = ctx.memory_org.read_text(encoding="utf-8")
    lines = text.split("\n")

    # 找到项目条目
    found = False
    for i, line in enumerate(lines):
        m = re.match(rf"^\*\* {re.escape(project_name)}\b", line)
        if m:
            found = True
            # 向下查找并更新/添加 LAST_ACTIVE
            for j in range(i + 1, min(i + 10, len(lines))):
                if ":LAST_ACTIVE:" in lines[j]:
                    lines[j] = re.sub(
                        r":LAST_ACTIVE:\s*\[.+?\]",
                        f":LAST_ACTIVE: [{today()}]",
                        lines[j],
                    )
                    break
                if ":END:" in lines[j]:
                    # 插入 LAST_ACTIVE
                    lines.insert(j, f"   :LAST_ACTIVE: [{today()}]")
                    break
            break

    if not found:
        die(f"未找到项目: {project_name}")

    atomic_write(ctx.memory_org, "\n".join(lines))

    print(f"已更新项目 {project_name} LAST_ACTIVE → {today()}")

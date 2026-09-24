# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

"""kb_memory — 知识库记忆系统：MEMORY.org 管理、项目记忆、模式管理"""

import argparse
import hashlib
import os
import re
import socket
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
from agenote.safeio import atomic_write, safe_read_text
from agenote.orgserde import (
    MEMORY_PROP_RE,
    build_memory_hook,
    entry_hook_or_title,
    is_memory_boundary,
    match_memory_entry,
    memory_prop,
    parse_memory_date,
    read_memory_hook,
    set_memory_prop_line,
    unverified_tag,
)


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
    """扫描已有 F/R 序号，返回下一个（如 F015）。走 orgserde 条目层。"""
    nums = []
    for line in section_content.splitlines():
        m = match_memory_entry(line)
        if m and re.fullmatch(rf"{re.escape(prefix)}\d+", m.group(1)):
            nums.append(int(m.group(1)[len(prefix):]))
    if not nums:
        return f"{prefix}001"
    return f"{prefix}{max(nums) + 1:03d}"


def _find_section_end(lines: list[str], section_start: int) -> int:
    """找到指定 `*` 节的最后一行（下一个 `*` 节之前）。"""
    for i in range(section_start + 1, len(lines)):
        if re.match(r"^\*\s+", lines[i]):
            return i
    return len(lines)


def _read_memory_org_text(ctx) -> str:
    """MEMORY.org 统一读入口（S8：走 safe_read，拒 symlink/非普通文件）。"""
    return safe_read_text(ctx.memory_org)


def memory_entry_freshness(entry_lines: list[str], stale_days: int = STALE_DAYS) -> str:
    """S7：条目时效标记。UPDATED 缺省 CREATED；超 stale_days 返回 `(unverified Nd)`。"""
    updated = parse_memory_date(memory_prop(entry_lines, "UPDATED")) or parse_memory_date(
        memory_prop(entry_lines, "CREATED")
    )
    days = (datetime.now().date() - updated).days if updated else None
    return unverified_tag(days, stale_days)


def format_memory_entry_line(
    entry_id: str,
    title: str,
    entry_lines: list[str] | None = None,
    *,
    freshness: bool = False,
    stale_days: int = STALE_DAYS,
) -> str:
    """S7 预留：memory --list 单行渲染。默认与旧概览一致；freshness 开启追加时效标记。"""
    line = f"** {entry_id} {title}".rstrip()
    if freshness and entry_lines is not None:
        tag = memory_entry_freshness(entry_lines, stale_days)
        if tag:
            line += f" {tag}"
    return line


# ═══════════════════════════════════════════════════════════════════════════════
# N1 事实层数据模型：类型化条目 + origin 链（读到无 TYPE 按前缀/节推导，不重写文件）
# ═══════════════════════════════════════════════════════════════════════════════

# --type 短字母与长名互映射（CLI 侧统一归一化为短字母）
TYPE_ALIASES = {
    "u": "U", "user": "U",
    "f": "F", "feedback": "F",
    "p": "P", "project": "P",
    "e": "E", "environment": "E",
    "r": "R", "reference": "R",
}
SECTION_TO_TYPE = {
    "user": "U", "feedback": "F", "project": "P",
    "environment": "E", "reference": "R",
}


def origin_id(agent: str, relpath: str, title: str) -> str:
    """N1 幂等键：sha256(AGENT:相对路径:标题)[:16]。"""
    return hashlib.sha256(f"{agent}:{relpath}:{title}".encode("utf-8")).hexdigest()[:16]


def _iter_memory_entries(text: str) -> list[dict]:
    """解析 MEMORY.org 全部 `**` 条目。

    每个条目 dict：section/id(K001 或项目名)/title/type(U|F|P|E|R|None)/
    kind(entry|index)/props/hook/validated_at。
    TYPE 推导优先级：显式 :TYPE: > 前缀字母+序号 > 所在节映射；
    project 节无前缀行是项目索引（kind=index），其余为约定条目。
    """
    entries: list[dict] = []
    lines = text.split("\n")
    section = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        m_sec = re.match(r"^\*\s+(.+)", line)
        if m_sec:
            section = m_sec.group(1).strip()
            i += 1
            continue
        m_entry = re.match(r"^\*\*\s+(.+)", line)
        if not m_entry:
            i += 1
            continue
        heading = m_entry.group(1).strip()
        m_typed = re.match(r"^([UFPER])(\d+)\s*(.*)$", heading)
        # 条目正文：到下一个 ** 或 * 为止；收集 props 与钩子行（`# ...`）
        props: dict[str, str] = {}
        hook = ""
        j = i + 1
        while j < len(lines) and not re.match(r"^\*\*?\s+", lines[j]):
            pm = re.match(r"\s*:(\w+):\s*(.+)", lines[j])
            if pm:
                props[pm.group(1)] = pm.group(2).strip()
            elif not hook:
                hm = re.match(r"\s*#\s?(.*)", lines[j])
                if hm and hm.group(1).strip():
                    hook = hm.group(1).strip()[:120]
            j += 1
        if m_typed:
            entry_id = m_typed.group(1) + m_typed.group(2)
            title = m_typed.group(3).strip()
            kind = "entry"
            derived = m_typed.group(1)
        else:
            entry_id = heading  # 项目索引行：id 即项目名
            title = heading
            kind = "index" if section.lower() == "project" else "entry"
            derived = None
        entry_type = (props.get("TYPE") or "").strip().upper() or None
        if entry_type not in ("U", "F", "P", "E", "R"):
            entry_type = derived or SECTION_TO_TYPE.get(section.lower())
        entries.append({
            "section": section, "id": entry_id, "title": title,
            "type": entry_type, "kind": kind, "props": props,
            "hook": hook,
            "validated_at": props.get("VALIDATED_AT", "").strip("[] "),
        })
        i = j
    return entries


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: memory — 管理记忆系统
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_memory(args: argparse.Namespace, ctx=None) -> None:
    """管理 MEMORY.org 中的反馈、项目和参考记忆。"""
    ctx = ctx or default_context()
    ensure_dirs(ctx)

    # N1：--type 短字母/长名统一归一化为短字母
    mem_type = getattr(args, "type", None)
    if mem_type and str(mem_type).lower() in TYPE_ALIASES:
        args.type = TYPE_ALIASES[str(mem_type).lower()]

    # --list：只读列出条目（不持 KB 锁，见 cli.py）
    if getattr(args, "list", False):
        _memory_list(args, ctx)
        return

    # --conflicts：只读列出冲突队列（N2 落盘，N4 裁决消费；不持 KB 锁）
    if getattr(args, "conflicts", False):
        _memory_conflicts(ctx)
        return

    # --get：输出 MEMORY.org 全文或指定节
    if getattr(args, "get", False):
        if not ctx.memory_org.exists():
            print("(记忆文件不存在)")
            return
        text = _read_memory_org_text(ctx)
        mem_type = getattr(args, "type", None)
        if mem_type:
            # 只输出指定节的内容（mem_type 已归一化为短字母，转回节名匹配）
            want_sec = next(
                (s for s, t in SECTION_TO_TYPE.items() if t == mem_type),
                str(mem_type).lower(),
            )
            # 只输出指定节的内容
            sections = _parse_memory_sections(text)
            for sec_name, entries in sections.items():
                if want_sec in sec_name.lower():
                    for _start, _end, sec_content in entries:
                        print(sec_content, end="")
                    return
            print(f"(未找到 {mem_type} 节)")
        else:
            print(text, end="")
        return

    # --export：N3 投影（MUTATING 但不持 kb_lock，见 projector；路径只来自 SCHEMA）
    if getattr(args, "export", False):
        from agenote import projector as _projector  # lazy：projector 反向引用本模块

        _projector.cmd_export(args, ctx)
        return

    # --supersede：N4 裁决落地（MUTATING，双侧写一步完成）
    if getattr(args, "supersede", None):
        new_id, old_id = args.supersede
        _memory_supersede(new_id, old_id, ctx)
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

    # --revalidate：只读列出待重验条目（machine 变更批量 + 手填过期 + 孤儿）
    if getattr(args, "revalidate", False):
        _memory_revalidate(ctx)
        return

    # --validate：刷新单条 VALIDATED_AT（MUTATING）
    if getattr(args, "validate", None):
        _memory_validate(args.validate, ctx)
        return

    # --import：N2 摄取管道（写命令；--dry-run 只预览不落盘，但仍持锁）
    if getattr(args, "do_import", False):
        from agenote.memory_import import run_import  # lazy：与 memory_import 互引

        import json as _json3

        report = run_import(
            source=getattr(args, "source", None) or "all",
            dry_run=getattr(args, "dry_run", False),
            ctx=ctx,
        )
        if getattr(args, "json", False):
            print(_json3.dumps(report, ensure_ascii=False))
            return
        print(f"import ({report['source']}, dry_run={report['dry_run']}): "
              f"imported={len(report['imported'])} skipped={len(report['skipped'])} "
              f"suspected_dup={len(report['suspected_dup'])} "
              f"conflicted={len(report['conflicted'])} "
              f"secret_blocked={len(report['secret_blocked'])}")
        for key in ("imported", "suspected_dup", "conflicted", "secret_blocked", "skipped"):
            for it in report[key]:
                extra = it.get("reason") or it.get("category") or it.get("existing", "")
                print(f"  [{key}] {it.get('title', '')}" + (f" ({extra})" if extra else ""))
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


def _memory_list(args: argparse.Namespace, ctx=None) -> None:
    """N1 只读：列出条目，支持 --type/--scope/--json 过滤。"""
    import json as _json

    ctx = ctx or default_context()
    if not ctx.memory_org.exists():
        print("(记忆文件不存在)")
        return

    text = ctx.memory_org.read_text(encoding="utf-8")
    entries = _iter_memory_entries(text)

    want_type = getattr(args, "type", None)
    want_scope = getattr(args, "scope", None)
    fresh_on = getattr(args, "freshness", False)
    # S7 收尾：--freshness 开启才算时效，阈值走 [memories].export_stale_days；默认输出不变
    export_stale_days = STALE_DAYS
    if fresh_on:
        from agenote import config as _config
        try:
            export_stale_days = int(str(_config.get("memories", "export_stale_days")))
        except (ValueError, TypeError, KeyError):
            pass
    rows = []
    for e in entries:
        if want_type and e["type"] != want_type:
            continue
        scope = (e["props"].get("SCOPE") or "").strip().lower()
        if want_scope and scope != str(want_scope).lower():
            continue
        if fresh_on:
            vd = parse_memory_date(
                e["props"].get("VALIDATED_AT")
                or e["props"].get("UPDATED")
                or e["props"].get("CREATED")
                or ""
            )
            days = (datetime.now().date() - vd).days if vd else None
            e["freshness"] = unverified_tag(days, export_stale_days)
        rows.append({**e, "scope": scope, "orphan": _entry_orphan(e)})

    if getattr(args, "json", False):
        print(_json.dumps([
            {"id": e["id"], "title": e["title"], "type": e["type"],
             "kind": e["kind"], "section": e["section"], "scope": e["scope"],
             "hook": e["hook"], "validated_at": e["validated_at"],
             "orphan": e["orphan"],
             "deprecated": e.get("section", "").lower() == "deprecated",
             **({"freshness": e.get("freshness", "")} if fresh_on else {})}
            for e in rows
        ], ensure_ascii=False))
        return

    if not rows:
        print("(无匹配条目)")
        return
    for e in rows:
        meta = f"[{e['type'] or '?'}] {e['id']} {e['title']}"
        details = []
        if e["scope"]:
            details.append(f"scope={e['scope']}")
        if e["validated_at"]:
            details.append(f"validated={e['validated_at']}")
        if e["orphan"]:
            details.append("orphan")
        if e.get("section", "").lower() == "deprecated":
            details.append("deprecated")
        if details:
            meta += f" ({', '.join(details)})"
        if fresh_on and e.get("freshness"):
            meta += f" {e['freshness']}"
        print(meta)
        if e["hook"]:
            print(f"    # {e['hook']}")


def _entry_orphan(entry: dict) -> bool:
    """N4 轻量孤儿检测：有 ORIGIN_AGENT + ORIGIN_PATH 但源文件已消失。

    完整版（源消失→重验队列）归 N2 import；此处 --list 仅打标记。
    接口假设：ORIGIN_PATH 为源文件绝对路径或 ~ 路径。
    """
    agent = (entry["props"].get("ORIGIN_AGENT") or "").strip()
    opath = (entry["props"].get("ORIGIN_PATH") or "").strip()
    if not agent or not opath:
        return False
    try:
        return not Path(opath).expanduser().exists()
    except (OSError, RuntimeError):
        return False


def _memory_overview(args: argparse.Namespace, ctx=None) -> None:
    """列出所有记忆概览或按类型过滤。"""
    ctx = ctx or default_context()
    if not ctx.memory_org.exists():
        print("(记忆文件不存在)")
        return

    text = _read_memory_org_text(ctx)
    sections = _parse_memory_sections(text)

    mem_type = getattr(args, "type", None)
    if mem_type:
        # 映射类型到节名（mem_type 已归一化为短字母）
        type_to_section = {t: s for s, t in SECTION_TO_TYPE.items()}
        section_name = type_to_section.get(mem_type)
        if not section_name:
            die(f"未知记忆类型: {mem_type}")
        for sec_name, entries in sections.items():
            if section_name in sec_name.lower():
                for _start, _end, content in entries:
                    # 列出 ** 二级标题（--freshness 追加时效标记，默认输出不变）
                    entry_lines_list = content.split("\n")
                    for idx, line in enumerate(entry_lines_list):
                        m = re.match(r"^\*\*\s+(.+)", line)
                        if m:
                            if getattr(args, "freshness", False):
                                tag = memory_entry_freshness(
                                    entry_lines_list[idx + 1 : idx + 11]
                                )
                                print(m.group(1) + (f" {tag}" if tag else ""))
                            else:
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
    mem_type = TYPE_ALIASES.get(str(mem_type).lower(), mem_type)
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

    if mem_type == "P":
        _memory_add_project(title, body, project_name, ctx)
        return

    if not ctx.memory_org.exists():
        _init_memory_template_for_ctx(ctx)

    text = _read_memory_org_text(ctx)
    lines = text.split("\n")
    sections = _parse_memory_sections(text)

    type_to_section = {t: s for s, t in SECTION_TO_TYPE.items() if t != "P"}
    section_name = type_to_section.get(mem_type)
    if not section_name:
        die(f"未知记忆类型: {mem_type}")

    # 找到目标节
    target_section = None
    for sec_name, entries in sections.items():
        if section_name in sec_name.lower():
            target_section = entries[0]
            break

    prefix = mem_type

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
    entry_lines.append(f"   :TYPE:     {mem_type}")
    entry_lines.append(f"   :SCOPE:    {'machine' if mem_type == 'E' else 'user'}")
    if mem_type == "E":
        # N5：E 条目记录所属机器键（切机检测用）；EXPIRES_AFTER 默认不写（空）
        entry_lines.append(f"   :MACHINE:  {resolve_machine_key()}")
    if mem_type == "F" and getattr(args, "ref", None):
        entry_lines.append(f"   :REF:      {args.ref}")
    entry_lines.append("   :END:")
    entry_lines.append(f"# {build_memory_hook(title, body)}")
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

    text = _read_memory_org_text(ctx)

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

    text = _read_memory_org_text(ctx)
    lines = text.split("\n")

    # 找到条目位置
    found = False
    for i, line in enumerate(lines):
        if match_memory_entry(line, entry_id):
            found = True
            # 向下查找 :UPDATED: 属性行（orgserde 层改值，保留原缩进/间距）
            for j in range(i + 1, min(i + 10, len(lines))):
                new_line = set_memory_prop_line(lines[j], "UPDATED", today())
                if new_line is not None:
                    lines[j] = new_line
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

    text = _read_memory_org_text(ctx)
    lines = text.split("\n")

    # 找到条目起始行
    entry_start = None
    for i, line in enumerate(lines):
        if match_memory_entry(line, entry_id):
            entry_start = i
            break

    if entry_start is None:
        die(f"未找到条目: {entry_id}")

    # 找到条目结束行（下一个 ** 或 * 之前）
    entry_end = entry_start + 1
    while entry_end < len(lines):
        if is_memory_boundary(lines[entry_end]):
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


# ═══════════════════════════════════════════════════════════════════════════════
# N5 事件驱动重验：machine 键变更批量 + 手填过期 + 孤儿（只读列出 + 单条刷新）
# ═══════════════════════════════════════════════════════════════════════════════


def resolve_machine_key() -> str:
    """本机记忆键：[memories].machine_key 非空则用之，否则 hostname。

    import/export/revalidate 共用同一口径：任一入口拿到的键与条目 :MACHINE:
    不一致即视为切机，该条目待重验。
    """
    from agenote import config as _config

    try:
        configured = str(_config.get("memories", "machine_key") or "").strip()
    except KeyError:
        configured = ""
    return configured or socket.gethostname()


def _memory_revalidate(ctx=None) -> None:
    """只读列出待重验条目（不写盘，不动 --stale 输出）。

    三源：machine-changed（SCOPE:machine 的 E 条目 :MACHINE: 与当前键不一致
    → 切机时自然全量命中）、expired（手填 :EXPIRES_AFTER: 已过）、
    orphan（project 索引的 PATH/FILE 已失效）。
    """
    ctx = ctx or default_context()
    if not ctx.memory_org.exists():
        print("(记忆文件不存在)")
        return

    key = resolve_machine_key()
    entries = _iter_memory_entries(_read_memory_org_text(ctx))
    hits: list[tuple[str, str, str]] = []
    for e in entries:
        if e["section"].lower() == "deprecated":
            continue
        props = e["props"]
        scope = (props.get("SCOPE") or "").strip().lower()
        machine = (props.get("MACHINE") or "").strip()
        if e["type"] == "E" and scope == "machine" and machine and machine != key:
            hits.append((e["id"], e["title"], "machine-changed"))
            continue
        exp = parse_memory_date((props.get("EXPIRES_AFTER") or "").strip("[] "))
        if exp is not None and (datetime.now().date() - exp).days >= 0:
            hits.append((e["id"], e["title"], "expired"))
            continue
        if e["kind"] == "index":
            target = (props.get("PATH") or props.get("FILE") or "").strip()
            if target and not Path(target).expanduser().exists():
                hits.append((e["id"], e["title"], "orphan"))

    if not hits:
        print("无待重验记忆")
        return
    for entry_id, title, reason in hits:
        print(f"  ** {entry_id} {title} ({reason})")
    print(f"\n共 {len(hits)} 条待重验（memory --validate <id> 刷新）")


def _memory_validate(entry_id: str, ctx=None) -> None:
    """刷新单条 VALIDATED_AT=[today]（MUTATING）；失败提示 supersede/归档。"""
    ctx = ctx or default_context()
    if not ctx.memory_org.exists():
        die("记忆文件不存在")

    text = _read_memory_org_text(ctx)
    lines = text.split("\n")

    start = next(
        (i for i, line in enumerate(lines) if match_memory_entry(line, entry_id)),
        None,
    )
    if start is None:
        die(f"未找到条目: {entry_id}（若已失效可考虑 supersede 或归档替代）")

    end = start + 1
    while end < len(lines) and not is_memory_boundary(lines[end]):
        end += 1
    for j in range(start + 1, end):
        new_line = set_memory_prop_line(lines[j], "VALIDATED_AT", today())
        if new_line is not None:
            lines[j] = new_line
            break
        if lines[j].strip() == ":END:":
            lines.insert(j, f"   :VALIDATED_AT:  [{today()}]")
            break
    else:
        lines[start + 1 : start + 1] = [
            "   :PROPERTIES:",
            f"   :VALIDATED_AT:  [{today()}]",
            "   :END:",
        ]

    atomic_write(ctx.memory_org, "\n".join(lines))
    print(f"已验证 {entry_id} → {today()}")


def _memory_stale(ctx=None) -> None:
    ctx = ctx or default_context()
    """列出超过 STALE_DAYS 天未更新的条目。"""
    if not ctx.memory_org.exists():
        print("(记忆文件不存在)")
        return

    text = _read_memory_org_text(ctx)
    lines = text.split("\n")
    stale_count = 0
    section = ""  # 当前一级节（deprecated 节下的条目不算陈旧——它们已被归档）

    for i, line in enumerate(lines):
        if re.match(r"^\* ", line):
            section = line[2:].strip()
            continue
        if not match_memory_entry(line):
            continue
        if section == "deprecated":
            continue
        updated = parse_memory_date(memory_prop(lines[i + 1 : i + 10], "UPDATED"))
        if updated is None:
            continue
        if (datetime.now().date() - updated).days > STALE_DAYS:
            print(f"  {line.strip()} (更新于 {updated})")
            stale_count += 1

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

    text = _read_memory_org_text(ctx)
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
                        pm = MEMORY_PROP_RE.match(el)
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

    text = _read_memory_org_text(ctx)
    lines = text.split("\n")

    # 找到条目
    entry_start = None
    for i, line in enumerate(lines):
        if match_memory_entry(line, entry_id):
            entry_start = i
            break

    if entry_start is None:
        die(f"未找到条目: {entry_id}")

    # 找到条目结束行
    entry_end = entry_start + 1
    while entry_end < len(lines):
        if is_memory_boundary(lines[entry_end]):
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

    text = _read_memory_org_text(ctx)
    lines = text.split("\n")

    # 找到项目条目
    found = False
    for i, line in enumerate(lines):
        if match_memory_entry(line, project_name):
            found = True
            # 向下查找并更新/添加 LAST_ACTIVE
            for j in range(i + 1, min(i + 10, len(lines))):
                new_line = set_memory_prop_line(lines[j], "LAST_ACTIVE", today())
                if new_line is not None:
                    lines[j] = new_line
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


# ═══════════════════════════════════════════════════════════════════════════════
# N4 冲突裁决：冲突队列只读 + supersede 双侧写
# ═══════════════════════════════════════════════════════════════════════════════


def _memory_conflicts(ctx=None) -> None:
    """只读列出 .memory-conflicts.json（N2 落盘结构，tolerant 消费）。"""
    import json as _json

    from agenote import projector as _projector  # lazy：与 --export 同因

    ctx = ctx or default_context()
    path = _projector.conflicts_path(ctx)
    if not path.exists():
        print("(无冲突)")
        return
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        die(f"冲突队列文件损坏: {path}")
    items = data if isinstance(data, list) else data.get("conflicts", [])
    if not items:
        print("(无冲突)")
        return
    print(_json.dumps(items, ensure_ascii=False, indent=1))


def _set_prop_in_block(block: list[str], key: str, value: str, bracket: bool = False) -> list[str]:
    """条目块内置/改属性：已存在改值（orgserde 层保留缩进），否则 :END: 前插入。"""
    out = list(block)
    val = f"[{value}]" if bracket else value
    for i, ln in enumerate(out):
        new_line = set_memory_prop_line(ln, key, value)
        if new_line is not None:
            out[i] = new_line
            return out
        pm = MEMORY_PROP_RE.match(ln)
        if pm and pm.group(1).upper() == key.upper():
            indent = ln[: len(ln) - len(ln.lstrip())]
            out[i] = f"{indent}:{key}:      {val}"
            return out
    for i, ln in enumerate(out):
        if ln.strip() == ":END:":
            indent = ln[: len(ln) - len(ln.lstrip())]
            out.insert(i, f"{indent}:{key}:      {val}")
            return out
    # 无属性抽屉：标题行后补抽屉
    out[1:1] = ["   :PROPERTIES:", f"   :{key}:      {val}", "   :END:"]
    return out


def _memory_supersede(new_id: str, old_id: str, ctx=None) -> None:
    """N4 裁决落地一步完成：新条目写 SUPERSEDES + 刷 VALIDATED_AT，
    旧条目记 SUPERSEDED_BY 后移入 deprecated 节。单次 atomic_write。

    裁决方向（谁胜）由 agent/人按 human>agent、同级比 VALIDATED_AT 定，CLI 只落地。
    """
    ctx = ctx or default_context()
    if new_id == old_id:
        die("supersede 新旧条目不能相同")
    if not ctx.memory_org.exists():
        die("记忆文件不存在")

    text = _read_memory_org_text(ctx)
    lines = text.split("\n")

    def _find(entry_id: str, hay: list[str]) -> tuple[int, int] | None:
        start = None
        for i, ln in enumerate(hay):
            if match_memory_entry(ln, entry_id):
                start = i
                break
        if start is None:
            return None
        end = start + 1
        while end < len(hay) and not is_memory_boundary(hay[end]):
            end += 1
        return (start, end)

    new_span = _find(new_id, lines)
    if new_span is None:
        die(f"未找到新条目: {new_id}")
    old_span = _find(old_id, lines)
    if old_span is None:
        die(f"未找到旧条目: {old_id}")

    # 1) 新条目：SUPERSEDES + VALIDATED_AT（就地改）
    ns, ne = new_span
    new_block = _set_prop_in_block(lines[ns:ne], "SUPERSEDES", old_id)
    new_block = _set_prop_in_block(new_block, "VALIDATED_AT", today(), bracket=True)
    lines[ns:ne] = new_block

    # 2) 旧条目：重定位后记 SUPERSEDED_BY，搬 deprecated（ mirrors _memory_archive）
    old_span = _find(old_id, lines)
    assert old_span is not None
    os_, oe = old_span
    old_block = _set_prop_in_block(lines[os_:oe], "SUPERSEDED_BY", new_id)
    del lines[os_:oe]
    sections = _parse_memory_sections("\n".join(lines))
    dep = next((ents[0] for name, ents in sections.items()
                if "deprecated" in name.lower()), None)
    if dep:
        insert = _find_section_end(lines, dep[0])
    else:
        lines.append("")
        lines.append("* deprecated")
        insert = len(lines)
    for k, el in enumerate(old_block):
        lines.insert(insert + k, el)

    atomic_write(ctx.memory_org, "\n".join(lines))
    print(f"已裁决 {old_id} → {new_id}（旧条目入 deprecated）")

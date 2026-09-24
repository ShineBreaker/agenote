# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.orgserde — Org-mode 序列化/反序列化。

从 core.py 拆出（ADR-0003）：Org 属性解析（读）与 facts→Org 文档渲染（写）
归于一处。零 agenote 依赖（纯文本函数）；orgfmt 保持独立为交互式格式美化器。

- parse_org_prop / set_org_prop / delete_org_prop：顶层属性抽屉读写
- render_facts_org：ReconciledFact 列表 → Org 文档（原 run_extract 内联渲染）
"""

from __future__ import annotations

import re
from datetime import datetime

from agenote import config
from agenote.core import PublicError

# Org 渲染正文截断（config [extract].trunc_org_render 覆盖）
ORG_RENDER_TRUNC = int(config.get("extract", "trunc_org_render"))


class OrgPropertyDrawerError(PublicError):
    """卡片缺少可写的顶层 PROPERTIES 抽屉。"""


# Org headline 后、属性抽屉前允许出现 planning 行；它们仍是 headline 元数据，
# 不能误判为正文示例，也不能遮住紧随其后的顶层 PROPERTIES 抽屉。
_PLANNING_LINE = re.compile(
    r"^(?:SCHEDULED|DEADLINE|CLOSED|LAST_REPEAT):[ \t]+.*$", re.IGNORECASE
)


# ═══════════════════════════════════════════════════════════════════════════════
# 读：PROPERTIES / 标题解析
# ═══════════════════════════════════════════════════════════════════════════════


def _heading_probe(line: str, index: int) -> str:
    """匹配用行文本：首行 UTF-8 BOM 会让 ``^\\* `` 失配，只在匹配时剥离，
    写回仍使用原始行，BOM 字节不会被顺手改掉。"""
    return line[1:] if index == 0 and line.startswith("\ufeff") else line


def _top_property_drawer(
    lines: list[str],
) -> tuple[int, int, str] | None:
    """返回顶层属性抽屉的起止行和缩进；正文示例不算元数据。"""
    heading = next(
        (
            i
            for i, line in enumerate(lines)
            if re.match(r"^\* ", _heading_probe(line, i))
        ),
        None,
    )
    if heading is None:
        return None
    for start in range(heading + 1, len(lines)):
        line = lines[start].rstrip("\r\n")
        if not line.strip():
            continue
        if _PLANNING_LINE.fullmatch(line):
            continue
        drawer = re.match(r"^([ \t]*):PROPERTIES:[ \t]*$", line)
        if not drawer or drawer.group(1):
            # 顶层 headline 的属性抽屉必须在第 0 列；缩进的 drawer 属于正文/
            # 子标题，不能被当作卡片元数据。
            return None
        indent = drawer.group(1)
        for end in range(start + 1, len(lines)):
            if re.fullmatch(
                rf"{re.escape(indent)}:END:[ \t]*", lines[end].rstrip("\r\n")
            ):
                return start, end, indent
        return None
    return None


def has_top_property_drawer(content: str) -> bool:
    """判断内容是否含有可写的顶层 PROPERTIES 抽屉。"""
    return _top_property_drawer(content.splitlines()) is not None


def parse_org_prop(content: str, key: str) -> str:
    """从顶层卡片的 PROPERTIES 抽屉中提取属性值，忽略正文示例。"""
    lines = content.splitlines()
    drawer = _top_property_drawer(lines)
    if drawer is None:
        return ""
    start, end, indent = drawer
    for prop_line in lines[start + 1 : end]:
        prop = re.match(
            rf"^[ \t]*:{re.escape(key)}:[ \t]*(.*?)[ \t]*$",
            prop_line,
            re.IGNORECASE,
        )
        if prop and prop.group(1):
            return prop.group(1)
    return ""


def set_org_prop(content: str, key: str, value: str) -> str:
    """更新顶层属性抽屉中的字段；字段缺失时在 :END: 前插入。"""
    if "\n" in value or "\r" in value:
        # 换行会把伪 :END: 注入抽屉，使其提前闭合、后续字段静默脱离元数据。
        raise ValueError("属性值不能包含换行符")
    lines = content.splitlines(keepends=True)
    drawer = _top_property_drawer(lines)
    if drawer is None:
        raise OrgPropertyDrawerError("卡片缺少顶层 PROPERTIES 抽屉")
    start, end, indent = drawer
    pattern = re.compile(
        rf"^({re.escape(indent)}:{re.escape(key)}:)([ \t]*)(.*?)(\r?\n)?$",
        re.IGNORECASE,
    )
    for index in range(start + 1, end):
        match = pattern.match(lines[index])
        if match:
            spacing = match.group(2) or " "
            newline = match.group(4) or ""
            lines[index] = f"{match.group(1)}{spacing}{value}{newline}"
            return "".join(lines)
    lines.insert(end, f"{indent}:{key}: {value}\n")
    return "".join(lines)


def delete_org_prop(content: str, key: str) -> str:
    """只删除顶层属性抽屉中的字段，正文示例保持原样。"""
    lines = content.splitlines(keepends=True)
    drawer = _top_property_drawer(lines)
    if drawer is None:
        raise OrgPropertyDrawerError("卡片缺少顶层 PROPERTIES 抽屉")
    start, end, indent = drawer
    pattern = re.compile(rf"^{re.escape(indent)}:{re.escape(key)}:[ \t]*", re.IGNORECASE)
    kept = lines[: start + 1]
    kept.extend(
        line
        for line in lines[start + 1 : end]
        if not pattern.match(line)
    )
    kept.extend(lines[end:])
    return "".join(kept)


def _parse_float_prop(content: str, key: str, default: float) -> float:
    """从 PROPERTIES 解析浮点字段，缺失返回 default。"""
    raw = parse_org_prop(content, key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_int_prop(content: str, key: str, default: int) -> int:
    """从 PROPERTIES 解析整数字段，缺失返回 default。"""
    raw = parse_org_prop(content, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def read_org_title(content: str) -> str:
    """从 Org 内容中提取一级标题文本（去掉 DONE/TODO 前缀）。

    首行 BOM 复用 _heading_probe 剥离后再匹配，BOM 卡片的标题不再降级 unknown
    （否则 touch/merge 等每次策展都会把脏 title 写进 index.json）。
    """
    for index, line in enumerate(content.splitlines()):
        m = re.match(r"^\* (?:DONE|TODO) (.+)", _heading_probe(line, index))
        if m:
            return m.group(1).strip()
    return "unknown"


# ═══════════════════════════════════════════════════════════════════════════════
# 写：facts → Org 文档
# ═══════════════════════════════════════════════════════════════════════════════


def render_facts_org(
    facts: list,
    *,
    source: str,
    date: str = "",
    limit: int | None = None,
) -> str:
    """把 ReconciledFact（鸭子类型）列表渲染为 Org 文档。

    原 extract/base.py run_extract 的内联渲染段；输出格式保持逐字节兼容：
    #+TITLE/#+DATE/#+SOURCE/#+TOTAL/#+FILTERED_BY_DATE/#+LIMIT 头 + 每条 fact
    一个一级条目（PROPERTIES + 截断 ORG_RENDER_TRUNC 字的正文）。
    """
    lines: list[str] = [
        f"#+TITLE: {source} conversations",
        f"#+DATE: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"#+SOURCE: {source}",
        f"#+TOTAL: {len(facts)}",
        f"#+FILTERED_BY_DATE: {date or 'no'}",
        f"#+LIMIT: {limit if limit else 'unlimited'}",
        "",
    ]
    for f in facts:
        lines.append(f"* {f.title}")
        lines.append(":PROPERTIES:")
        lines.append(f":ID: {f.id}")
        lines.append(f":CATEGORY: {f.category}")
        lines.append(f":WEIGHT: {f.weight}")
        if f.timestamp:
            lines.append(f":TIMESTAMP: {f.timestamp}")
        lines.append(":END:")
        lines.append("")
        lines.append(f.content[:ORG_RENDER_TRUNC])
        lines.append("")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# S6：MEMORY.org 条目层（memory.py 行级正则统一走这里，消除正则散布）
#
# 条目形状：`** <ID> [标题]` + 缩进 PROPERTIES + `# 钩子` + 正文。
# 宽容解析：未知属性读时忽略，旧条目无钩子不报错（展示降级用标题）。
# ═══════════════════════════════════════════════════════════════════════════════

#: 二级条目标题：`** <ID> [标题]`（ID 无空格，标题可选）
MEMORY_ENTRY_RE = re.compile(r"^\*\* (\S+)(?:\s+(.*?))?\s*$")
#: 条目边界：任意 `*`/`**` 标题行（归档移动时切块用）
MEMORY_BOUNDARY_RE = re.compile(r"^\*\*?\s+")
#: 条目属性行：`   :KEY:  value`
MEMORY_PROP_RE = re.compile(r"^\s*:([A-Za-z0-9_]+):\s*(.*?)\s*$")
#: 钩子行：`# 一句话钩子`（description 召回语义）
MEMORY_HOOK_RE = re.compile(r"^#\s?(\S.*)?\s*$")
#: 钩子长度上限字符数
HOOK_MAX_CHARS = 120


def match_memory_entry(line: str, entry_id: str | None = None) -> re.Match | None:
    """匹配 MEMORY.org 二级条目标题；entry_id 给定时比对 ID（保留旧 `\b` 语义）。"""
    m = MEMORY_ENTRY_RE.match(line)
    if not m:
        return None
    if entry_id is not None and not re.match(rf"{re.escape(entry_id)}\b", m.group(1)):
        return None
    return m


def is_memory_boundary(line: str) -> bool:
    """是否为条目切块边界（任意 `*`/`**` 标题行）。"""
    return MEMORY_BOUNDARY_RE.match(line) is not None


def memory_prop(entry_lines: list[str], key: str) -> str:
    """条目块内取属性值（大小写不敏感，遇 :END: 停止）；缺失返回 ""。"""
    for line in entry_lines:
        if line.strip() == ":END:":
            break
        m = MEMORY_PROP_RE.match(line)
        if m and m.group(1).upper() == key.upper():
            return m.group(2)
    return ""


def set_memory_prop_line(line: str, key: str, value: str) -> str | None:
    """属性行就地改值（保留原缩进/间距，值整体换成 [value]）；非该 key 行返回 None。"""
    m = re.match(rf"^(\s*:{re.escape(key)}:\s*)\[.*?\](\s*)$", line, re.IGNORECASE)
    if not m:
        return None
    return f"{m.group(1)}[{value}]{m.group(2)}"


def read_memory_hook(entry_lines: list[str]) -> str:
    """读条目钩子：:END: 之后首个非空行是 `# ...` 则取之（截断上限），否则 ""。"""
    past_end = False
    for line in entry_lines:
        stripped = line.strip()
        if not past_end:
            if stripped == ":END:":
                past_end = True
            continue
        if not stripped:
            continue
        m = MEMORY_HOOK_RE.match(stripped)
        return (m.group(1) or "").strip()[:HOOK_MAX_CHARS] if m else ""
    return ""


def entry_hook_or_title(entry_lines: list[str], title: str) -> str:
    """索引/投影用展示行：有钩子用钩子，旧条目降级用标题。"""
    return read_memory_hook(entry_lines) or title


def build_memory_hook(title: str, body: str) -> str:
    """新条目钩子：正文首个非空行（去 `#` 前缀），无正文用标题；截断上限。"""
    candidate = ""
    for line in (body or "").splitlines():
        if line.strip():
            candidate = line.strip().lstrip("#").strip()
            break
    return (candidate or title).strip()[:HOOK_MAX_CHARS]


def parse_memory_date(value: str):
    """`[2026-09-24]` / `2026-09-24 ...` → date；缺失/非法返回 None。"""
    if not value or not value.split():
        return None
    token = re.sub(r"[\[\]]", "", value).split()[0]
    try:
        return datetime.strptime(token, "%Y-%m-%d").date()
    except ValueError:
        return None


def unverified_tag(days: int | None, stale_days: int) -> str:
    """S7 时效标记：超 stale_days 未验证返回 `(unverified Nd)`，否则 ""。"""
    if days is None:
        return "(unverified ?d)"
    return f"(unverified {days}d)" if days > stale_days else ""

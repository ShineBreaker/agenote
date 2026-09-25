# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

"""orgfmt — 通用 org-mode 格式化核心。

零内部依赖（仅标准库），既被独立 `orgfmt` CLI 使用，也被 agenote import。
所有函数纯函数化，format_org() 幂等（连续跑两次第二次无变更）。

设计分层：
  - 通用规则（默认）：属性列对齐、block 大小写、affiliated keyword 紧贴、
    空行规范化、行内标记 PRE/POST 间距、表格列对齐
  - 中文技术文档规范（默认）：全角空格、全角标点间距、中英文间距、破折号、
    连续感叹号、序数词、数值与单位间距；规范来源为 zh-tech-doc-style-guide
    skill，只覆盖机械可判定项，语义级规则（的/地/得、顿号误用、繁体混用）
    以「待人工」lint 形式报告，不改文本
  - strict 规则（agenote 卡片专用）：Markdown→Org 6 条转换、fingerprint 尾随空格

管线顺序（format_org 内）：
  1. _normalize_blocks      — block 大小写统一（建立状态机，供后续阶段跳过代码块）
  2. _md_to_org             — [strict] Markdown→Org 转换（需在非代码块内）
  3. _fix_inline_markers    — 行内标记 PRE/POST 间距
  4. _align_properties      — :PROPERTIES: drawer 列对齐
  5. _normalize_fingerprint — [strict] fingerprint 行尾随空格
  6. _fix_affiliated        — affiliated keyword 紧贴 block
  7. _zh_style              — 中文技术文档规范（只改行内容，须在表格对齐前）
  8. _align_tables          — 表格列对齐（CJK 宽度）
  9. _normalize_blank_lines — 空行规则（最后跑，基于已规范化的结构）
"""

import os
import re
import sys
import unicodedata
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════════════
# 公开接口
# ═══════════════════════════════════════════════════════════════════════════════


def format_org(text: str, *, strict: bool = False) -> tuple[str, list[str]]:
    """格式化 org 文本，返回 (新文本, 变更说明列表)。幂等。

    Args:
        text: org 文件全文
        strict: 启用 agenote 卡片专用规则（MD→Org、fingerprint 清理）
    """
    changes: list[str] = []

    text, c = _normalize_blocks(text)
    changes += c

    if strict:
        text, c = _md_to_org(text)
        changes += c

    text, c = _fix_inline_markers(text)
    changes += c

    text, c = _align_properties(text)
    changes += c

    if strict:
        text, c = _normalize_fingerprint(text)
        changes += c

    text, c = _fix_affiliated(text)
    changes += c

    text, c = _zh_style(text)
    changes += c

    text, c = _align_tables(text)
    changes += c

    text, c = _normalize_blank_lines(text)
    changes += c

    return text, changes


def format_file(
    path: str | Path, *, strict: bool = False, dry_run: bool = False
) -> list[str]:
    """格式化单个文件，返回变更说明列表。

    Args:
        path: 文件路径
        strict: 启用 agenote 专用规则
        dry_run: True 只检查不写盘
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    new_text, changes = format_org(text, strict=strict)
    if changes and not dry_run:
        p.write_text(new_text, encoding="utf-8")
    return changes


def cmd_format(args, ctx=None) -> None:
    """agenote format 子命令：格式化卡片（默认直接写盘）。

    agenote 域默认 strict=True（一次完成全部可自动化的格式化）。
    --check 只检查不写盘；目标文件缺省时扫全部 experiences。
    """
    import argparse  # 局部 import，避免顶层依赖

    # 延迟 import ctx，避免 orgfmt.py 顶层依赖 agenote.core（保持零内部依赖）
    if ctx is None:
        from agenote.core import default_context

        ctx = default_context()

    target_files = getattr(args, "files", None) or []
    if not target_files:
        target_files = sorted(
            str(f) for f in ctx.experiences.rglob("*.org") if not f.is_symlink()
        )
    if not target_files:
        print("未找到 .org 文件")
        return

    do_check = getattr(args, "check", False)
    total_changes = 0
    files_changed = 0
    failed_files = 0
    reports: list[tuple[str, list[str]]] = []
    for filepath in target_files:
        try:
            changes = format_file(filepath, strict=True, dry_run=do_check)
        except OSError as exc:
            print(
                f"错误 {filepath}: 读取或写入失败（{type(exc).__name__}）",
                file=sys.stderr,
            )
            failed_files += 1
            continue
        except (KeyboardInterrupt, GeneratorExit, SystemExit):
            raise
        except BaseException as exc:
            print(
                f"错误 {filepath}: 操作失败（{type(exc).__name__}）",
                file=sys.stderr,
            )
            failed_files += 1
            continue
        if changes:
            total_changes += len(changes)
            files_changed += 1
            import os

            basename = os.path.basename(filepath)
            reports.append((basename, changes))

    action = "检查" if do_check else "格式化"
    # 先打印已完成文件的报告与汇总再退出：失败时用户仍需要知道
    # 哪些文件已被实际写盘、改了什么（SystemExit 不会吞掉这些信息）。
    for basename, changes in reports:
        print(f"\n{basename} ({len(changes)} 项):")
        for ch in changes:
            print(ch)
    print(
        f"\n{action}完成: {files_changed}/{len(target_files)} 个文件有变更, "
        f"共 {total_changes} 处"
    )
    if failed_files:
        raise SystemExit(1)


# ═══════════════════════════════════════════════════════════════════════════════
# SPDX header 规范化（紧凑化）
# ═══════════════════════════════════════════════════════════════════════════════

# SPDX header 行的特征（每行都是 # 注释）：
#   - # SPDX-FileCopyrightText: ...
#   - # SPDX-License-Identifier: ...
#   - # SPDX-... (其他 SPDX 字段)
#   - # （单独的空注释行，作为视觉分隔）
_SPDX_LINE = re.compile(r"^#\s*(SPDX-[A-Za-z-]+:|)\s*$")


def _compact_spdx_header(lines: list[str]) -> tuple[list[str], bool]:
    """识别并紧凑化文件开头的 SPDX header。

    识别规则：
      - 文件首行必须是 # SPDX-... 或 #（单独注释行）
      - 后续连续行（中间可有空行）必须都是 # SPDX-... 或 #（空注释行）
      - 直到遇到非 # 开头行 或第一个空行后接非 SPDX 的行

    行为：
      - 移除 SPDX header 内部的空行（紧凑化为 1 个连续段）
      - 如果原本没有空行：原样返回（不变更）
      - 如果原本有空行：紧凑化后返回

    返回 (新行列表, 是否做了修改)。
    """
    # 找 header 结束位置
    # header 范围：开头连续的 # SPDX-... / # 行（中间可有空行），直到遇到第一个非 # 行
    end = 0
    in_header = False
    has_spdx_marker = False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.startswith("#") and (
            s.startswith("# SPDX") or s == "#" or s.startswith("#SPDX")
        ):
            in_header = True
            if "SPDX" in s:
                has_spdx_marker = True
            end = i + 1
        elif in_header and s == "":
            # header 内的空行：跳过，继续看下一行
            continue
        else:
            # header 结束
            break

    # 没有 SPDX 标记 → 不是 SPDX header，不处理
    if not has_spdx_marker:
        return lines, False

    # 提取 header 内的非空行
    header_lines = [ln for ln in lines[:end] if ln.strip()]
    # 提取 header 后内容（从 end 开始）
    rest = lines[end:]

    # 检查是否实际有变更（原本有空行 vs 紧凑）
    original_non_blank_count = sum(1 for ln in lines[:end] if ln.strip())
    if len(header_lines) == original_non_blank_count and len(header_lines) == end:
        # 原本就紧凑，没有空行
        return lines, False

    # 紧凑化：header 内的 # 行紧贴，然后与正文之间保留 1 空行
    new_lines = list(header_lines)

    # header 后第一个非空行之前如果有空行 → 移除多余空行（保留 1 个）
    rest_stripped_leading_blanks = True
    out_rest: list[str] = []
    seen_first_nonblank = False
    for ln in rest:
        if not seen_first_nonblank:
            if ln.strip() == "":
                continue  # 跳过 header 后开头的空行（下方会补 1 个）
            seen_first_nonblank = True
        out_rest.append(ln)
    new_lines.append("")  # header 与正文之间 1 空行
    new_lines.extend(out_rest)

    return new_lines, True


# ═══════════════════════════════════════════════════════════════════════════════
# 辅助：CJK 显示宽度
# ═══════════════════════════════════════════════════════════════════════════════


def _disp_width(s: str) -> int:
    """计算字符串的显示宽度（CJK 全角字符算 2 列）。"""
    w = 0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            w += 2
        else:
            w += 1
    return w


# ═══════════════════════════════════════════════════════════════════════════════
# 代码块 / block 识别（跨阶段共享的状态机辅助）
# ═══════════════════════════════════════════════════════════════════════════════

# 匹配 #+begin_src / #+begin_example / #+begin_quote 等所有 block 起始
_BLOCK_BEGIN = re.compile(r"^(\s*)#\+begin_(\w+)\b", re.IGNORECASE)
_BLOCK_END = re.compile(r"^(\s*)#\+end_(\w+)\b", re.IGNORECASE)
# Markdown ``` 围栏（strict 模式下会被转成 block）
_MD_FENCE = re.compile(r"^(\s*)```(\w*)\s*$")


def _iter_lines_with_block_state(text: str):
    """逐行生成 (行内容, 行号, in_block)。

    in_block 标记该行是否位于 #+begin_src/.../#+end_src 或 ``` 围栏内部。
    围栏起始行本身 in_block=False（它是边界），围栏内内容 True。
    """
    in_org = False
    in_md = False
    for i, line in enumerate(text.split("\n")):
        if _BLOCK_BEGIN.match(line):
            in_org = True
            yield line, i, False
            continue
        if _BLOCK_END.match(line):
            in_org = False
            yield line, i, False
            continue
        if _MD_FENCE.match(line):
            # 围栏是边界切换：第一次进入，第二次退出
            in_md = not in_md
            yield line, i, False
            continue
        yield line, i, in_org or in_md


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 1：block 大小写统一
# ═══════════════════════════════════════════════════════════════════════════════


def _normalize_blocks(text: str) -> tuple[str, list[str]]:
    """统一 #+begin_xxx / #+end_xxx 为小写（Org 惯例）。

    例：#+BEGIN_SRC python → #+begin_src python
    保留 #+begin_ 后的参数（语言、switches、header args）原样，只规范化关键字大小写。
    """
    changes: list[str] = []
    lines = text.split("\n")
    result = []

    for i, line in enumerate(lines):
        new = line
        m = _BLOCK_BEGIN.match(line)
        if m:
            indent, _btype = m.group(1), m.group(2).lower()
            # 重写为 #+begin_<type>（小写），保留 indent 与后续参数
            rest = line[m.end() :]  # #+begin_src 之后的全部内容（含语言名）
            new = f"{indent}#+begin_{_btype}{rest}"
        else:
            m = _BLOCK_END.match(line)
            if m:
                indent = m.group(1)
                btype = m.group(2).lower()
                rest = line[m.end() :]
                new = f"{indent}#+end_{btype}{rest}"

        if new != line:
            changes.append(f"  行 {i + 1}: block 关键字大小写 → 小写")
        result.append(new)

    return "\n".join(result), changes


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 2 [strict]：Markdown → Org 转换（迁移自 lint.py）
# ═══════════════════════════════════════════════════════════════════════════════


def _md_to_org(text: str) -> tuple[str, list[str]]:
    """Markdown 残留语法转 Org（仅在非代码块内）。

    迁移自 lint.py 的 6 条规则。与原实现保持行为一致。
    """
    changes: list[str] = []
    lines = text.split("\n")
    result = []
    in_code_block = False  # 追踪 ``` 代码块状态
    in_org_code_block = False  # 追踪 #+begin_src ... #+end_src 代码块状态

    i = 0
    while i < len(lines):
        line = lines[i]

        # ── 规则 1: ```lang ... ``` → #+begin_src lang ... #+end_src ──
        code_match = _MD_FENCE.match(line)
        if code_match:
            indent = code_match.group(1)
            lang = code_match.group(2)
            if not in_code_block:
                in_code_block = True
                new_line = f"{indent}#+begin_src {lang}".rstrip()
                if new_line != line:
                    changes.append(f"  行 {i + 1}: ```{lang} → #+begin_src {lang}")
                result.append(new_line)
                i += 1
                continue
            else:
                in_code_block = False
                new_line = f"{indent}#+end_src"
                changes.append(f"  行 {i + 1}: ``` → #+end_src")
                result.append(new_line)
                i += 1
                continue

        # ── 追踪 Org 代码块状态 ──
        if _BLOCK_BEGIN.match(line):
            in_org_code_block = True
        elif _BLOCK_END.match(line):
            in_org_code_block = False

        # ── 规则 2-5: 只在非代码块内生效 ──
        if not in_code_block and not in_org_code_block:
            stripped = line.lstrip()

            # 排除 Org 标题模式（** 或 *** 开头后跟空格）
            if re.match(r"^\*+\s", stripped):
                result.append(line)
                i += 1
                continue

            # 排除注释行（# 开头）—— # 注释行不是 Markdown heading
            # （SPDX header / Emacs modeline / 普通注释等都属此类）
            if stripped.startswith("#"):
                result.append(line)
                i += 1
                continue

            # 行内 **bold** → *bold*
            new_line = re.sub(r"\*\*(.+?)\*\*", r"*\1*", line)
            if new_line != line:
                changes.append(f"  行 {i + 1}: **bold** → *bold*")
                line = new_line

            # Markdown heading → Org heading
            heading_match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading_match:
                level = len(heading_match.group(1)) + 1
                title = heading_match.group(2)
                new_line = f"{'*' * level} {title}"
                changes.append(f"  行 {i + 1}: Markdown heading → Org heading")
                line = new_line

            # `- list` → `+ list`
            list_match = re.match(r"^(\s*)(- )(\S)", line)
            if list_match and not line.lstrip().startswith("--"):
                indent = list_match.group(1)
                content = line.lstrip()[2:]
                new_line = f"{indent}+ {content}"
                changes.append(f"  行 {i + 1}: `- ` → `+ `")
                line = new_line

            # `inline code` → ~inline code~
            if "`" in line and "```" not in line:
                new_line = re.sub(r"`([^`\n]+?)`", r"~\1~", line)
                if new_line != line:
                    count = len(re.findall(r"`[^`\n]+?`", line))
                    changes.append(f"  行 {i + 1}: `code` → ~code~ (×{count})")
                    line = new_line

        result.append(line)
        i += 1

    return "\n".join(result), changes


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 3：行内标记 PRE/POST 间距（迁移自 lint.py）
# ═══════════════════════════════════════════════════════════════════════════════

# Org 强调标记 PRE 合法字符（行首也合法）
_MARKER_PRE_VALID = set(" \t\n({['\"`")
# Org 强调标记 POST 合法字符（行尾也合法）
_MARKER_POST_VALID = set(' \t\n.,:;!?\'"" )}]\\-')


def _fix_marker_spacing(line: str, marker_char: str) -> tuple[str, int, int]:
    """检查并修复 ~...~ 或 *...* 标记的外部空格。

    使用 Org 的 PRE/POST 规则判断是否需要插入空格。
    相邻标记共享边界空格，避免重复插入。

    Returns (fixed_line, pre_fix_count, post_fix_count).
    """
    escaped = re.escape(marker_char)
    pattern = escaped + r"[^" + escaped + r"\n]+" + escaped
    markers = [
        (m.start(), m.end())
        for m in re.finditer(pattern, line)
        if m.group()[1:-1].strip()
    ]

    if not markers:
        return line, 0, 0

    inserts: list[tuple[int, str]] = []
    pre_count = 0
    post_count = 0

    for start, end in markers:
        if (
            start > 0
            and line[start - 1] not in _MARKER_PRE_VALID
            and line[start - 1] != marker_char
        ):
            if not any(pos == start for pos, _ in inserts):
                inserts.append((start, " "))
                pre_count += 1

        if end < len(line) and line[end] not in _MARKER_POST_VALID:
            if not any(pos == end for pos, _ in inserts):
                inserts.append((end, " "))
                post_count += 1

    if not inserts:
        return line, 0, 0

    result = list(line)
    for pos, char in sorted(inserts, reverse=True):
        result.insert(pos, char)

    return "".join(result), pre_count, post_count


def _fix_inline_markers(text: str) -> tuple[str, list[str]]:
    """对全文非代码块行应用 ~...~ / *...* 标记的 PRE/POST 间距修复。"""
    changes: list[str] = []
    result = []

    for line, idx, in_block in _iter_lines_with_block_state(text):
        if in_block:
            result.append(line)
            continue
        # 跳过 Org 标题行（* 开头）避免误伤
        if re.match(r"^\*+\s", line):
            result.append(line)
            continue

        new_line = line
        if "~" in new_line:
            new_line, pre_n, post_n = _fix_marker_spacing(new_line, "~")
            if pre_n:
                changes.append(f"  行 {idx + 1}: ~code~ 前缺空格 (×{pre_n})")
            if post_n:
                changes.append(f"  行 {idx + 1}: ~code~ 后缺空格 (×{post_n})")

        if "*" in new_line:
            new_line, pre_n, post_n = _fix_marker_spacing(new_line, "*")
            if pre_n:
                changes.append(f"  行 {idx + 1}: *bold* 前缺空格 (×{pre_n})")
            if post_n:
                changes.append(f"  行 {idx + 1}: *bold* 后缺空格 (×{post_n})")

        result.append(new_line)

    return "\n".join(result), changes


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 4：属性 drawer 列对齐
# ═══════════════════════════════════════════════════════════════════════════════

# 匹配 drawer 内的属性行：:KEY:   value  或  :KEY:
_PROP_LINE = re.compile(r"^:(\w+):(\s*)(.*)$")


def _align_properties(text: str) -> tuple[str, list[str]]:
    """对齐 :PROPERTIES: ... :END: drawer 内的属性列。

    规则：
    - 所有 :KEY: 的值起始列对齐到 drawer 内最长 KEY + 1 空格
    - 空值字段（如 :EFFORT:）保持无尾随空格
    - 只作用于 :PROPERTIES: drawer（其他 drawer 如 :LOGBOOK: 不动）

    例：
      :ID:       20260701-120000
      :ENTRY_TYPE: note
      →
      :ID:         20260701-120000
      :ENTRY_TYPE: note
    """
    changes: list[str] = []
    lines = text.split("\n")
    result = []
    i = 0

    while i < len(lines):
        line = lines[i]
        # 检测 :PROPERTIES: drawer 起始
        if line.strip() == ":PROPERTIES:":
            drawer_start = i
            drawer_lines = [line]
            j = i + 1
            # 收集 drawer 内行直到 :END:
            while j < len(lines) and lines[j].strip() != ":END:":
                drawer_lines.append(lines[j])
                j += 1
            if j < len(lines):  # 找到 :END:
                drawer_lines.append(lines[j])  # :END: 行
                aligned, n = _align_one_drawer(drawer_lines, drawer_start)
                changes += n
                result.extend(aligned)
                i = j + 1
                continue
            else:
                # 未闭合 drawer，原样输出
                result.extend(drawer_lines)
                i = j
                continue
        else:
            result.append(line)
            i += 1

    return "\n".join(result), changes


def _align_one_drawer(
    drawer_lines: list[str], base_idx: int
) -> tuple[list[str], list[str]]:
    """对齐单个 drawer 的属性行（drawer_lines 含 :PROPERTIES: 和 :END:）。"""
    changes: list[str] = []
    # 解析每行：是否为属性行、key、原始值
    parsed = []  # [(原行, 是否属性, key, 值)]
    max_key_len = 0
    for ln in drawer_lines:
        m = _PROP_LINE.match(ln)
        if ln.strip() in (":PROPERTIES:", ":END:"):
            parsed.append((ln, False, None, None))
            continue
        if m:
            key = m.group(1)
            val = m.group(3).rstrip()  # 去尾随空格
            parsed.append((ln, True, key, val))
            if len(key) > max_key_len:
                max_key_len = len(key)
        else:
            # drawer 内非属性行（少见），原样保留
            parsed.append((ln, False, None, None))

    # 生成对齐后的行：":KEY:" + 空格补齐到 max_key_len + 1 个空格 + 值
    result = []
    for orig, is_prop, key, val in parsed:
        if is_prop:
            # 空值：":KEY:" 无尾随空格
            if not val:
                new = f":{key}:"
            else:
                # 对齐：":KEY:" + (max_key_len - len(key) + 1) 个空格 + 值
                pad = max_key_len - len(key) + 1
                new = f":{key}:" + (" " * pad) + val
            if new != orig:
                changes.append(f"  行 {base_idx + len(result) + 1}: 属性 :{key}: 对齐")
            result.append(new)
        else:
            result.append(orig)

    return result, changes


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 5 [strict]：fingerprint 行尾随空格清理
# ═══════════════════════════════════════════════════════════════════════════════

# fingerprint 行：:cat:type:owner:tech:entry:: （以 : 开头、:: 结尾、含字段）
# tech 字段可能含分号、连字符等，故用宽松字符类（排除空格与下一个 : 的歧义）
_FINGERPRINT = re.compile(r"^(:[^:\s][^:]*(:[^:]+)*::)\s*$")


def _normalize_fingerprint(text: str) -> tuple[str, list[str]]:
    """清理卡片 fingerprint 行（:END: 后的 :cat:type:owner:tech:entry::）的尾随空格。

    不改字段值（字段数校验留给 lint），只保证格式干净：去尾随空格、确保 :: 结尾。
    """
    changes: list[str] = []
    lines = text.split("\n")
    result = []
    saw_end = False

    for i, line in enumerate(lines):
        new = line
        if line.strip() == ":END:":
            saw_end = True
            result.append(line)
            continue
        if saw_end and _FINGERPRINT.match(line):
            # 去尾随空格
            stripped = line.rstrip()
            if stripped != line:
                changes.append(f"  行 {i + 1}: fingerprint 行尾随空格")
            new = stripped
            saw_end = False  # 只处理 :END: 后第一行
        else:
            # 其他行重置 saw_end（避免误判非 drawer 的 :END:）
            if line.strip() != ":END:":
                saw_end = False
        result.append(new)

    return "\n".join(result), changes


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 6：affiliated keywords 紧贴 block
# ═══════════════════════════════════════════════════════════════════════════════

# affiliated keywords：必须紧贴下方 #+begin_* block
_AFFILIATED = re.compile(
    r"^[ \t]*#\+(name|caption|attr_\w+|header|plot|results|label|orphan|toc|include)\b",
    re.IGNORECASE,
)


def _fix_affiliated(text: str) -> tuple[str, list[str]]:
    """删除 affiliated keyword 与下方 #+begin_* block 之间的空行。

    #+name: / #+caption: / #+attr_html: 等 affiliated keywords 必须紧贴 block，
    二者间的空行会导致 Org 不识别 affiliation。

    上方空行保留（与上文段落分隔）。
    """
    changes: list[str] = []
    lines = text.split("\n")
    result = []
    i = 0

    while i < len(lines):
        result.append(lines[i])
        # 若当前行是 affiliated keyword，跳过其后连续空行直到非空行
        if _AFFILIATED.match(lines[i]):
            j = i + 1
            removed = 0
            while j < len(lines) and lines[j].strip() == "":
                removed += 1
                j += 1
            if removed > 0:
                changes.append(
                    f"  行 {i + 2}: affiliated keyword 与 block 间空行（删除 {removed} 行）"
                )
            i = j
        else:
            i += 1

    return "\n".join(result), changes


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 7：中文技术文档规范（Markdown 与 Org 共用）
# ═══════════════════════════════════════════════════════════════════════════════
#
# 规范来源：zh-tech-doc-style-guide skill（yikeke/zh-style-guide）。
# 选型原则：只收「机械可判定 + 自动修复不改变语义」的项；语义级规则
# （的/地/得、顿号误用、繁体混用、缩略语首现）需要理解上下文，一律只报不改。
#
# 已实现（自动修复）：
#   R1 全角空格 → 半角空格
#   R2 全角标点两侧多余半角空格
#   R3 中英文之间补半角空格
#   R4 破折号 —— 两侧空格
#   R5 英文省略号 … → 中文 ……
#   R6 连续感叹号 ！！→ ！
#   R7 序数词 3、 → 3.
#   R8 数值与单位之间补半角空格（7kg → 7 kg，角度/摄氏/百分号除外）
#
# 只报告（lint，不自动修复）：
#   L1 的/地/得 疑似误用（三字都出现在同一行才报，避免噪音）
#   L2 顿号误用：并列分句之间用顿号（，.*、.*，模式）
#   L3 繁体字（少量高频字，非穷举）
#   L4 不规范缩略语（16c32g / 10w 形态）
#
# 保护：代码块 / 表格 / drawer / 属性行内的内容一律不动。

# 全角空格 U+3000
_FULLWIDTH_SPACE = "\u3000"

# 中文标点（全角形式）
_ZH_PUNCT = "，。！？；：、（）【】《》〈〉「」『』…—"
# 其中「可以紧邻半角空格」的行尾类：这些标点后的空格本该删
# 另有一类「前不该有空格」的标点，用同一集合处理两侧。

# 需要在中英文之间补空格的「中文侧」字符：只含汉字。
# 顿号不算边界（「3、5、7」并列数字两侧不补空格）；
# 中文标点不算「中文单词」，其与英文之间是否空格由 R2 单独处理
# （上游规范是全角标点两旁不空格，故标点后绝不补）。
# 不含 U+3000 全角空格本身（它已被 R1 转成半角）。
_CJK_HAN = "\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff"
_LATIN_CHAR = re.compile(r"[A-Za-z0-9]")

# 单位符号集合：数值后接这些字母时应补空格
_UNIT_TOKENS = (
    "km",
    "cm",
    "mm",
    "nm",
    "kg",
    "mg",
    "ms",
    "MB",
    "GB",
    "TB",
    "KB",
    "Mbps",
    "Gbps",
    "h",
    "min",
    "s",
    "W",
    "kW",
    "V",
    "A",
    "Hz",
    "kHz",
    "MHz",
    "GHz",
    "rpm",
    "fps",
    "dpi",
)

# R8 例外的单位：数值与单位之间不空格
_UNIT_NO_SPACE = ("°", "℃", "%")

# 繁体专用词（繁简确定不同）。
# 不用单字判定：「行」「化」「文」「表」等字两岸通用，凭单字判繁体必然误伤。
# 表中每个词都核对过繁简写法确实不同——「伺服器」「滑鼠」「警告」「修改」
# 等两岸同形的词一律不入表，否则纯误报。
# ponytail: 词组级检测会漏掉单字混入的繁体（如单独的「檔」），
# 漏网由人工审校兜底；要更严可换 OpenCC，代价是引入依赖。
_TRAD_WORDS = (
    "這個", "那個", "認識", "點擊",
    "開啟", "證據", "檔案", "網路", "儲存",
    "動態", "狀態", "視覺", "設計", "開發", "測試",
    "環境", "組態", "參數", "資訊", "瀏覽",
    "載入", "安裝", "卸載", "執行緒", "客戶端", "資料庫", "備份",
    "還原", "遷移", "監視", "報表",
    "導覽", "搜尋", "鍵盤", "螢幕", "視窗", "專輯", "專業", "筆記本",
    "電腦", "網際網路", "服務", "架構", "模組", "欄位", "選項", "單選",
    "複選", "方塊", "開關", "按鈕", "狀態列",
    "訊息", "對話方塊", "錯誤", "失敗",
    "輸入", "輸出", "刪除", "查詢", "篩選", "群組",
    "標籤", "分類", "頁尾", "聯絡", "說明文件", "教學", "課程",
    "線上", "離線", "並行",
)

# L4 不规范缩略语：数字+字母混搭且长度可疑
_ABBREV_BAD = re.compile(r"\b\d+[a-z]\d*[a-z]\b|\b\d{1,3}w\b")

# org 元数据行：DEADLINE/SCHEDULED/CLOCKED/CLOCK 的 timestamp（含 +1w 等
# repeat cookie）与 #+KEYWORD directive。这些不是正文，不套中文规范。
_ORG_DIRECTIVE = re.compile(
    r"^\s*(DEADLINE|SCHEDULED|CLOSED|CLOCK|CLOG)\s*:|^\s*#\+\w+:", re.IGNORECASE
)

# L2 顿号误用：并列分句之间用顿号（「，…、…，」形态，粗略启发）
_DUNHAO_MISUSE = re.compile(r"[，,][^，,。；！？]{2,40}、[^，,。；！？]{2,40}[，,]")


def _zh_style(text: str) -> tuple[str, list[str]]:
    """中文技术文档规范：逐行应用自动修复规则 + 收集 lint 报告。

    只处理行内容，不增删行；因此必须排在 _align_tables 之前
    （表格对齐依赖单元格内容宽度）与 _normalize_blank_lines 无关。
    返回 (新文本, 变更说明列表)。
    """
    changes: list[str] = []
    result: list[str] = []

    for line, idx, in_block in _iter_lines_with_block_state(text):
        if in_block:
            result.append(line)
            continue
        stripped = line.strip()
        # 表格行不动（列对齐阶段会重排，先改内容会与其冲突）
        if _TABLE_ROW.match(line):
            result.append(line)
            continue
        # drawer 内属性行不动（:KEY: value / :KEY: / :PROPERTIES: / :END:）
        # ——:EFFORT: 8h 这类属性值是元数据，不是正文的单位用法
        if _DRAWER_BEGIN.match(line) or _DRAWER_END.match(line):
            result.append(line)
            continue
        if _PROP_LINE.match(line):
            result.append(line)
            continue
        # org directive / timestamp 行不动：
        # DEADLINE/SCHEDULED/CLOCK 的 +1w repeat cookie、#+KEYWORD 等
        if _ORG_DIRECTIVE.match(line):
            result.append(line)
            continue
        # 行内公式不动（$$...$$ / $...$）：4ac 是代数式，不是缩略语
        if "$$" in line or stripped.startswith("\\["):
            result.append(line)
            continue
        # Org 标题行：只做 lint，不改（标题里动标点风险高）
        if _HEADING.match(line):
            lint = _zh_lint(line, idx)
            if lint:
                changes += lint
            result.append(line)
            continue

        new_line = line
        # R7 必须在 R2 之前：R2 会删掉「3、 行政」中顿号后的空格，
        # 若先跑 R2，R7 只能得到「3.行政」而非「3. 行政」。
        new_line, c7 = _fix_ordinal_dun(new_line, idx)
        new_line, c1 = _fix_fullwidth_space(new_line, idx)
        new_line, c2 = _fix_punct_spacing(new_line, idx)
        new_line, c3 = _fix_zh_latin_spacing(new_line, idx)
        new_line, c4 = _fix_dash_spacing(new_line, idx)
        new_line, c5 = _fix_ellipsis(new_line, idx)
        new_line, c6 = _fix_repeated_bang(new_line, idx)
        new_line, c8 = _fix_unit_spacing(new_line, idx)
        changes += c7 + c1 + c2 + c3 + c4 + c5 + c6 + c8
        changes += _zh_lint(new_line, idx)
        result.append(new_line)

    return "\n".join(result), changes


# ── R1: 全角空格 → 半角空格 ────────────────────────────────────────────────


def _fix_fullwidth_space(line: str, idx: int) -> tuple[str, list[str]]:
    if _FULLWIDTH_SPACE not in line:
        return line, []
    new = line.replace(_FULLWIDTH_SPACE, " ")
    return new, [f"  行 {idx + 1}: 全角空格 → 半角空格"]


# ── R2: 全角标点两侧多余半角空格 ───────────────────────────────────────────


def _fix_punct_spacing(line: str, idx: int) -> tuple[str, list[str]]:
    """删除中文全角标点紧邻的半角空格（标点前、标点后各一处）。

    上游规范：「中文全角标点符号两旁禁止空半角空格」。
    """
    changes: list[str] = []
    new = line
    # 标点前空格：「 。」→「。」
    new2 = re.sub(r"[ \t]+([" + re.escape(_ZH_PUNCT) + r"])", r"\1", new)
    # 标点后空格：「， 」→「，」
    new2 = re.sub(r"([" + re.escape(_ZH_PUNCT) + r"])[ \t]+", r"\1", new2)
    if new2 != new:
        changes.append(f"  行 {idx + 1}: 全角标点旁多余空格")
        new = new2
    return new, changes


# ── R3: 中英文之间补半角空格 ───────────────────────────────────────────────


def _fix_zh_latin_spacing(line: str, idx: int) -> tuple[str, list[str]]:
    """中文与英文/数字之间补一个半角空格。

    上游规范：「英文字符和阿拉伯数字一般使用半角格式，所以应使用半角空格
    包围」，例外为位于句首（左边省略）与右侧为标点（右边省略）——两者本就
    没有空格可补，正则天然不匹配。

    顿号不作为边界：「3、5、7」是并列数字，顿号两侧不补空格。
    """
    changes: list[str] = []
    new = line
    # 汉字 后接 英文/数字：字 → A
    new2 = re.sub(f"([{_CJK_HAN}])(?=[A-Za-z0-9])", r"\1 ", new)
    # 英文/数字 后接 汉字：A → 字
    new2 = re.sub(f"(?<=[A-Za-z0-9])([{_CJK_HAN}])", r" \1", new2)
    if new2 != new:
        changes.append(f"  行 {idx + 1}: 中英文之间补空格")
        new = new2
    return new, changes


# ── R4: 破折号 —— 两侧空格 ─────────────────────────────────────────────────


def _fix_dash_spacing(line: str, idx: int) -> tuple[str, list[str]]:
    """破折号前后不空格（上游：破折号前后不空格）。"""
    if "——" not in line and " — " not in line:
        return line, []
    new = re.sub(r"[ \t]*——[ \t]*", "——", line)
    if new == line:
        return line, []
    return new, [f"  行 {idx + 1}: 破折号两侧空格"]


# ── R5: 英文省略号 … → 中文 …… ────────────────────────────────────────────


def _fix_ellipsis(line: str, idx: int) -> tuple[str, list[str]]:
    """英文省略号转为中文省略号（六个点）。

    上游规范：「中文语境中禁止使用英文省略号，即三个小圆点」。
    同时处理 ASCII 三点「...」与 U+2026 单字符「…」。
    """
    if "..." not in line and "…" not in line:
        return line, []
    new = re.sub(r"(?:\.{3,}|…+)", "……", line)
    if new == line:
        return line, []
    return new, [f"  行 {idx + 1}: 英文省略号 → 中文省略号"]


# ── R6: 连续感叹号 → 单个 ──────────────────────────────────────────────────


def _fix_repeated_bang(line: str, idx: int) -> tuple[str, list[str]]:
    """禁止多个感叹号连用（上游：禁止多个感叹号连用）。"""
    if not re.search(r"！{2,}", line):
        return line, []
    new = re.sub(r"！{2,}", "！", line)
    return new, [f"  行 {idx + 1}: 连续感叹号 → 单个"]


# ── R7: 序数词 3、 → 3. ────────────────────────────────────────────────────


def _fix_ordinal_dun(line: str, idx: int) -> tuple[str, list[str]]:
    """阿拉伯数字作序数词后用点号不用顿号。

    上游规范：「阿拉伯数字作为序数词，如『1』后用点号而不用顿号」。
    只匹配「数字 + 、 + 非数字」形态：顿号后紧跟另一个数字的是并列
    （如「3、5、7」），不动；后接文字才是序数词（如「3、行政文明建设」）。
    """
    if not re.search(r"\d、(?![0-9、])", line):
        return line, []
    new = re.sub(r"(\d)、", r"\1.", line)
    return new, [f"  行 {idx + 1}: 序数词「数字、」→「数字.」"]


# ── R8: 数值与单位之间补空格 ───────────────────────────────────────────────


def _fix_unit_spacing(line: str, idx: int) -> tuple[str, list[str]]:
    """数值与单位符号之间补一个半角空格（7kg → 7 kg）。

    上游规范：「大多数情况下，数值与单位符号之间需要空一个半角空格」，
    例外为角度、摄氏度、百分号（60°、37°C、100%）与英尺英寸符号。
    """
    changes: list[str] = []
    new = line
    # 为每个单位 token 匹配「数字紧接单位」的形态
    for unit in sorted(_UNIT_TOKENS, key=len, reverse=True):
        pat = re.compile(rf"(?<=\d)({re.escape(unit)})\b")
        if pat.search(new):
            new = pat.sub(r" \1", new)
            changes.append(f"  行 {idx + 1}: 数值与单位 {unit} 之间补空格")
    # 摄氏度：37C → 37 °C（数字后紧跟 C 且非其他单位的一部分）
    new2 = re.sub(r"(?<=\d)C(?=[\s，。！？；：、）】」』]|$)", " °C", new)
    if new2 != new:
        new = new2
        changes.append(f"  行 {idx + 1}: 摄氏度补空格与度符号")
    return new, changes


# ── L1-L4: 只报告不修复的语义级 lint ───────────────────────────────────────


def _zh_lint(line: str, idx: int) -> list[str]:
    """语义级中文规范检查：只报告，不自动修复。"""
    out: list[str] = []

    # L1 的/地/得：同一行出现多个结构助词时提示人工核对
    hits = [ch for ch in "的地得" if ch in line]
    if len(hits) >= 2:
        out.append(f"  行 {idx + 1}: [待人工] 含 {'/'.join(hits)}，核对的地得用法")

    # L2 顿号误用：并列分句之间用顿号
    if _DUNHAO_MISUSE.search(line):
        out.append(f"  行 {idx + 1}: [待人工] 顿号可能误用于分句之间")

    # L3 繁体词（词组级，单字两岸通用必然误伤）
    hits = [w for w in _TRAD_WORDS if w in line]
    if hits:
        out.append(f"  行 {idx + 1}: [待人工] 疑似繁体用语：{'、'.join(hits[:3])}")

    # L4 不规范缩略语
    m = _ABBREV_BAD.search(line)
    if m:
        out.append(f"  行 {idx + 1}: [待人工] 不规范缩略语：{m.group()}")

    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 8：表格列对齐
# ═══════════════════════════════════════════════════════════════════════════════

# 表格行：以 | 开头（允许前导空白）
_TABLE_ROW = re.compile(r"^[ \t]*\|.*\|[ \t]*$")
# 表格分隔行：|---|---| 或 |---+---|
_TABLE_SEP = re.compile(r"^[ \t]*\|[-+|]+\|[ \t]*$")


def _align_tables(text: str) -> tuple[str, list[str]]:
    """对齐 org 表格列宽（支持 CJK 全角字符）。

    连续的表格行块统一对齐：每列宽度取该列最大显示宽度 + 1 空格 padding。
    分隔行（|---+---|）重新生成以匹配新列宽。
    """
    changes: list[str] = []
    lines = text.split("\n")
    result = []
    i = 0

    while i < len(lines):
        if _TABLE_ROW.match(lines[i]) and not _TABLE_SEP.match(lines[i]):
            # 收集连续表格行块
            block_start = i
            block = []
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                block.append(lines[i])
                i += 1
            aligned, n = _align_one_table(block, block_start)
            changes += n
            result.extend(aligned)
        else:
            result.append(lines[i])
            i += 1

    return "\n".join(result), changes


def _parse_table_row(line: str) -> list[str]:
    """解析表格行为单元格列表（去除外层 | 与首尾空白）。"""
    s = line.strip()
    # 去掉首尾 |
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [cell.strip() for cell in s.split("|")]


def _align_one_table(block: list[str], base_idx: int) -> tuple[list[str], list[str]]:
    """对齐单个表格块。返回 (新行列表, 变更说明)。"""
    changes: list[str] = []
    # 解析每行
    rows = []  # [(原始行, 单元格列表, 是否分隔行)]
    for ln in block:
        if _TABLE_SEP.match(ln):
            rows.append((ln, None, True))
        else:
            rows.append((ln, _parse_table_row(ln), False))

    # 计算每列最大显示宽度
    n_cols = max(
        (len(cells) for _, cells, is_sep in rows if cells is not None), default=0
    )
    if n_cols == 0:
        return block, []

    col_widths = [0] * n_cols
    for _, cells, is_sep in rows:
        if cells is None:
            continue
        for c in range(min(len(cells), n_cols)):
            w = _disp_width(cells[c])
            if w > col_widths[c]:
                col_widths[c] = w

    # 生成对齐后的行
    result = []
    for idx, (orig, cells, is_sep) in enumerate(rows):
        if is_sep:
            # 分隔行：|-------+-------|
            sep_cells = ["-" * (w + 2) for w in col_widths]
            new = "|" + "+".join(sep_cells) + "|"
        else:
            # 数据行：左对齐，每格 " content " + 补空格
            parts = []
            for c in range(n_cols):
                val = cells[c] if c < len(cells) else ""
                pad = col_widths[c] - _disp_width(val)
                parts.append(" " + val + " " * (pad + 1))
            new = "|" + "|".join(parts) + "|"
        if new != orig:
            changes.append(f"  行 {base_idx + idx + 1}: 表格列对齐")
        result.append(new)

    return result, changes


# ═══════════════════════════════════════════════════════════════════════════════
# 阶段 8：空行规范化（最后跑）
# ═══════════════════════════════════════════════════════════════════════════════

_HEADING = re.compile(r"^\*+\s")
_DRAWER_BEGIN = re.compile(r"^\s*:PROPERTIES:\s*$")
_DRAWER_END = re.compile(r"^\s*:END:\s*$")
# 任何 #+ 开头的行（affiliated keyword / block begin / block end / 其他 directive）
_HASH_DIRECTIVE = re.compile(r"^\s*#\+")
# 列表项：+ / - / 数字+. / * 开头（缩进可有可无）
_LIST_ITEM = re.compile(r"^[ \t]*([+\-]|[0-9]+\.|\*)\s")
# block 内行：#+begin_* 与 #+end_* 之间的内容（含空行也不算"块外"）


def _classify_line(line: str) -> str:
    """返回行的语义类型。

    返回值集合：
      blank           — 空行
      heading         — * / ** 标题
      block_begin     — #+begin_xxx
      block_end       — #+end_xxx
      md_fence        — ``` markdown 代码围栏（边界，与 block_begin/end 同类紧贴）
      drawer_begin    — :PROPERTIES: 等
      drawer_end      — :END:
      table           — 表格行
      hash_directive  — #+ 开头的行（affiliated / begin / end / 其他 directive）
      comment         — # 开头的注释行（含 SPDX / Emacs modeline / 普通 # 注释）
      list_item       — 列表项（+ - * 1. 开头）
      list_continuation — 缩进的非空行（属于列表项的内容延续）
      prop_line       — drawer 内 :KEY: value 属性行
      normal          — 普通段落行（无缩进）
    """
    if line.strip() == "":
        return "blank"
    if _HEADING.match(line):
        return "heading"
    if _BLOCK_BEGIN.match(line):
        return "block_begin"
    if _BLOCK_END.match(line):
        return "block_end"
    if _MD_FENCE.match(line):
        return "md_fence"
    if _DRAWER_BEGIN.match(line):
        return "drawer_begin"
    if _DRAWER_END.match(line):
        return "drawer_end"
    if _TABLE_ROW.match(line):
        return "table"
    if _HASH_DIRECTIVE.match(line):
        return "hash_directive"
    if line.lstrip().startswith("#"):
        # 普通 # 注释行（含 SPDX / Emacs modeline / 文件头注释等）——
        # 视为与上一行注释连续的整体，紧贴处理
        return "comment"
    if _PROP_LINE.match(line):
        return "prop_line"
    if _LIST_ITEM.match(line):
        return "list_item"
    # 缩进的非空行（非列表符号）= 列表/段落延续
    if line.startswith((" ", "\t")):
        return "list_continuation"
    return "normal"


def _advance_blank_state(
    kind: str, in_block: bool, in_drawer: bool, in_list: bool, in_table: bool
) -> tuple[bool, bool, bool, bool]:
    """根据当前行类型推进空行状态机的四个状态位。

    抽成纯函数以便首行也走同一路径（首行无 need_blank 评估，
    但必须进状态机，否则以 :PROPERTIES: / #+begin_src 开头的文件
    状态永不置位）。
    """
    if kind == "block_begin":
        return True, False, False, False
    if kind == "block_end":
        return False, False, False, False
    if kind == "drawer_begin":
        return in_block, True, False, False
    if kind == "drawer_end":
        return in_block, False, False, False
    if kind == "list_item":
        return in_block, in_drawer, True, False
    if kind == "list_continuation":
        # 延续段落：不重置 in_list（仍属于上一个列表项的内容）
        return in_block, in_drawer, in_list, in_table
    if kind == "table":
        return in_block, in_drawer, False, True
    # 普通段落 / heading / hash_directive / comment 等：重置 list/table run 状态
    # comment 不打断 list run——但 SPDX header 紧贴规则由规则 8 处理
    return in_block, in_drawer, False, False


def _normalize_blank_lines(text: str) -> tuple[str, list[str]]:
    """规范化空行。

    核心原则（用户明确）：
      1. 标题（`*`/`**`/...）后总是插空行（与其他元素分隔）
      2. 任何 #+ 开头的行（affiliated / begin / end / 其他 directive）相互紧贴，
         它们与「非 #+ 元素」（标题/段落/列表/表格/drawer）之间应空 1 行
      3. drawer 内部（:PROPERTIES: ... :END: 及其属性行）不空行
      4. block 内部（#+begin_* 与 #+end_* 之间）不空行
      5. 列表 run（连续 list_item + 缩进延续行）内部不空行；
         列表 run 与表格/drawer/heading/非缩进 normal 之间空行
      6. 表格连续行内部不空行；表格与表格外其他元素之间空行
      7. 段落（normal，无缩进）之间、段落与标题/列表/表格/block 之间空 1 行
      8. 连续空行折叠为 1；文件末尾恰好 1 个换行符

    实现：用「状态」跟踪上下文（block / drawer / list / table 内部），
    内部行一律紧贴，状态结束后才重新评估空行。
    """
    changes: list[str] = []
    lines = text.split("\n")

    # 步骤 1：折叠连续空行为 1 个空行（结构性预处理）
    folded = []
    prev_blank = False
    for ln in lines:
        is_blank = ln.strip() == ""
        if is_blank and prev_blank:
            continue
        folded.append(ln)
        prev_blank = is_blank

    # 步骤 1.5：识别并规范化 SPDX header（折叠为紧凑形式）
    # SPDX header 特征：文件开头，由 # 注释行组成，包含 SPDX-FileCopyrightText /
    # SPDX-License-Identifier / 单独的 # 等关键字。视为一个整体：紧凑化（移除中间空行），
    # header 结束后与正文之间留 1 空行。
    folded, spdx_changed = _compact_spdx_header(folded)
    if spdx_changed:
        changes.append("  SPDX header 紧凑化")

    # 步骤 2：基于上下文状态机决定每行前的空行需求

    out: list[str] = []
    # 上下文状态
    in_block = False  # 在 #+begin_* 与 #+end_* 之间
    in_fence = False  # 在 ``` markdown 围栏之间（非 strict 模式下不转换，仍须保护）
    in_drawer = False  # 在 :PROPERTIES: 与 :END: 之间（属性行）
    in_list = False  # 上一非空行也是 list_item（连续列表项）
    in_table = False  # 上一非空行也是 table（连续表格行）

    for i, ln in enumerate(folded):
        kind = _classify_line(ln)

        # blank 行：在 block/fence/drawer 内部保留空行；
        # list run / table run 内**不**保留空行（run 紧贴）；
        # run 外（normal 段落/标题/其他元素之间）丢弃（下方结构化补空行）
        if kind == "blank":
            if in_block or in_drawer or in_fence:
                out.append(ln)
            # 否则丢弃（结构化上下文外不保留空行；list/table run 不允许内空行）
            continue

        # 文件首行：永不前置空行
        need_blank = False
        if out:  # out 非空意味着不是首行
            last = out[-1]
            last_kind = _classify_line(last)

            # ── 空行规则 ──
            # 规则 1: block 内部（block_begin/end 与其中内容）不空行
            if in_block or in_fence:
                need_blank = False
            # 规则 2: drawer 内部（属性行与 :END:）不空行
            elif in_drawer:
                need_blank = False
            # 规则 3: 列表 run 内部（连续列表项 / 缩进延续行）不空行
            elif in_list and kind in ("list_item", "list_continuation"):
                need_blank = False
            # 规则 4: 表格 run 内部（连续表格行）不空行
            elif in_table and kind == "table":
                need_blank = False
            # 规则 5: #+ 系列内部紧贴（hash_directive 与 block_begin/end 视为同类）
            elif kind in ("hash_directive", "block_begin") and last_kind in (
                "hash_directive",
                "block_begin",
            ):
                need_blank = False
            # 规则 6: :END: 与 fingerprint 紧贴（约定）
            elif _FINGERPRINT.match(ln) and last_kind == "drawer_end":
                need_blank = False
            # 规则 7: 标题紧接 :PROPERTIES: drawer（Org 标准约定，drawer 是 metadata）
            elif kind == "drawer_begin" and last_kind == "heading":
                need_blank = False
            # 规则 8: 注释块内部紧贴（SPDX header / 文件头注释 / Emacs modeline 等
            #         # 开头的连续注释行视为整体）。这对 SPDX header 紧凑化至关重要。
            elif kind == "comment" and last_kind == "comment":
                need_blank = False
            # 规则 9: 其他所有组合：插空行（实现"标题后空行、段落间空行、#+与其它元素空行"等）
            else:
                need_blank = True

        # 状态更新必须在 if out: 之外：文件首行也要进入状态机，
        # 否则 :PROPERTIES: 位于首行时 in_drawer 永不置位，
        # 后续属性行与 :END: 之间会被插入空行。
        in_block, in_drawer, in_list, in_table = _advance_blank_state(
            kind, in_block, in_drawer, in_list, in_table
        )
        if kind == "md_fence":
            in_fence = not in_fence

        if need_blank:
            out.append("")
        out.append(ln)

    # 步骤 3：去尾随空行 + 确保恰好 1 个换行
    while out and out[-1].strip() == "":
        out.pop()
    out.append("")  # 末尾换行（join 后产生 \n）

    new_text = "\n".join(out)
    if new_text != text:
        changes.append("  空行规范化")

    return new_text, changes

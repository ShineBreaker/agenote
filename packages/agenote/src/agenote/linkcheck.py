# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
#
"""agenote.linkcheck — ``[[路径]]`` 链接完整性只读检查。

纯函数模块：无落盘、无锁、无配置键（约定硬编码于此 + ADR-0005，
复用既有 ``KB_ROOT``/记忆根解析，不进 SCHEMA）。

约定摘要（完整版见 ``docs/adr/0005-link-convention.md``）：
- ``[[路径]]`` 相对记忆根（人类域 ``KB_ROOT/memories/``，agent 域对应子目录）。
- ``.md`` 可省扩展名（:func:`resolve_link` 补全）。
- Org 卡片的 ``[[file:]]`` / ``[[http(s):]]`` / ``[[id:]]`` 不在此列，直接排除。
- 大小写敏感（POSIX 原样比对）；首尾空格忽略，内部空格保留。
"""

from __future__ import annotations

import re
from pathlib import Path

# ``[[...]]`` 内不允许嵌套括号——Org 双段式 ``[[file:x][描述]]`` 天然匹配不上，
# 单段式 ``[[file:x]]`` / ``[[https://..]]`` 靠下面的 scheme 前缀过滤。
_LINK_RE = re.compile(r"\[\[([^\[\]]+?)\]\]")

# 非单源引用：Org 外链与卡片 ID 引用，不属于 [[路径]] 约定。
_EXTERNAL_PREFIXES = ("file:", "http:", "https:", "id:", "ftp:", "mailto:")


def extract_links(text: str) -> list[str]:
    """从文本抽取 ``[[路径]]`` 引用（去重、保序）。

    - 排除 ``[[file:]]`` / ``[[http(s):]]`` / ``[[id:]]`` 等外部前缀。
    - ``[[目标|别名]]`` 取 ``|`` 前部分，``#锚点`` 忽略（见 ADR-0005）。
    - 首尾空格忽略；空目标丢弃；非 str 输入返回空表。
    """
    if not isinstance(text, str) or not text:
        return []
    links: list[str] = []
    seen: set[str] = set()
    for m in _LINK_RE.finditer(text):
        raw = m.group(1).split("][", 1)[0]  # 防御：Org 双段残留
        target = raw.split("|", 1)[0].split("#", 1)[0].strip()
        if not target:
            continue
        if target.lower().startswith(_EXTERNAL_PREFIXES):
            continue
        if target not in seen:
            seen.add(target)
            links.append(target)
    return links


def resolve_link(root: str | Path, path: str) -> Path:
    """把 ``[[路径]]`` 解析为记忆根下的绝对路径（只读，不触盘写）。

    - 相对记忆根拼接；开头的 ``/`` 按相对处理（不允许跳出记忆根的绝对写法）。
    - 无扩展名时：同名真实文件存在则原样返回，否则补 ``.md``。
    """
    base = Path(root)
    target = str(path or "").split("|", 1)[0].split("#", 1)[0].strip().lstrip("/")
    cand = base / target
    if cand.suffix:
        return cand
    try:
        if cand.is_file() and not cand.is_symlink():
            return cand
    except OSError:
        pass
    return cand.with_suffix(".md")


def check_links(
    root: str | Path, exempt: tuple[str, ...] = ("index.md",)
) -> dict[str, list]:
    """扫描记忆根下全部 ``*.md`` 条目，返回坏链与孤儿（只读）。

    - ``broken``：``{"source", "link", "expected"}`` 三元组，按 (source, link) 排序；
      目标不存在、不可读或为 symlink 即判坏链。
    - ``orphaned``：存在但无任何 ``[[ ]]`` 指向的条目相对路径（排序）；
      入口索引文件本身豁免，豁免集由 ``exempt`` 控制（匹配文件名或相对路径）。
    - 根不存在 / 文件读失败 → 按空处理（零候选即成功，不抛错）。
    """
    base = Path(root)
    exempt_names = set(exempt or ())
    try:
        files = sorted(
            p
            for p in base.rglob("*.md")
            if p.is_file() and not p.is_symlink()
        )
    except OSError:
        files = []

    def _rel(p: Path) -> str:
        try:
            return p.relative_to(base).as_posix()
        except ValueError:
            return p.as_posix()

    def _exempted(p: Path) -> bool:
        return p.name in exempt_names or _rel(p) in exempt_names

    referenced: set[str] = set()
    broken: list[dict[str, str]] = []
    for src in files:
        try:
            text = src.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for link in extract_links(text):
            resolved = resolve_link(base, link)
            try:
                ok = resolved.is_file() and not resolved.is_symlink()
            except OSError:
                ok = False
            if ok:
                referenced.add(_rel(resolved))
            else:
                broken.append(
                    {"source": _rel(src), "link": link, "expected": _rel(resolved)}
                )

    orphaned = sorted(_rel(p) for p in files if not _exempted(p) and _rel(p) not in referenced)
    broken.sort(key=lambda b: (b["source"], b["link"]))
    return {"broken": broken, "orphaned": orphaned}

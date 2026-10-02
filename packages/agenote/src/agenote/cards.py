# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from agenote.core import (
    KB_ROOT,
    KB_EXPERIENCES,
    KB_MEMORY,
    KB_INBOX,
    SEED_TYPES,
    VALID_OWNERS,
    VALID_ENTRY_TYPES,
    VALID_STATUSES,
    STALE_DAYS,
    ARCHIVE_THRESHOLD_DAYS,
    TYPE_PROMOTE_MIN,
    CARD_TEMPLATES,
    ENTRY_BODY_DEFAULTS,
    DEFAULT_CATEGORY,
    DEFAULT_LIST_COUNT,
    DEFAULT_OWNER,
    DEFAULT_TYPE,
    build_fingerprint_line,
    die,
    now,
    today,
    timestamp_id,
    _build_template,
    ensure_dirs,
    gate_secret_write,
    touch_card,
    safe_error_message,
    validate_category,
    warn_secret_write,
    _resolve_card,
    default_context,
    agenote_context,
)
from agenote.orgserde import (
    has_top_property_drawer,
    parse_org_prop,
    read_org_title,
    set_org_prop,
)
from agenote.index import (
    _card_dict,
    _load_index,
    _save_index,
    _upsert_card,
    known_agents,
    type_counts,
)
from agenote.safeio import atomic_write, restore_text_files


def _gate_type(type_: str, ctx, force: bool):
    """type 门禁：正式 type（种子 ∪ 非归档数 ≥ 晋升阈值）免检，其余 die。

    返回已加载的 index 供调用方后续 upsert 复用。--force 强制写入观察期
    或全新 type，但该 type 在达到晋升阈值前始终不走免检。
    """
    # 读改写路径必须先严格验证 LKG，不能把损坏索引静默降成空索引。
    index = _load_index(ctx)
    counts = type_counts(ctx)
    n = counts.get(type_, 0)
    if type_ in SEED_TYPES or n >= TYPE_PROMOTE_MIN:
        return index

    formal = ", ".join(
        f"{t}({c})" for t, c in counts.most_common() if c >= TYPE_PROMOTE_MIN
    )
    if not force:
        if n:
            reason = (
                f"type '{type_}' 仅 {n} 张非归档卡片，未达晋升标准"
                f"（需 ≥{TYPE_PROMOTE_MIN} 张），处于聚拢观察期"
            )
        else:
            reason = f"type '{type_}' 是新类型（知识库中无此类型卡片）"
        die(
            f"{reason}\n"
            f"      正式 type: {formal or '(无）'}\n"
            f"      种子 type: {', '.join(sorted(SEED_TYPES))}\n"
            f"      请复用已有 type（agenote fields --type 查看全量）；"
            f"确需延续/新建请加 --force"
        )
    print(
        f"警告: type '{type_}' 不在正式 type 中"
        f"（{n} 张 < {TYPE_PROMOTE_MIN}），已按 --force 强制写入",
        file=sys.stderr,
    )
    return index


def cmd_add(args: argparse.Namespace, ctx=None) -> None:
    """
    创建一张新的经验卡片并写入 experiences/ 目录。

    参数解析后根据 entry_type 自动推断 type 和 owner，
    生成符合 Org mode 格式的卡片文件。
    """
    ctx = ctx or default_context()
    title = args.title or ""
    category = args.category or DEFAULT_CATEGORY
    tech = args.tech or ""
    type_ = args.type or DEFAULT_TYPE
    owner = args.owner or DEFAULT_OWNER
    entry_type = args.entry or ""
    summary = args.summary or ""
    use_stdin = args.stdin

    if not title:
        die("必须指定 --title")

    validate_category(category)

    if entry_type and entry_type not in VALID_ENTRY_TYPES | {""}:
        die(f"--entry 仅支持: {', '.join(sorted(VALID_ENTRY_TYPES))}")

    # ── owner 白名单：非标准值只警告不阻塞（便于扩展）─────────────────────
    if owner not in VALID_OWNERS:
        print(
            f"警告: owner '{owner}' 不在标准值中 ({', '.join(sorted(VALID_OWNERS))})",
            file=sys.stderr,
        )

    # source_agent：从 ctx.agent_name 取（agenote 域有值、人类域为空串）。
    # 已知 agent = 种子 ∪ index 出现过（index.known_agents 实时计算）：新 agent
    # 的首张卡会警告提示，第二张起自动收录，无需同步改代码。
    source_agent = getattr(ctx, "agent_name", "") or ""
    if source_agent and source_agent not in known_agents(ctx):
        print(
            f"警告: source_agent '{source_agent}' 不在已知 agent 列表中"
            f"（首次出现，写入后自动收录；已知: "
            f"{', '.join(sorted(known_agents(ctx)))}）",
            file=sys.stderr,
        )

    # entry_type 自动推断（仅在用户未显式指定时）
    if entry_type and not args.type:
        if entry_type in ("mistake", "ascended"):
            type_ = "debug"
        elif entry_type == "note":
            type_ = "workflow"

    # ── type 门禁：正式 type（种子 ∪ 非归档 ≥阈值）免检，其余需 --force ──
    index = _gate_type(type_, ctx, getattr(args, "force", False))

    if entry_type and not args.owner:
        owner = "collab"

    if not tech:
        tech = category

    id_ = timestamp_id()
    # 同秒并发 add 防撞：ID 追加序号保证唯一（index 按 ID upsert，撞车会互相覆盖；
    # kb 锁内执行，检查-生成无竞态窗口）
    base_id = id_
    n = 2
    while any(ctx.experiences.rglob(f"{id_}-*.org")):
        id_ = f"{base_id}-{n}"
        n += 1
    ts = now()
    filename = f"{id_}-{type_}-{category}.org"

    # 构建标签行（fingerprint 单一真相源：core.build_fingerprint_line）
    tags_line = build_fingerprint_line(category, type_, owner, tech, entry_type)

    filepath = ctx.experiences / category / filename
    filepath.parent.mkdir(parents=True, exist_ok=True)

    # 读取 stdin 内容
    body = ""
    if use_stdin:
        body = sys.stdin.read()

    # 写入侧 secret 门禁（与 import/export 同一清单）：卡片入 git 且可被
    # 检索/注入通道带出，密钥落入 SSOT 后每轮上下文都有外发风险。
    gate_secret_write(
        f"{title}\n{summary}\n{body}", "卡片写入",
        allow=getattr(args, "allow_secret", False),
    )

    # ── 组装 Org 内容 ──────────────────────────────────────────────────────
    lines = []
    lines.append(f"* DONE {title}")
    lines.append(":PROPERTIES:")
    lines.append(f":ID:       {id_}")
    lines.append(f":CREATED:  [{ts}]")
    lines.append(f":CATEGORY: {category}")
    lines.append(f":TECH:     {tech}")
    lines.append(f":TYPE:     {type_}")
    if entry_type:
        lines.append(f":ENTRY_TYPE: {entry_type}")
    lines.append(":STATUS:   done")
    lines.append(f":LAST_USED:   [{ts}]")
    lines.append(f":LAST_VERIFIED: [{ts}]")
    lines.append(":EFFORT:")
    lines.append(f":OWNER:    {owner}")
    # source_agent：agent 域写入者溯源；人类域留空（不写该行，区分人/agent）
    if source_agent:
        lines.append(f":SOURCE_AGENT: {source_agent}")
    lines.append(":USAGE_COUNT: 0")
    lines.append(":END:")
    lines.append(tags_line)
    lines.append("")

    # 如果 stdin 已包含 ** 小节，直接作为完整正文写入
    if use_stdin and re.search(r"^\*\* ", body, re.MULTILINE):
        lines.append(body.rstrip("\n"))
    else:
        lines.append("** 任务描述")
        lines.append(summary or title)
        lines.append("")
        lines.extend(_build_template(entry_type, body))

    original_index = ctx.index.read_bytes() if ctx.index.exists() else None
    try:
        atomic_write(filepath, "\n".join(lines) + "\n")

        # 增量更新 JSON 索引（index 已在 type 门禁处加载）
        _upsert_card(index, filepath, ctx)
        _save_index(index, ctx)
    except BaseException as exc:
        failures = restore_text_files(
            {filepath: None, ctx.index: original_index}
        )
        if failures:
            raise RuntimeError(
                f"add 回滚失败（{', '.join(failures)}）"
            ) from exc
        raise

    print(filepath)


def cmd_get(args: argparse.Namespace, ctx=None) -> None:
    """读取指定卡片的完整内容。支持完整路径或 ID 部分匹配。

    安全限制：直接路径必须在 KB_ROOT 内，防止任意文件读取。
    """
    ctx = ctx or default_context()
    target = args.target
    if not target:
        die("用法: kb get <卡片文件名或ID>")

    p = Path(target)
    if p.is_absolute():
        try:
            p.resolve().relative_to(ctx.root.resolve())
        except ValueError:
            die(f"路径超出知识库范围: {target}")

    # --used：读取后显式留痕（递增 USAGE_COUNT）；默认纯读取不计数
    used = getattr(args, "used", False)
    card = _resolve_card(target, ctx)
    if not card:
        die(f"未找到卡片: {target}")
    if used:
        try:
            touch_card(card, "LAST_USED", ctx)
        except (OSError, UnicodeError, ValueError) as exc:
            die(f"记录使用痕迹失败: {safe_error_message(exc)}")
    print(card.read_text(encoding="utf-8"), end="")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: list — 列出卡片
# ═══════════════════════════════════════════════════════════════════════════════


def _days_since(date_prop: str | None) -> int:
    """Org 日期属性（[YYYY-MM-DD] …）距今天数；无法解析返回 -1（永不过滤）。"""
    if not date_prop:
        return -1
    try:
        d = datetime.strptime(re.sub(r"[\[\]]", "", date_prop).split()[0], "%Y-%m-%d")
        return (datetime.now() - d).days
    except (ValueError, IndexError):
        return -1


def cmd_list(args: argparse.Namespace, ctx=None) -> None:
    """列出经验卡片，支持过滤和数量限制。默认显示最近 DEFAULT_LIST_COUNT 条，输出 JSON。

    --unused-days N：只列「最后使用（缺省用创建日期）距今超 N 天」的卡片——
    策展时找 done/stable → stale 降级候选的只读入口。
    """
    ctx = ctx or default_context()
    recent = (
        args.recent
        if args.recent is not None
        else (0 if args.all else DEFAULT_LIST_COUNT)
    )
    unused_days = getattr(args, "unused_days", None)
    index = _load_index(ctx)
    matched = []
    for c in index["cards"]:
        if args.category and c["category"] != args.category:
            continue
        if args.type and c["type"] != args.type:
            continue
        if args.owner and c["owner"] != args.owner:
            continue
        if unused_days is not None and _days_since(c.get("last_used") or c.get("created")) <= unused_days:
            continue
        if unused_days is not None and (c.get("status") or "done") != "done":
            # 降级候选只收 status=done：stable 是「>30 天且复核合格」的终态，
            # archived 已出局——否则每轮策展都会把同一批老卡重审一遍。
            continue
        matched.append(c)
        if recent > 0 and len(matched) >= recent:
            break

    compact = [
        {
            k: c.get(k, "")
            for k in (
                "id",
                "title",
                "category",
                "type",
                "tech",
                "owner",
                "created",
                "status",
                "last_used",
                "usage_count",
                "source_agent",
                "last_verified",
                "file",
            )
        }
        for c in matched
    ]
    print(json.dumps(compact, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: fields — 列出已有字段值
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_fields(args: argparse.Namespace, ctx=None) -> None:
    """
    从 JSON 索引统计已有的 category/tech/type/owner 值及出现次数。
    帮助写入新卡片时优先复用已有标签。
    """
    ctx = ctx or default_context()
    show = {
        "category": args.category,
        "tech": args.tech,
        "type": args.type_,
        "owner": args.owner,
    }
    if not any(show.values()):
        for k in show:
            show[k] = True

    counters = {k: Counter() for k in show}
    index = _load_index(ctx)

    for c in index["cards"]:
        if show["category"] and c.get("category"):
            counters["category"][c["category"]] += 1
        if show["tech"] and c.get("tech"):
            counters["tech"][c["tech"]] += 1
        if show["type"] and c.get("type"):
            counters["type"][c["type"]] += 1
        if show["owner"] and c.get("owner"):
            counters["owner"][c["owner"]] += 1

    labels = {"category": "category", "tech": "tech", "type": "type", "owner": "owner"}
    if args.json:
        payload = {
            key: [
                {"name": name, "count": cnt}
                for name, cnt in sorted(counters[key].items())
            ]
            for key in labels
            if show[key]
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    for key, label in labels.items():
        if show[key]:
            print(f"── {label} ──")
            if not counters[key]:
                print("  (无)")
            else:
                for k, c in sorted(counters[key].items()):
                    print(f"  {k} ({c})")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: tags — 按标签检索
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_tags(args: argparse.Namespace, ctx=None) -> None:
    """按标签搜索卡片文件，标签以冒号包裹形式存储在 Org 属性中。"""
    ctx = ctx or default_context()
    if not args.tags:
        die("用法: kb tags <标签> [标签2 ...]")

    json_items: list[dict] = []

    for tag in args.tags:
        try:
            if shutil.which("rg"):
                result = subprocess.run(
                    ["rg", "--color=never", "-l", f":{tag}:", str(ctx.experiences)],
                    capture_output=True,
                    text=True,
                )
            else:
                result = subprocess.run(
                    ["grep", "-rl", f":{tag}:", str(ctx.experiences)],
                    capture_output=True,
                    text=True,
                )
        except (KeyboardInterrupt, GeneratorExit, SystemExit):
            raise
        except BaseException as exc:
            die(f"标签检索失败（{type(exc).__name__}）")
        if result.returncode not in (0, 1):
            die("标签检索失败（命令错误）")

        files = (
            [Path(line) for line in result.stdout.splitlines() if line]
            if result.returncode == 0 and result.stdout.strip()
            else []
        )

        if args.json:
            for filepath in files:
                try:
                    content = filepath.read_text(encoding="utf-8")
                except OSError:
                    continue
                card_id = parse_org_prop(content, "ID") or filepath.stem.split("-")[0]
                json_items.append(
                    {
                        "tag": tag,
                        "id": card_id,
                        "title": read_org_title(content),
                    }
                )
            continue

        print(f"── 标签: {tag} ──")
        if files:
            for filepath in files:
                print(filepath)
        else:
            print("  (无匹配)")

    if args.json:
        print(json.dumps(json_items, ensure_ascii=False, indent=2))


# ═══════════════════════════════════════════════════════════════════════════════
# MEMORY 系统：内部工具函数
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_inbox(args: argparse.Namespace, ctx=None) -> None:
    """快速捕获一个想法或待办到 inbox.org。"""
    ctx = ctx or default_context()
    content = args.content
    if not content:
        content = sys.stdin.read()
    if not content.strip():
        die("必须指定内容或通过 stdin 管道输入")

    warn_secret_write(content, "捕获内容")

    ensure_dirs(ctx)
    ts = now()
    entry = f"\n** [{ts}] {content.strip()}\n"
    with ctx.inbox.open("a", encoding="utf-8") as f:
        f.write(entry)
    print(f"已捕获到 {ctx.inbox}")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: stats — 知识库统计
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_stats(args: argparse.Namespace, ctx=None) -> None:
    """输出知识库的统计概览。"""
    ctx = ctx or default_context()
    ensure_dirs(ctx)
    index = _load_index(ctx)
    cards = index["cards"]
    total = len(cards)

    cat_counter: Counter = Counter()
    type_counter: Counter = Counter()
    owner_counter: Counter = Counter()
    tech_counter: Counter = Counter()
    dates = []

    for c in cards:
        cat_counter[c.get("category", "unknown")] += 1
        type_counter[c.get("type", "unknown")] += 1
        owner_counter[c.get("owner", "unknown")] += 1
        tech_counter[c.get("tech", "unknown")] += 1
        if c.get("created"):
            dates.append(c["created"])

    print(f"═══ 知识库统计 ═══")
    print(f"总卡片数: {total}")
    if dates:
        print(f"时间范围: {min(dates)} ~ {max(dates)}")
    print()

    for label, counter in [
        ("按类别", cat_counter),
        ("按类型", type_counter),
        ("按执行者", owner_counter),
        ("按技术栈", tech_counter),
    ]:
        print(f"── {label} ──")
        for k, v in counter.most_common():
            bar = "█" * v
            print(f"  {k:20s} {v:3d} {bar}")
        print()

    # MEMORY 统计
    if ctx.memory_org.exists():
        mem_text = ctx.memory_org.read_text(encoding="utf-8")
        fb_count = len(re.findall(r"^\*\* F\d+", mem_text, re.MULTILINE))
        ref_count = len(re.findall(r"^\*\* R\d+", mem_text, re.MULTILINE))
        dep_section = (
            mem_text[mem_text.find("* deprecated") :]
            if "* deprecated" in mem_text
            else ""
        )
        dep_count = len(re.findall(r"^\*\* F\d+", dep_section, re.MULTILINE))
        proj_section = ""
        if "* project" in mem_text and "* reference" in mem_text:
            proj_start = mem_text.find("* project")
            proj_end = mem_text.find("* reference")
            if proj_start < proj_end:
                proj_section = mem_text[proj_start:proj_end]
        proj_count = len(
            re.findall(r"^\*\* .+\n\s+:PROPERTIES:", proj_section, re.MULTILINE)
        )
        stale_count = 0
        for m in re.finditer(r":UPDATED:\s*\[(\d{4}-\d{2}-\d{2})\]", mem_text):
            try:
                updated = datetime.strptime(m.group(1), "%Y-%m-%d")
                if (datetime.now() - updated).days > STALE_DAYS:
                    stale_count += 1
            except ValueError:
                pass
        print("── MEMORY ──")
        print(f"  feedback 条目数: {fb_count}")
        print(f"  project 索引数: {proj_count}")
        print(f"  reference 条目数: {ref_count}")
        print(f"  deprecated 条目数: {dep_count}")
        print(f"  stale (>{STALE_DAYS}d): {stale_count}")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: connect — 双向链接
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_connect(args: argparse.Namespace, ctx=None) -> None:
    """在两张卡片之间建立双向链接。"""
    ctx = ctx or default_context()
    id_a = args.id_a
    id_b = args.id_b
    desc = args.desc or ""

    if id_a == id_b:
        die("不能链接同一张卡片")

    file_a = _resolve_card(id_a, ctx)
    file_b = _resolve_card(id_b, ctx)
    if not file_a:
        die(f"未找到卡片: {id_a}")
    if not file_b:
        die(f"未找到卡片: {id_b}")

    originals: dict[Path, bytes | None] = {
        file_a: file_a.read_bytes(),
        file_b: file_b.read_bytes(),
    }
    try:
        _append_link(file_a, file_b, desc)
        _append_link(file_b, file_a, desc)
    except BaseException as exc:
        failures = restore_text_files(originals)
        if failures:
            raise RuntimeError(
                f"connect 回滚失败（{', '.join(failures)}）"
            ) from exc
        raise
    print(f"已建立双向链接: {file_a.name} ↔ {file_b.name}")


def _append_link(filepath: Path, target: Path, desc: str) -> None:
    """在卡片的 ** 相关链接 章节追加链接。"""
    import os as _os

    content = filepath.read_text(encoding="utf-8")
    # 使用从源文件到目标文件的相对路径（Org 按文件位置解析）
    rel_path = _os.path.relpath(target, filepath.parent)
    link = (
        f"  [[file:{rel_path}][{read_org_title(target.read_text(encoding='utf-8'))}]]"
    )
    if desc:
        link += f" — {desc}"

    if "** 相关链接" in content:
        content = content.replace(
            "** 相关链接\n",
            f"** 相关链接\n{link}\n",
            1,
        )
    else:
        content = content.rstrip("\n") + f"\n\n** 相关链接\n{link}\n"

    atomic_write(filepath, content)


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: update — 更新卡片
# ═══════════════════════════════════════════════════════════════════════════════


def _rebuild_fingerprint_line(content: str) -> str:
    """按属性重建 :END: 后的 fingerprint 标签行（与 cmd_add 同口径）。

    格式 ``:category:type:owner:tech:entry_type::``；tech 与 category 相同则省略，
    entry 为空则省略。只替换已存在的 fingerprint 行，不新增（旧卡片无则不动）。
    """
    category = parse_org_prop(content, "CATEGORY") or DEFAULT_CATEGORY
    type_ = parse_org_prop(content, "TYPE") or DEFAULT_TYPE
    owner = parse_org_prop(content, "OWNER") or DEFAULT_OWNER
    tech = parse_org_prop(content, "TECH") or ""
    entry = parse_org_prop(content, "ENTRY_TYPE") or ""
    line = build_fingerprint_line(category, type_, owner, tech, entry)
    lines = content.split("\n")
    end_idx = next((i for i, ln in enumerate(lines) if ln.strip() == ":END:"), None)
    if end_idx is None:
        return content
    for j in range(end_idx + 1, min(end_idx + 4, len(lines))):
        stripped = lines[j].strip()
        if stripped.startswith(":") and stripped.endswith("::"):
            lines[j] = line
            break
    return "\n".join(lines)


def cmd_update(args: argparse.Namespace, ctx=None) -> None:
    """更新已有卡片的属性或追加内容。

    属性替换使用 .+ 匹配到行尾，避免含空格的属性值被截断。
    category/tech/type/owner 任一变更都会重建 fingerprint 标签行并刷新索引，
    避免属性与标签/索引漂移（--status 保持轻量，仍由 reindex 刷新）。
    """
    ctx = ctx or default_context()
    target = args.target
    card = _resolve_card(target, ctx)
    if not card:
        die(f"未找到卡片: {target}")

    index = _load_index(ctx)
    content = card.read_text(encoding="utf-8")
    old_type = parse_org_prop(content, "TYPE") or DEFAULT_TYPE
    original = card.read_bytes()
    updated = card
    reclassify = False

    # ── 更新属性（用 .+ 匹配到行尾，避免含空格的属性值被截断）────────────
    if args.status:
        if args.status not in VALID_STATUSES:
            die(f"无效状态: {args.status}（可选: {', '.join(sorted(VALID_STATUSES))}）")
        content = set_org_prop(content, "STATUS", args.status)
        # --status stable 自动更新 LAST_VERIFIED
        if args.status == "stable":
            content = set_org_prop(content, "LAST_VERIFIED", f"[{now()}]")
    if args.category:
        validate_category(args.category)
        content = set_org_prop(content, "CATEGORY", args.category)
        reclassify = True
    if args.tech:
        content = set_org_prop(content, "TECH", args.tech)
        reclassify = True
    if args.type_ and args.type_ != old_type:
        # 与 add 相同的 type 门禁：防止通过 update 绕过晋升规则
        index = _gate_type(args.type_, ctx, getattr(args, "force", False))
        content = set_org_prop(content, "TYPE", args.type_)
        reclassify = True
    if args.owner:
        content = set_org_prop(content, "OWNER", args.owner)
        reclassify = True

    if reclassify:
        content = _rebuild_fingerprint_line(content)

    # 追加内容到指定章节（写入侧 secret 门禁与 add 同口径）
    if args.append_to and args.append_text:
        gate_secret_write(
            args.append_text, "卡片追加",
            allow=getattr(args, "allow_secret", False),
        )
        section = f"** {args.append_to}"
        if section in content:
            content = content.replace(
                section,
                f"{section}\n{args.append_text}",
                1,
            )
        else:
            content = content.rstrip("\n") + f"\n\n{section}\n{args.append_text}\n"

    # 从 stdin 追加到末尾（在 PROPERTIES 和标签之后）
    if args.stdin:
        extra = sys.stdin.read()
        if extra.strip():
            gate_secret_write(
                extra, "卡片追加",
                allow=getattr(args, "allow_secret", False),
            )
            content = content.rstrip("\n") + f"\n\n{extra.strip()}\n"

    original_index = ctx.index.read_bytes() if ctx.index.exists() else None
    try:
        atomic_write(card, content)
        # 重分类收尾：type 变更时文件名同步为 {id}-{type}-{category}.org，并刷新索引
        if args.type_ and args.type_ != old_type:
            card_id = parse_org_prop(content, "ID") or card.stem.split("-")[0]
            category = parse_org_prop(content, "CATEGORY") or DEFAULT_CATEGORY
            new_path = card.parent / f"{card_id}-{args.type_}-{category}.org"
            if new_path != card and not new_path.exists():
                card.rename(new_path)
                updated = new_path
        if reclassify:
            _upsert_card(index, updated, ctx)
            _save_index(index, ctx)
    except BaseException as exc:
        # 回滚分两步：先恢复必须存续的 card，只有恢复写成功才删除改名后的
        # 新文件——card 恢复失败（如磁盘满）时保留新副本，避免仅存的一份内容
        # 也被 unlink 清掉（双副本全丢）；索引恢复独立收尾，失败一并上报。
        failures = restore_text_files({card: original})
        if updated != card and not failures:
            failures += restore_text_files({updated: None})
        failures += restore_text_files({ctx.index: original_index})
        if failures:
            raise RuntimeError(
                f"update 回滚失败（{', '.join(failures)}）"
            ) from exc
        raise
    print(f"已更新: {updated}")


# ═══════════════════════════════════════════════════════════════════════════════
# 新命令: touch — 更新时间戳
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_touch(args: argparse.Namespace, ctx=None) -> None:
    """更新卡片的时间戳。"""
    ctx = ctx or default_context()
    target = args.target
    card = _resolve_card(target, ctx)
    if not card:
        die(f"未找到卡片: {target}")

    session = getattr(args, "session", None)
    originals: dict[Path, bytes | None] = {card: card.read_bytes()}
    original_index = ctx.index.read_bytes() if ctx.index.exists() else None
    try:
        if args.used_only:
            touch_card(card, "LAST_USED", ctx, session=session)
        else:
            touch_card(card, "LAST_USED", ctx, session=session)
            # 第二次只刷时间戳不计数——否则无 session 时单次 touch 计两次使用
            touch_card(card, "LAST_VERIFIED", ctx, session=session, count=False)
    except BaseException as exc:
        originals[ctx.index] = original_index
        failures = restore_text_files(originals)
        if failures:
            raise RuntimeError(
                f"touch 回滚失败（{', '.join(failures)}）"
            ) from exc
        raise
    if args.used_only:
        print(f"已更新 LAST_USED: {card.name}")
    else:
        print(f"已更新 LAST_USED + LAST_VERIFIED: {card.name}")


# ═══════════════════════════════════════════════════════════════════════════════
# 子命令: sweep — done/stable → stale 降级候选（S2）
# ═══════════════════════════════════════════════════════════════════════════════


def _sweep_candidates(index: dict) -> tuple[list[dict], list[dict]]:
    """双键判龄：done 按 last_used（缺省 created），stable 按 last_verified。

    list --unused-days 只收 done（其参数语义即 last_used）；stable 的
    降级入口在这里，避免一个命令两套判龄。
    """
    done_cands, stable_cands = [], []
    for c in index["cards"]:
        status = c.get("status") or "done"
        if status == "done":
            age = _days_since(c.get("last_used") or c.get("created"))
            if age > STALE_DAYS:
                done_cands.append({**c, "days": age})
        elif status == "stable":
            age = _days_since(c.get("last_verified"))
            if age > ARCHIVE_THRESHOLD_DAYS:
                stable_cands.append({**c, "days": age})
    return done_cands, stable_cands


def cmd_sweep(args: argparse.Namespace, ctx=None) -> None:
    """列出或执行 done/stable → stale 降级（默认 dry-run，只读出清单）。"""
    ctx = ctx or default_context()
    index = _load_index(ctx)
    done_cands, stable_cands = _sweep_candidates(index)

    if not getattr(args, "apply", False):
        if getattr(args, "json", False):
            print(json.dumps({"done": done_cands, "stable": stable_cands},
                             ensure_ascii=False, indent=2))
            return
        for c in done_cands:
            print(f"  [done] {c['id']}  {c.get('title', '')[:60]}  (>{c['days']}天未用)")
        for c in stable_cands:
            print(f"  [stable] {c['id']}  {c.get('title', '')[:60]}  (>{c['days']}天未验证)")
        print(f"\n共 {len(done_cands) + len(stable_cands)} 张降级候选"
              f"（done {len(done_cands)} / stable {len(stable_cands)}）"
              f"—— dry-run，未改动；加 --apply 执行")
        return

    targets = done_cands + stable_cands
    if not targets:
        print("无降级候选，无需执行。")
        return
    # 先完成整批解析与内容准备；任何卡片缺失时都不写盘。
    prepared: list[tuple[Path, bytes, str]] = []
    for cand in targets:
        card = _resolve_card(cand["id"], ctx)
        if not card:
            die(f"未找到卡片: {cand['id']}")
        original_bytes = card.read_bytes()
        content = original_bytes.decode("utf-8")
        content = set_org_prop(content, "STATUS", "stale")
        content = set_org_prop(content, "LAST_VERIFIED", f"[{now()}]")
        prepared.append((card, original_bytes, content))

    original_index = ctx.index.read_bytes() if ctx.index.exists() else None
    try:
        for card, _original, content in prepared:
            atomic_write(card, content)
        for card, _original, _content in prepared:
            _upsert_card(index, card, ctx)
        _save_index(index, ctx)
    except BaseException as exc:
        originals: dict[Path, bytes | None] = {
            card: original for card, original, _content in prepared
        }
        originals[ctx.index] = original_index
        failures = restore_text_files(originals)
        if failures:
            raise RuntimeError(
                f"sweep 回滚失败（{', '.join(failures)}）"
            ) from exc
        raise
    print(f"已降级 {len(prepared)} 张为 stale: "
          f"{', '.join(card.name for card, _, _ in prepared)}")


# ═══════════════════════════════════════════════════════════════════════════════
# 新命令: merge — 合并卡片
# ═══════════════════════════════════════════════════════════════════════════════


def cmd_merge(args: argparse.Namespace, ctx=None) -> None:
    """将 secondary 卡片合并到 primary 卡片。"""
    ctx = ctx or default_context()
    primary_id = args.primary
    secondary_ids = args.secondary

    primary = _resolve_card(primary_id, ctx)
    if not primary:
        die(f"未找到主卡片: {primary_id}")

    original_bytes = primary.read_bytes()
    primary_content = original_bytes.decode("utf-8")
    if not has_top_property_drawer(primary_content):
        die("主卡片缺少顶层 PROPERTIES 抽屉")
    original_contents: dict[Path, bytes] = {primary: original_bytes}
    merged_from_ids = []
    planned_secondary: list[tuple[Path, str, str]] = []

    for sec_id in secondary_ids:
        sec = _resolve_card(sec_id, ctx)
        if not sec:
            print(f"警告: 未找到卡片 {sec_id}，跳过", file=sys.stderr)
            continue
        if sec == primary:
            print(f"警告: 跳过自身合并 {sec_id}", file=sys.stderr)
            continue

        sec_bytes = sec.read_bytes()
        sec_content = sec_bytes.decode("utf-8")
        original_contents[sec] = sec_bytes
        sec_title = read_org_title(sec_content)
        sec_card_id = parse_org_prop(sec_content, "ID") or sec.stem.split("-")[0]
        merged_from_ids.append(sec_card_id)

        # 追加到 primary
        merge_section = f"\n** 合并来源: {sec_title}\n   :PROPERTIES:\n   :MERGED_FROM: {sec_card_id}\n   :END:\n\n"
        # 提取 secondary 正文
        body_lines = []
        in_props = False
        past_header = False
        for line in sec_content.split("\n"):
            if line.startswith("* DONE") or line.startswith("* TODO"):
                past_header = True
                continue
            if past_header and line.startswith(":PROPERTIES:"):
                in_props = True
                continue
            if in_props:
                if line.startswith(":END:"):
                    in_props = False
                continue
            if past_header and not in_props and re.match(r"^:\w+:", line):
                continue
            if past_header and not in_props:
                body_lines.append(line)
        body = "\n".join(body_lines).strip()
        primary_content = primary_content.rstrip("\n") + f"\n{merge_section}{body}\n"

        sec_new = set_org_prop(sec_content, "STATUS", "archived")
        if not parse_org_prop(sec_new, "MERGED_INTO"):
            sec_new = set_org_prop(sec_new, "MERGED_INTO", primary_id)
        planned_secondary.append((sec, sec_card_id, sec_new))

    if not merged_from_ids:
        die("没有可合并的卡片")

    # 所有读改写和索引准备先完成；写阶段按 secondary → primary 顺序。
    merged_str = ",".join(merged_from_ids)
    primary_content = set_org_prop(primary_content, "MERGED_FROM", merged_str)
    index = _load_index(ctx)
    original_index = ctx.index.read_bytes() if ctx.index.exists() else None

    try:
        for sec, _sec_card_id, sec_new in planned_secondary:
            atomic_write(sec, sec_new)

        atomic_write(primary, primary_content)

        # 所有文件写入成功后再一次性发布索引；任何失败均回滚已写卡片。
        for sec, _sec_card_id, _sec_new in planned_secondary:
            _upsert_card(index, sec, ctx)
        _upsert_card(index, primary, ctx)
        _save_index(index, ctx)
    except BaseException as exc:
        # atomic_write 只保证单文件；跨文件 merge 需自行恢复所有已写卡片与索引。
        originals: dict[Path, bytes | None] = dict(original_contents)
        originals[ctx.index] = original_index
        failures = restore_text_files(originals)
        if failures:
            raise RuntimeError(
                f"merge 回滚失败（{', '.join(failures)}）"
            ) from exc
        raise

    for _sec, _sec_card_id, _sec_new in planned_secondary:
        print(f"  已归档: {_sec.name}")
    print(f"已合并 {len(merged_from_ids)} 张卡片到: {primary.name}")

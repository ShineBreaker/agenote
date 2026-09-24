# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.projector — N3 export 投影器：SSOT→宿主聚合投影（单向派生物）。

zcode/claude 聚合 Markdown + codex/pi 建议清单 + hermes 待录入清单（§切分，
手动录入）+ reasonix 既有 slug 直写（per-entry，不动 MEMORY.md 索引）。
KB 外写入纪律：走 safeio._atomic_replace_bytes（与 atomic_write 同机制，
tmp+rename，不留半文件），不持 kb_lock；路径只来自 SCHEMA，不接受命令行任意路径。
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
from pathlib import Path

from agenote import config
from agenote.core import today
from agenote.orgserde import parse_memory_date
from agenote.safeio import _atomic_replace_bytes, atomic_write, safe_read_text

AGGREGATE_NAME = "agenote-profile.md"
SUGGEST_NAME = "agenote-suggestions.md"
POINTER_MARK = "x-agenote-pointer"
MARKER_KEY = "x-agenote-projected"
EXPORT_STATE = ".memory-export.json"
CONFLICTS_STATE = ".memory-conflicts.json"

TYPE_SECTIONS = (("U", "user"), ("F", "feedback"), ("P", "project"),
                 ("E", "environment"), ("R", "reference"))

# 高置信密钥前缀子集（v1 不做行为分析，不承诺完备；命中只记类别不记值）
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("sk-ant-key", re.compile(r"sk-ant-[A-Za-z0-9\-_]{8,}")),
    ("openai-key", re.compile(r"sk-[A-Za-z0-9]{16,}")),
    ("github-token", re.compile(r"gh[pousr]_[A-Za-z0-9]{8,}")),
    ("gitlab-token", re.compile(r"glpat-[A-Za-z0-9\-_]{8,}")),
    ("slack-token", re.compile(r"xox[abpras]-[A-Za-z0-9\-]{8,}")),
    ("aws-key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("google-key", re.compile(r"AIza[0-9A-Za-z\-_]{20,}")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def scan_secrets(text: str) -> list[str]:
    """扫正文命中类别（只回类别名，永不回值）。"""
    return sorted({name for name, rx in SECRET_PATTERNS if rx.search(text)})


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def machine_key() -> str:
    """E 类事件驱动重验的机器键：配置 machine_key 为空则取 hostname。"""
    return str(config.get("memories", "machine_key") or socket.gethostname())


def _truthy(val: object) -> bool:
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() not in ("", "0", "false", "no", "off")


def target_dirs() -> dict[str, Path]:
    """非空 [memories.targets] 投影根（空 = 该目标不投影）。"""
    out: dict[str, Path] = {}
    for key, name in (("zcode_dir", "zcode"), ("claude_dir", "claude"),
                      ("codex_suggest_dir", "codex"), ("reasonix_dir", "reasonix"),
                      ("pi_suggest_dir", "pi"), ("hermes_suggest_dir", "hermes")):
        raw = str(config.get("memories.targets", key) or "").strip()
        if raw:
            out[name] = config.get_path("memories.targets", key)
    return out


def export_target_prefixes() -> list[str]:
    """N2 回声排除共用口径：targets 任一非空前缀匹配即跳过（import 侧复用本函数）。

    ponytail: 字符串前缀比对，symlink/大小写归一化上游 N2 做。
    """
    return [str(p) for p in target_dirs().values()]


def state_path(ctx) -> Path:
    base = getattr(ctx, "root", None) or ctx.memory_org.parent
    return Path(base) / EXPORT_STATE


def conflicts_path(ctx) -> Path:
    """冲突队列路径（N2 落盘，N4 只读列出；缺失即无冲突）。"""
    base = getattr(ctx, "root", None) or ctx.memory_org.parent
    return Path(base) / CONFLICTS_STATE


def load_export_state(ctx) -> dict:
    p = state_path(ctx)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _marker_of(text: str) -> str:
    m = re.search(rf"{re.escape(MARKER_KEY)}:\s*(\S+)", text)
    return m.group(1).strip() if m else ""


def _read_target(path: Path) -> str | None:
    """S8 受控读目标聚合文件；缺失返回 None。"""
    if not path.exists():
        return None
    return safe_read_text(path)


def _write_if_changed(path: Path, text: str) -> bool:
    """字节相同不重写（幂等）；KB 外路径。返回是否写入。"""
    if path.exists():
        try:
            if path.read_bytes() == text.encode("utf-8"):
                return False
        except OSError:
            pass
    _atomic_replace_bytes(path, text.encode("utf-8"))
    return True


def _stale_days() -> int:
    try:
        return max(0, int(str(config.get("memories", "export_stale_days"))))
    except (TypeError, ValueError):
        return 30


def _entry_stale(entry: dict) -> bool:
    """VALIDATED_AT 缺失或超 export_stale_days 即附时效标记。"""
    days = _stale_days()
    vd = parse_memory_date(entry.get("validated_at") or "")
    if vd is None:
        return True
    from datetime import datetime
    return (datetime.now().date() - vd).days > days


def _select_entries(args, entries: list[dict]) -> list[dict]:
    """默认全量 active（deprecated 节除外）；U 全投，P 无 --project 时排除。"""
    want_type = getattr(args, "type", None)
    want_scope = getattr(args, "scope", None)
    want_project = getattr(args, "project", None)
    # --project 传路径时只取末段名比对（CLI 复用 IDENTIFIER 参数）
    if want_project and "/" in str(want_project):
        want_project = Path(str(want_project)).name
    rows = []
    for e in entries:
        if e["section"].lower() == "deprecated":
            continue
        if want_type and e["type"] != want_type:
            continue
        scope = (e["props"].get("SCOPE") or "").strip().lower()
        if want_scope and scope != str(want_scope).lower():
            continue
        if e["type"] == "P":
            if not want_project:
                continue
            props = e["props"]
            hit = (props.get("PROJECT") == want_project or e["id"] == want_project
                   or str(want_project) in e["title"])
            if not hit:
                continue
        rows.append(e)
    return rows


def _entry_lines(e: dict) -> list[str]:
    """聚合条目渲染（build_profile 与 reasonix 直写共用）。"""
    line = f"- ** {e['id']} {e['title']}".rstrip()
    if _entry_stale(e):
        line += " (unverified)"
    out = [line]
    if e["hook"]:
        out.append(f"  # {e['hook']}")
    return out


def build_profile(entries: list[dict]) -> str:
    """聚合投影：frontmatter（含 marker）+ 按类型分节（标题+钩子索引级投影，正文留 SSOT）。"""
    first = entries[0] if entries else None
    desc = (first["hook"] or first["title"]) if first else "agenote SSOT 投影"
    by_type: dict[str, list[dict]] = {}
    for e in entries:
        by_type.setdefault(e["type"] or "?", []).append(e)
    parts = []
    for short, sec in TYPE_SECTIONS:
        rows = by_type.get(short, [])
        if not rows:
            continue
        parts.append(f"\n## {sec}\n")
        for e in rows:
            parts.extend(_entry_lines(e))
    # 未知类型兜底一节（不静默丢条目）
    rest = [e for t, es in by_type.items()
            for e in es if t not in {s for s, _ in TYPE_SECTIONS}]
    if rest:
        parts.append("\n## misc\n")
        for e in rest:
            parts.extend(_entry_lines(e))
    body = "\n".join(parts).rstrip() + "\n" if parts else "(空)\n"
    marker = content_hash(body)
    head = (f"---\nname: agenote-profile\ndescription: {desc[:120]}\n"
            f"type: project\n{MARKER_KEY}: {marker}\n"
            f"x-agenote-generated: {today()}\n---\n"
            f"# agenote memories（SSOT 派生物：宿主请勿直接改，下轮 export 覆盖）\n")
    return head + body


def build_suggestions(entries: list[dict]) -> str:
    """codex 建议清单：只出清单不直写其记忆树（git baseline + 整合代理归宿主）。"""
    lines = [f"# agenote 记忆建议清单（{today()} 生成，需人工应用）\n"]
    for short, sec in TYPE_SECTIONS:
        rows = [e for e in entries if e["type"] == short]
        if not rows:
            continue
        lines.append(f"\n## {sec}\n")
        for e in rows:
            lines.append(f"- ** {e['id']} {e['title']}".rstrip()
                         + (f" — {e['hook']}" if e["hook"] else ""))
    return "\n".join(lines).rstrip() + "\n"


def build_hermes_suggest(entries: list[dict]) -> str:
    """hermes 待录入清单：禁直写 memories/*.md，只出 §切分文本手动录入。

    每条一节（§ 行分隔），首句为 40 字内摘要，正文禁 § 字符。
    """
    head = (f"# agenote hermes 待录入清单（{today()} 生成，需经 memory 工具手动录入）\n"
            f"# 路由：U→USER.md（user memory），F/P/E/R→MEMORY.md（memory）；"
            f"正文已剔除切分符\n")
    blocks = []
    for e in entries:
        first = (e["hook"] or e["title"])[:40].replace("§", "")
        body = "\n".join([f"- ** {e['id']} {e['title']}".rstrip()]
                         + ([f"  # {e['hook']}"] if e["hook"] else [])).replace("§", "")
        blocks.append(f"{first}\n{body}")
    return head + "\n§\n".join(blocks).rstrip() + "\n" if blocks else head + "(空)\n"


REASONIX_TYPE_WORDS = {"U": "user", "F": "feedback", "P": "project",
                       "E": "environment", "R": "reference"}


def build_reasonix_entry(e: dict) -> str:
    """reasonix per-entry 直写文件：frontmatter 贴近原生键名，id/revision 留空注来源。"""
    body = "\n".join(_entry_lines(e))
    # 正文禁首行超长（读侧标题启发式截断）：首行兜底截断
    lines = body.split("\n")
    lines[0] = lines[0][:120]
    body = "\n".join(lines)
    marker = content_hash(e["id"] + body)
    head = (f"---\nname: agenote-{e['id']}\ntitle: {e['title'][:120]}\n"
            f"description: {(e['hook'] or e['title'])[:120]}\n"
            f"id: \"\"  # SSOT id 见正文（agenote 投影，宿主请勿直接改）\n"
            f"revision: \"\"  # 同上\n"
            f"metadata:\n  type: {REASONIX_TYPE_WORDS.get(e['type'], 'reference')}\n---\n")
    tail = f"\n<!-- {MARKER_KEY}: {marker}（agenote SSOT 投影） -->\n"
    return head + body + tail


def _reasonix_slugs(root: Path) -> list[str]:
    """枚举 reasonix 根下既有 project slug（只读既有目录，绝不新建）。"""
    proot = root / "projects"
    if not proot.is_dir():
        return []
    return sorted(p.parent.name for p in proot.glob("*/memory") if p.is_dir())


def _sync_pointer(index_path: Path, aggregate: str = AGGREGATE_NAME) -> bool:
    """宿主索引单行指针追加/更新（幂等；只动 marker 行，不碰宿主正文）。"""
    line = f"- [agenote-profile]({aggregate}) <!-- {POINTER_MARK} -->"
    if not index_path.exists():
        _atomic_replace_bytes(index_path, (line + "\n").encode("utf-8"))
        return True
    try:
        cur = safe_read_text(index_path)
    except OSError:
        return False
    lines = cur.split("\n")
    for i, ln in enumerate(lines):
        if POINTER_MARK in ln:
            if ln.strip() == line:
                return False
            lines[i] = line
            break
    else:
        lines.append(line)
    _atomic_replace_bytes(index_path, "\n".join(lines).encode("utf-8"))
    return True


def cmd_export(args, ctx=None) -> None:
    """`memory --export`：幂等投影 + 漂移检测 + 双道闸。MUTATING 但不持 kb_lock。"""
    from agenote import memory as _mem  # lazy：memory 侧同样 lazy，避免成环

    ctx = ctx or _mem.default_context()
    if not ctx.memory_org.exists():
        print("(记忆文件不存在)")
        return
    entries = _select_entries(args, _mem._iter_memory_entries(
        _mem._read_memory_org_text(ctx)))
    profile = build_profile(entries)
    if _truthy(config.get("memories", "secret_scan_enabled")):
        hits = scan_secrets(profile)
        if hits:
            _mem.die(f"export 被 secret 门禁拦截: {', '.join(hits)}（只记类别不记值）")

    state = load_export_state(ctx)
    rec_targets: dict = state.get("targets", {})
    mk = machine_key()
    if state.get("machine_key") and state["machine_key"] != mk:
        print(f"[!] machine_key 变更 {state['machine_key']} → {mk}："
              f"SCOPE=machine 的 E 条目待重验（N5 --revalidate 接管）")
    targets = target_dirs()
    if not targets:
        print("(未配置 [memories.targets]，无投影目标)")
    for name, root in targets.items():
        if name in ("codex", "pi"):
            path = root / SUGGEST_NAME
            text = build_suggestions(entries)
            wrote = _write_if_changed(path, text)
            print(f"{name} 建议清单 → {path}（{'已更新' if wrote else '无变化跳过'}）")
            if wrote:
                rec_targets[name] = {"path": str(path),
                                     "content_hash": content_hash(text)}
            continue
        if name == "hermes":
            path = root / SUGGEST_NAME
            text = build_hermes_suggest(entries)
            wrote = _write_if_changed(path, text)
            print(f"hermes 待录入清单 → {path}（{'已更新' if wrote else '无变化跳过'}）")
            if wrote:
                rec_targets[name] = {"path": str(path),
                                     "content_hash": content_hash(text)}
            continue
        if name == "reasonix":
            slug = getattr(args, "project", None)
            if slug and "/" in str(slug):
                slug = Path(str(slug)).name
            slugs = _reasonix_slugs(root)
            if not slug or slug not in slugs:
                _mem.die(f"reasonix 直写需 --project 指定既有 slug"
                         f"（既有：{', '.join(slugs) if slugs else '无'}）")
            memdir = root / "projects" / slug / "memory"
            n = 0
            for e in entries:
                path = memdir / f"agenote-{e['id']}.md"
                text = build_reasonix_entry(e)
                if path.exists() and not _marker_of(_read_target(path) or ""):
                    print(f"[!] reasonix → {path} 存在非投影文件，本次不覆盖")
                    continue
                if _write_if_changed(path, text):
                    n += 1
            print(f"reasonix → {memdir}（{n} 个条目更新）")
            rec_targets[name] = {"path": str(memdir), "content_hash": content_hash(profile)}
            continue
        path = root / AGGREGATE_NAME
        cur = _read_target(path)
        if cur is not None and cur == profile:
            print(f"{name} → {path}（无变化跳过）")
        elif cur is None:
            _atomic_replace_bytes(path, profile.encode("utf-8"))
            _sync_pointer(root / "MEMORY.md")
            print(f"{name} → {path}（新建）")
            rec_targets[name] = {"path": str(path), "content_hash": content_hash(profile)}
        else:
            # 漂移检测：文件整体 hash ≠ 上次投影记录 → 宿主改过，不覆盖
            rec_hash = rec_targets.get(name, {}).get("content_hash", "")
            if rec_hash and content_hash(cur) != rec_hash:
                print(f"[!] {name} → {path} 漂移：宿主已改聚合文件，"
                      f"本次不覆盖（走 import 重新裁决）")
            elif not rec_hash and not _marker_of(cur):
                print(f"[!] {name} → {path} 漂移：目标存在非投影文件，"
                      f"本次不覆盖（走 import 重新裁决）")
            else:  # 记录丢失但 marker 自证是旧投影，或 hash 一致 → 宿主未动
                _atomic_replace_bytes(path, profile.encode("utf-8"))
                _sync_pointer(root / "MEMORY.md")
                print(f"{name} → {path}（已更新）")
                rec_targets[name] = {"path": str(path), "content_hash": content_hash(profile)}
    state["targets"] = rec_targets
    state["machine_key"] = mk
    state["exported_at"] = today()
    atomic_write(state_path(ctx), json.dumps(state, ensure_ascii=False, indent=1) + "\n")

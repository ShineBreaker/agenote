#!/usr/bin/env python3
"""校验**不能代码生成**的产物与 spec/injection.toml 一致。

`generate.py` 负责写源码里的常量块；本脚本负责另外两类无法安全重写的产物：

1. **JSON 里的正则**（`agenote-zcode/hooks/hooks.json`）——JSON 不支持注释，
   塞不进生成块标记。改为验证 spec 的每个信号都能被该正则命中。
   正则刻意写得比清单紧凑（如 `[Dd]one[.!]` 一条覆盖 `done.` 与 `done!`），
   所以这里验的是**覆盖度**而非字符串相等。

2. **Markdown 散文**（`agenote-review/references/triggers.md`）——该文件除信号
   清单外还有机制说明与调整原则，整体重写会毁掉人工内容。改为校验两节
   显式完成信号的集合与 spec 相等。

另外做一次**副本清点**：仓库里任何定义 `COMPLETION_SIGNALS` 的文件都必须是
codegen 的注册目标。这条是防回归的关键——迁移前信号散落 6 处靠纪律同步，
现在新增一份未登记的副本会直接让本脚本失败。

用法::

    python3 tools/codegen/check.py     # 有漂移则退出码 1
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools" / "codegen"))
from generate import TARGETS, load_spec  # noqa: E402

problems: list[str] = []


def fail(msg: str) -> None:
    problems.append(msg)


# ══════════════════════════════════════════════════════════════
# 1. zcode hooks.json 的 matcher 正则覆盖度
# ══════════════════════════════════════════════════════════════


def check_zcode_matcher(spec) -> None:
    path = REPO_ROOT / "packages/agenote-zcode/hooks/hooks.json"
    data = json.loads(path.read_text())
    rules = data.get("hooks", {}).get("UserPromptSubmit", [])
    matchers = [r.get("matcher") for r in rules if r.get("matcher")]
    if not matchers:
        fail("hooks.json 的 UserPromptSubmit 没有任何 matcher，无法做信号覆盖校验")
        return

    # 逐条 hook 校验：只要**存在一条**规则能覆盖全部信号即可（当前就是单条）。
    signals = spec.signals()
    for matcher in matchers:
        try:
            rx = re.compile(matcher)
        except re.error as exc:
            fail(f"hooks.json matcher 不是合法正则：{matcher!r}（{exc}）")
            continue
        missed = []
        for s in signals:
            # 宿主实际匹配的是用户整句，信号常作为子串出现（"搞定了"），
            # 故用带后缀的样本验覆盖度，贴近真实输入。
            if not (rx.search(s) or rx.search(f"{s}了") or rx.search(f" {s}")):
                missed.append(s)
        if not missed:
            return
    fail(
        "hooks.json 的 UserPromptSubmit matcher 未覆盖 spec 的完成信号："
        + "、".join(missed)
    )


# ══════════════════════════════════════════════════════════════
# 2. triggers.md 散文清单
# ══════════════════════════════════════════════════════════════


def _bullets_under(text: str, heading: str) -> list[str] | None:
    """取某个 `### 标题` 小节下的一级无序列表项。"""
    m = re.search(
        rf"^###\s+{re.escape(heading)}\s*$(.*?)(?=^###\s|^##\s|\Z)",
        text,
        re.M | re.S,
    )
    if not m:
        return None
    return re.findall(r"^-\s+(.+?)\s*$", m.group(1), re.M)


def check_triggers_md(spec) -> None:
    path = (
        REPO_ROOT
        / "packages/agenote-skills/agenote-review/references/triggers.md"
    )
    text = path.read_text()
    zh = _bullets_under(text, "中文显式完成")
    en = _bullets_under(text, "英文显式完成")
    if zh is None or en is None:
        fail("triggers.md 缺少「中文显式完成」/「英文显式完成」小节，无法校验")
        return

    want_zh = spec["signals"]["completion"]["zh"]
    want_en = spec["signals"]["completion"]["en"]
    for label, got, want in (("中文", zh, want_zh), ("英文", en, want_en)):
        if got != want:
            only_doc = [s for s in got if s not in want]
            only_spec = [s for s in want if s not in got]
            fail(
                f"triggers.md 的{label}显式完成信号与 spec 不一致："
                f"多出 {only_doc or '无'}，缺少 {only_spec or '无'}"
            )


# ══════════════════════════════════════════════════════════════
# 3. 副本清点：不允许出现未登记的 COMPLETION_SIGNALS 定义
# ══════════════════════════════════════════════════════════════

_SRC_SUFFIXES = {".ts", ".js", ".mjs", ".py", ".sh"}


def check_no_rogue_copies() -> None:
    registered = {
        (REPO_ROOT / rel, key)
        for rel, _prefix, blocks in TARGETS
        for key, _ in blocks
    }
    registered_paths = {p for p, key in registered if key == "signals.completion"}

    for path in (REPO_ROOT / "packages").rglob("*"):
        if path.suffix not in _SRC_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(errors="replace")
        if "COMPLETION_SIGNALS" not in text:
            continue
        if "BEGIN GENERATED: signals.completion" in text:
            if path not in registered_paths:
                fail(
                    f"{path.relative_to(REPO_ROOT)} 有生成块标记但未在 "
                    f"generate.py 的 TARGETS 里登记——两边会漂移"
                )
            continue
        # 纯引用（如 import / 调用点）不算副本；只看赋值定义。
        if re.search(r"COMPLETION_SIGNALS\s*=", text):
            fail(
                f"{path.relative_to(REPO_ROOT)} 自行定义了 COMPLETION_SIGNALS，"
                f"但不在 generate.py 的 TARGETS 中——请改 spec 后重新生成，"
                f"不要新开一份手工副本"
            )


# ══════════════════════════════════════════════════════════════


def main() -> int:
    spec = load_spec()
    check_zcode_matcher(spec)
    check_triggers_md(spec)
    check_no_rogue_copies()

    if problems:
        print("spec 一致性检查未通过：", file=sys.stderr)
        for p in problems:
            print(f"  ✗ {p}", file=sys.stderr)
        return 1
    print("✓ hooks.json matcher / triggers.md 散文 / 副本清点 三项均与 spec 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

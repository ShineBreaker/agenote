#!/usr/bin/env python3
"""从 spec/injection.toml 生成各宿主插件的常量块。

设计约束（ADR 0005）：

* 宿主插件是**独立安装**的（`~/.zcode/plugins/`、omp 扩展目录、hermes 插件
  目录），运行时不能依赖 monorepo 存在。因此生成物**提交进仓**，插件零运行时
  依赖；漂移由 CI 的 `python3 tools/codegen/generate.py && git diff --exit-code`
  拦截，而不是靠开发纪律。
* 标���块自带 BEGIN/END 标记，工具只改写两行之间的内容，标记外的代码与注释
  一律不碰——所以生成块可以嵌在文件中间，不要求整文件模板化。

用法::

    python3 tools/codegen/generate.py          # 写入生成块（幂等）
    python3 tools/codegen/generate.py --check  # 只校验，不写；漂移则退出码 1
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Callable

try:  # 3.11+ 内置；3.10 走 tomli（与 CLI 同一依赖口径）
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 取决于解释器版本
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "spec" / "injection.toml"

BEGIN_FMT = "─── BEGIN GENERATED: {key} ───"
END_FMT = "─── END GENERATED: {key} ───"
NOTE = "本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py"


# ══════════════════════════════════════════════════════════════
# spec 载入
# ══════════════════════════════════════════════════════════════


class Spec(dict):
    """带取值校验的 spec 视图。缺字段即报错——宁可生成失败，也不要静默写出错值。"""

    def host(self, name: str) -> dict:
        try:
            return self["hosts"][name]
        except KeyError:
            raise SystemExit(f"spec/injection.toml 缺少 [hosts.{name}] 段")

    def signals(self) -> list[str]:
        return [*self["signals"]["completion"]["zh"], *self["signals"]["completion"]["en"]]

    def budget(self, name: str, kind: str) -> int:
        h = self.host(name)
        if kind not in h:
            raise SystemExit(
                f"spec/injection.toml 的 [hosts.{name}] 缺 {kind} 预算——"
                f"mode={h.get('mode')} 的宿主必须有该预算"
            )
        return h[kind]

    def timeout(self, name: str) -> int | None:
        return self.host(name).get("cli_timeout_ms")


def load_spec() -> Spec:
    with SPEC_PATH.open("rb") as fh:
        spec = Spec(tomllib.load(fh))
    if spec.get("schema") != 1:
        raise SystemExit("spec/injection.toml 的 schema 不是 1，本生成器不认")
    return spec


# ══════════════════════════════════════════════════════════════
# 渲染器
# ══════════════════════════════════════════════════════════════


def _q(s: str) -> str:
    """JS/TS 双引号字符串字面量。信号清单不含双引号，出现即视为 spec 写错。"""
    if '"' in s or "\\" in s:
        raise SystemExit(f"信号 {s!r} 含双引号或反斜杠，渲染规则需相应扩充")
    return f'"{s}"'


def _qp(s: str) -> str:
    """Python 双引号字符串字面量。"""
    if '"' in s or "\\" in s:
        raise SystemExit(f"信号 {s!r} 含双引号或反斜杠，渲染规则需相应扩充")
    return f'"{s}"'


def render_signals_js(spec: Spec, indent: str = "  ", decl: str = "const") -> str:
    zh = spec["signals"]["completion"]["zh"]
    en = spec["signals"]["completion"]["en"]
    lines = [f"{decl} COMPLETION_SIGNALS = [", f"{indent}// 中文显式完成"]
    lines += [f"{indent}{_q(s)}," for s in zh]
    lines += [f"{indent}// 英文显式完成"]
    lines += [f"{indent}{_q(s)}," for s in en]
    lines.append("];")
    return "\n".join(lines)


def render_signals_py(spec: Spec) -> str:
    items = spec.signals()
    lines = ["COMPLETION_SIGNALS = ["]
    # 尾逗号是必需的：列表字面量里相邻字符串字面量不做隐式拼接，漏了直接语法错误。
    lines += [f"    {_qp(s)}," for s in items]
    lines.append("]")
    return "\n".join(lines)


def render_debounce_js(spec: Spec) -> str:
    return f"const DEBOUNCE_MS = {spec['timing']['debounce_ms']};"


def render_debounce_py(spec: Spec) -> str:
    # 注释不写死「5 分钟」——数值在 spec 里，注释跟着数值走只会成为第二处真相源。
    return f"DEBOUNCE_MS = {spec['timing']['debounce_ms']}  # 毫秒"


def render_idle_js(spec: Spec) -> str:
    return f"const IDLE_FALLBACK_MS = {spec['timing']['idle_ms']};"


# —— 预算块：每种宿主语言一种形态 ——


def render_budgets_mjs(spec: Spec, host: str) -> str:
    h = spec.host(host)
    common = spec["append"]
    return "\n".join(
        [
            f"export const BRIEF_BUDGET = Number(process.env.AGENOTE_INJECTION_BRIEF_BUDGET || {h['brief']});",
            f"export const RECALL_BUDGET = Number(process.env.AGENOTE_INJECTION_RECALL_BUDGET || {h['recall']});",
            "export const CUMULATIVE_BUDGET = Number(",
            f"  process.env.AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET || {common['cumulative']},",
            ");",
            f"export const MIN_QUERY = Number(process.env.AGENOTE_INJECTION_MIN_QUERY || {common['min_query']});",
            f"export const QUERY_MAX_CHARS = {common['query_max_chars']};",
            f"const CLI_TIMEOUT_MS = {spec.timeout(host)};",
        ]
    )


def render_budgets_py(spec: Spec, host: str) -> str:
    h = spec.host(host)
    common = spec["append"]
    return "\n".join(
        [
            f"_BRIEF_BUDGET = int(os.environ.get(\"AGENOTE_INJECTION_BRIEF_BUDGET\") or {h['brief']})",
            f"_RECALL_BUDGET = int(os.environ.get(\"AGENOTE_INJECTION_RECALL_BUDGET\") or {h['recall']})",
            "_CUMULATIVE_BUDGET = int(",
            f"    os.environ.get(\"AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET\") or {common['cumulative']}",
            ")",
            f"_MIN_QUERY = int(os.environ.get(\"AGENOTE_INJECTION_MIN_QUERY\") or {common['min_query']})",
            f"_QUERY_MAX_CHARS = {common['query_max_chars']}",
        ]
    )


def render_budgets_bash(spec: Spec, host: str) -> str:
    """注入器 lib.sh / codex 覆盖段。codex 只需覆盖 brief/recall 两项。"""
    h = spec.host(host)
    out = [
        f': "${{AGENOTE_INJECTION_BRIEF_BUDGET:={h["brief"]}}}"',
        f': "${{AGENOTE_INJECTION_RECALL_BUDGET:={h["recall"]}}}"',
    ]
    return "\n".join(out)


def render_brief_only_bash(spec: Spec, host: str) -> str:
    return f': "${{AGENOTE_INJECTION_BRIEF_BUDGET:={spec.budget(host, "brief")}}}"'


def render_brief_only_ts(spec: Spec, host: str) -> str:
    return (
        "const BRIEF_BUDGET = Number("
        f"process.env.AGENOTE_INJECTION_BRIEF_BUDGET || {spec.budget(host, 'brief')});"
    )


def render_pi_brief(spec: Spec) -> str:
    return (
        f"const DEFAULT_INJECT_CONFIG: InjectConfig = "
        f"{{ injectEnabled: true, budget: {spec.budget('pi', 'brief')} }};"
    )


def render_pi_timeout(spec: Spec) -> str:
    return f"const BRIEFING_TIMEOUT_MS = {spec.timeout('pi')};"


# ══════════════════════════════════════════════════════════════
# 目标表
# ══════════════════════════════════════════════════════════════

# (相对路径, 行注释前缀, [(生成块 key, 渲染器)])
Block = tuple[str, Callable[[Spec], str]]

TARGETS: list[tuple[str, str, list[Block]]] = [
    # —— pi（omp 扩展，重写型）——
    (
        "packages/agenote-pi/index.ts",
        "//",
        [
            ("signals.completion", lambda s: render_signals_js(s)),
            ("timing.debounce", render_debounce_js),
            ("timing.idle", render_idle_js),
            ("hosts.pi.brief", render_pi_brief),
            ("hosts.pi.timeout", render_pi_timeout),
        ],
    ),
    # —— dsh（cordis bundle）——
    (
        "packages/dsh-agenote/hooks.js",
        "//",
        [
            ("signals.completion", lambda s: render_signals_js(s, decl="export const")),
            ("timing.debounce", render_debounce_js),
            ("timing.idle", render_idle_js),
        ],
    ),
    # —— zcode（ZCode 插件）——
    (
        "packages/agenote-zcode/hooks/prompt-submit.mjs",
        "//",
        [
            ("signals.completion", lambda s: render_signals_js(s)),
            ("timing.debounce", render_debounce_js),
        ],
    ),
    (
        "packages/agenote-zcode/hooks/lib.mjs",
        "//",
        [("hosts.zcode.budgets", lambda s: render_budgets_mjs(s, "zcode"))],
    ),
    # —— hermes（Python 插件）——
    (
        "packages/agenote-hermes/__init__.py",
        "#",
        [
            ("signals.completion", render_signals_py),
            ("timing.debounce", render_debounce_py),
            ("hosts.hermes.budgets", lambda s: render_budgets_py(s, "hermes")),
        ],
    ),
    # —— bash 注入器（claude 走 lib.sh，codex 各自覆盖）——
    (
        "packages/agenote/injectors/lib.sh",
        "#",
        [
            ("hosts.claude.budgets", lambda s: render_budgets_bash(s, "claude")),
            ("append.common.bash", lambda s: render_append_common_bash(s)),
        ],
    ),
    (
        "packages/agenote/injectors/codex/session-start.sh",
        "#",
        [("hosts.codex.brief", lambda s: render_brief_only_bash(s, "codex"))],
    ),
    (
        "packages/agenote/injectors/codex/user-prompt-submit.sh",
        "#",
        [("hosts.codex.recall", lambda s: render_recall_only_bash(s, "codex"))],
    ),
    # —— opencode（重写型模板）——
    (
        "packages/agenote/injectors/opencode/agenote-context.ts",
        "//",
        [("hosts.opencode.brief", lambda s: render_brief_only_ts(s, "opencode"))],
    ),
]


def render_append_common_bash(spec: Spec) -> str:
    common = spec["append"]
    return "\n".join(
        [
            f': "${{AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET:={common["cumulative"]}}}"',
            f': "${{AGENOTE_INJECTION_MIN_QUERY:={common["min_query"]}}}"',
            f"_AGENOTE_QUERY_MAX_CHARS={common['query_max_chars']}",
        ]
    )


def render_recall_only_bash(spec: Spec, host: str) -> str:
    return f': "${{AGENOTE_INJECTION_RECALL_BUDGET:={spec.budget(host, "recall")}}}"'


# ══════════════════════════════════════════════════════════════
# 标记块读写
# ══════════════════════════════════════════════════════════════


def replace_block(text: str, prefix: str, key: str, body: str) -> str:
    begin = f"{prefix} {BEGIN_FMT.format(key=key)}"
    end = f"{prefix} {END_FMT.format(key=key)}"
    lines = text.splitlines(keepends=True)
    try:
        i = next(n for n, ln in enumerate(lines) if ln.strip() == begin)
        j = next(n for n, ln in enumerate(lines) if ln.strip() == end)
    except StopIteration:
        raise SystemExit(
            f"缺少生成块标记：找不到 {begin!r}（或对应的 END）。\n"
            f"请在该常量定义外侧手工加上 BEGIN/END 两行标记后重跑。"
        )
    if j < i:
        raise SystemExit(f"{key}: END 出现在 BEGIN 之前")
    new = [f"{begin}\n", f"{prefix} {NOTE}\n", body.rstrip("\n") + "\n", f"{end}\n"]
    return "".join(lines[:i] + new + lines[j + 1 :])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="只校验不写；有漂移则退出码 1")
    args = ap.parse_args()

    spec = load_spec()
    drifted: list[str] = []

    for rel, prefix, blocks in TARGETS:
        path = REPO_ROOT / rel
        if not path.exists():
            raise SystemExit(f"目标文件不存在：{rel}")
        original = text = path.read_text()
        for key, render in blocks:
            text = replace_block(text, prefix, key, render(spec))
        if text == original:
            continue
        if args.check:
            drifted.append(rel)
        else:
            path.write_text(text)
            print(f"写入 {rel}")

    if args.check:
        if drifted:
            print("生成块与 spec/injection.toml 不一致：", file=sys.stderr)
            for rel in drifted:
                print(f"  - {rel}", file=sys.stderr)
            print("\n修法：python3 tools/codegen/generate.py", file=sys.stderr)
            return 1
        print(f"✓ {len(TARGETS)} 个文件的生成块均与 spec 一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

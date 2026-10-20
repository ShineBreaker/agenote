# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""Hermes plugin for agenote — 知识库集成 + 记忆注入（设计 C4 W2 成品级）。

本插件是 agenote 生态为 hermes 的唯一接入点，承担两块职责：

注入（agenote → 会话，设计 C4）——策略在 CLI，本插件只做协议适配：
- register_system_prompt_section（after_memory 锚点，框架默认位）：会话级简报
  （3800 字符）。render 时调 `agenote inject --event session`，CLI 侧完成状态
  重置与指纹缓存重放；compact 后重建提示词会重新 render，缓存命中直接返回
  快照（不返回空，否则重建后的提示词会丢简报）。
- pre_llm_call（每回合 recall，2000 字符）：调 `agenote inject --event prompt`，
  三件套（指纹+query 去重 / 短 prompt 门槛 / 单会话累计触顶）由 CLI 统一判定
  （ADR 0005：此前四份语言副本，现在只有 `inject.py` 一份）。
- 与既有的任务完成信号检测共存于同一 pre_llm_call：召回正文与评估提示
  各自独立触发，同回合双双命中时以空行拼接成一个 context 注入。
- 状态文件 ~/.cache/agenote/injectors/hermes-<session_id>.json（CLI 统一布局；
  session_id 缺失退化为 hermes.json 单文件；整目录可清理无副作用）。
- `agenote inject` 输出非 inject 态（disabled/empty/duplicate/短 prompt）→
  本回合不注，宿主零感知。

集成（会话 → agenote，既有能力，保持不变）：
- pre_llm_call 钩子：检测任务完成信号，注入 agenote-review 评估提示
- /agenote-summarize：手动触发经验总结
- /agenote-health：显示 KB 健康度
- /agenote-curate：注入策展任务提示（agent 按 agenote-curator skill 主导执行，
  与 pi f8ccec3 同款——CLI 无 `curate` 子命令，流程编排由 agent 完成）

信号清单与写入流程由 agenote-{base,curator,review} skill 提供，本插件只做
事件触发 + 注入通道 + 命令快捷入口，避免与 skill 重复维护。

注入语义开关的真相源在 agenote SCHEMA [injection]/[injection.hosts]（一键全关
AGENOTE_INJECTION_ENABLED=false）；本侧旋钮只有每宿主预算两个 env。
真机验收：/context 看 agenote 节 + sidecar 审计（详见 agenote 仓库
injectors/hermes/README.md）。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

# ── 完成信号。行为规范的散文版在 agenote-review skill 的 references/triggers.md。
# ─── BEGIN GENERATED: signals.completion ───
# 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
COMPLETION_SIGNALS = [
    "可以用了",
    "一切正常",
    "都没问题",
    "都正常",
    "搞定",
    "完成",
    "做完了",
    "测试通过",
    "就这些",
    "先这样",
    "暂时够了",
    "就这样",
    "没了",
    "done.",
    "done!",
    "looks good",
    "ship it",
]
# ─── END GENERATED: signals.completion ───

HOOK_MARKER = "<agenote-hook>"
# ─── BEGIN GENERATED: timing.debounce ───
# 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
DEBOUNCE_MS = 300000  # 毫秒
# ─── END GENERATED: timing.debounce ───

_last_trigger_ms: float = 0.0

# ── 注入参数（设计 C4 每宿主预算表；env 覆盖，语义开关在 agenote SCHEMA）──
# 只保留每宿主单次预算；指纹/门槛/累计预算的真相源是 CLI（`agenote inject`），
# 本插件不再持有同名镜像值。
# ─── BEGIN GENERATED: hosts.hermes.budgets ───
# 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
_BRIEF_BUDGET = int(os.environ.get("AGENOTE_INJECTION_BRIEF_BUDGET") or 3800)
_RECALL_BUDGET = int(os.environ.get("AGENOTE_INJECTION_RECALL_BUDGET") or 2000)
# ─── END GENERATED: hosts.hermes.budgets ───
_SKIP_PLATFORMS = ("subagent", "cron")

# agenote CLI 解析：Guix 路径优先，fallback 到 PATH
_AGENOTE_BIN = "agenote"


def _resolve_agenote_bin() -> str:
    for cand in [
        str(Path.home() / ".guix-home/profile/bin/agenote"),
        str(Path.home() / ".local/bin/agenote"),
    ]:
        if Path(cand).exists() and os.access(cand, os.X_OK):
            return cand
    return _AGENOTE_BIN


_RESOLVED_BIN: str | None = None


def _bin() -> str:
    global _RESOLVED_BIN
    if _RESOLVED_BIN is None:
        _RESOLVED_BIN = _resolve_agenote_bin()
    return _RESOLVED_BIN


def _run_agenote(*args: str, timeout: int = 30) -> str:
    """运行 agenote CLI，返回 stdout；失败返回错误描述。"""
    try:
        result = subprocess.run(
            [_bin(), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "AGENOTE_AGENT": "hermes"},
        )
        out = (result.stdout or "").strip()
        err = (result.stderr or "").strip()
        if result.returncode != 0:
            detail = err or out or f"exit {result.returncode}"
            return f"(agenote {' '.join(args)} 失败: {detail})"
        return out or "(无输出)"
    except subprocess.TimeoutExpired:
        return f"(agenote {' '.join(args)} 超时 {timeout}s)"
    except FileNotFoundError:
        return "(agenote 命令未找到，请先安装 agenote CLI)"
    except Exception as exc:
        return f"(agenote 执行异常: {exc})"


# ═══════════════════════════════════════════════════════════════════════════════
# 注入：唯一 CLI 调用（`agenote inject` 内完成指纹 / 三件套 / 状态）
# ═══════════════════════════════════════════════════════════════════════════════


def _inject(session_id: str, event: str, prompt: str = "", cwd: str = "") -> str:
    """调 `agenote inject`；CLI 缺失/失败/非 inject 态一律空串。

    注入策略是 CLI 的单一实现（ADR 0005）：本插件只把宿主事件转成参数，
    不再自己读写状态文件、算指纹或拼 query。
    """
    args = [
        "inject", "--host", "hermes", "--session-id", session_id,
        "--event", event, "--format", "json",
    ]
    if event == "prompt":
        args += ["--prompt", prompt, "--budget", str(_RECALL_BUDGET)]
    else:
        args += ["--budget", str(_BRIEF_BUDGET)]
    try:
        result = subprocess.run(
            [_bin(), *args],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd or None,
            env={**os.environ, "AGENOTE_AGENT": "hermes"},
        )
        data = json.loads(result.stdout or "")
    except Exception:
        return ""
    if isinstance(data, dict) and data.get("action") == "inject":
        return data.get("content") or ""
    return ""


def _render_brief(session_info: dict[str, str]) -> str:
    """会话级简报节：render 时调 session 事件，CLI 完成重置 + 指纹缓存重放。

    hermes 的 render 只发生在会话建立与 compact 重建，频率低；缓存命中时
    CLI 直接返回快照（不再生成内容），compact 重建不会丢简报。
    """
    if session_info.get("platform") in _SKIP_PLATFORMS:
        return ""
    return _inject(
        session_info.get("session_id", ""),
        "session",
        cwd=session_info.get("cwd", ""),
    )


def _recall_context(session_id: str, user_text: str, cwd: str = "") -> str:
    """每回合 recall：三件套判定在 CLI 内；不注返回空串。"""
    return _inject(session_id, "prompt", user_text or "", cwd=cwd)


# ── 完成信号（既有逻辑，保持不变）─────────────────────────────────


def build_review_prompt(reason: str) -> str:
    return "\n".join(
        [
            f"<agenote-hook>{reason}，请按 agenote-review skill 流程评估本次对话：",
            "（注意：这有可能是误报，如果当前任务没有完成的话，请忽略）",
            "1. 是否有可记录的经验信号（bug/踩坑/更优方案/用户纠正/项目决策）？",
            "2. 如有 → 通过 bash 调用 agenote CLI 写入（agenote add / agenote memory --add 等）",
            "3. 本轮用到的资料留痕：已有卡片 agenote touch，联网新知识 agenote add（type=note）",
            "4. 如无 → 明确回复'本次无可记录经验'</agenote-hook>",
        ]
    )


def _should_skip(user_text: str) -> bool:
    """子注入或空文本跳过。"""
    if not user_text:
        return True
    if HOOK_MARKER in user_text:
        return True
    return False


def _has_completion_signal(text_lower: str) -> bool:
    for sig in COMPLETION_SIGNALS:
        if sig.lower() in text_lower:
            return True
    return False


# ── pre_llm_call hook（召回注入 + 完成信号检测合一）───────────────


def _pre_llm_call(
    session_id: str = "",
    user_message: str = "",
    platform: str = "",
    cwd: str = "",
    **_: Any,
) -> dict[str, str] | None:
    """召回记忆注入 + 完成信号检测。两者独立触发，命中合并为一个 context。"""
    global _last_trigger_ms

    # 子 agent / cron 会话不触发（避免污染 handoff / 定时任务噪音；设计 §5-9）
    if platform in _SKIP_PLATFORMS:
        return None

    text = user_message if isinstance(user_message, str) else str(user_message or "")
    if _should_skip(text):
        return None

    parts: list[str] = []

    recall = _recall_context(session_id, text, cwd)
    if recall:
        parts.append(recall)

    lower = text.lower()
    if _has_completion_signal(lower):
        now_ms = time.time() * 1000
        if now_ms - _last_trigger_ms >= DEBOUNCE_MS:
            _last_trigger_ms = now_ms
            parts.append(build_review_prompt("检测到任务完成信号"))

    if not parts:
        return None
    return {"context": "\n\n".join(parts)}


# ── slash command handlers ───────────────────────────────────────

def _make_summarize_handler(ctx: Any):
    def handler(raw_args: str) -> str:
        prompt = build_review_prompt("用户手动触发经验总结")
        injected = False
        try:
            injected = bool(ctx.inject_message(prompt))
        except Exception:
            injected = False
        if injected:
            return "已注入 agenote-review 评估提示到下一轮对话。"
        return prompt

    return handler


def _handle_health(raw_args: str) -> str:
    return _run_agenote("health", timeout=30)


def _build_curate_prompt(reason: str) -> str:
    """策展任务提示（对齐 pi f8ccec3：CLI 无 `curate` 子命令——原实现调
    `agenote curate` 必然 invalid choice 返回失败；策展流程由 agent 按
    agenote-curator skill 主导，编排原子命令并逐项审查）。"""
    return "\n".join(
        [
            f"<agenote-hook>{reason}，请按 agenote-curator skill 流程对知识库执行策展：",
            "（CLI 只提供检测报告与原子命令，流程编排与去留决策由你执行；先看候选清单，核实后再写盘）",
            "1. 诊断：agenote health --quality --duplicates / stats / gaps",
            "2. 状态重整：list --unused-days 找降级候选、archive --stale 找归档候选，逐项审查后显式 update/archive",
            "3. 去重合并 / type 聚拢 / 矛盾调和 / memory 维护（规则见 skill）",
            "4. 可选：reconcile + dream 综合（值得沉淀的候选用 agenote add 写入）",
            "5. 收尾：reindex + lint --fix + agenote commit（策展产物），输出策展报告</agenote-hook>",
        ]
    )


def _make_curate_handler(ctx: Any):
    def handler(raw_args: str) -> str:
        prompt = _build_curate_prompt("用户手动触发策展")
        injected = False
        try:
            injected = bool(ctx.inject_message(prompt))
        except Exception:
            injected = False
        if injected:
            return "已注入 agenote-curator 策展任务提示到下一轮对话。"
        return prompt

    return handler


def register(ctx: Any) -> None:
    # 会话级简报节（after_memory 锚点为框架默认位；单节 4000/总量 8000 由框架
    # 硬限，本插件节预算 3800 留余量）。global-context 插件先例：section 注册
    # 无需在 plugin.yaml 声明。
    ctx.register_system_prompt_section("agenote-context-brief", _render_brief)
    ctx.register_hook("pre_llm_call", _pre_llm_call)

    ctx.register_command(
        "agenote-summarize",
        _make_summarize_handler(ctx),
        description="在当前会话触发 agenote 经验总结 + 留痕",
        args_hint="",
    )
    ctx.register_command(
        "agenote-health",
        _handle_health,
        description="显示 agenote 健康度报告",
        args_hint="",
    )
    ctx.register_command(
        "agenote-curate",
        _make_curate_handler(ctx),
        description="在当前会话触发 KB 策展（agent 按 agenote-curator skill 执行）",
        args_hint="",
    )

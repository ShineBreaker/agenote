# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

# agenote 注入器共享库（bash）——claude/codex 注入器 source 本库。
#
# 职责边界：本库只负责「协议适配 + 调 CLI」，注入策略（指纹缓存、recall 门槛、
# 单会话累计预算、状态文件、会话重置）**全部在 `agenote inject` 内**。三件套
# 曾是四份语言各写一遍的复制债（ADR 0005），现在只有 CLI 一份实现；改语义改
# `packages/agenote/src/agenote/inject.py` 或 CLI SCHEMA，不再需要跨宿主同步。
#
# 依赖：bash、coreutils、python3（agenote 本身是 Python，python3 必在）。
# 返回码：CLI 缺失/失败/非 inject 态一律空正文（注入器不注任何东西）。

# ─── 每宿主单次预算（spec/injection.toml [hosts.claude]）────────────────────
# ─── BEGIN GENERATED: hosts.claude.budgets ───
# 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
: "${AGENOTE_INJECTION_BRIEF_BUDGET:=8000}"
: "${AGENOTE_INJECTION_RECALL_BUDGET:=4000}"
# ─── END GENERATED: hosts.claude.budgets ───

# ─── hook stdin JSON 字段提取（stdin 原文一次性读入由宿主脚本负责）────────────
_agenote_stdin_field() {  # $1=stdin原文  $2=字段名 → stdout 值（缺失/非字符串输出空）
  printf '%s' "$1" | python3 -c '
import json, sys
try:
    v = json.load(sys.stdin).get(sys.argv[1], "")
    print(v if isinstance(v, str) else "", end="")
except Exception:
    pass
' "$2" 2>/dev/null || true
}

# ─── 唯一 CLI 调用点：把事件交给 `agenote inject`，取回要注入的正文 ──────────
# stdout = 注入正文（空 = 不注）。cwd 在子壳内切换，不污染调用方。
_agenote_inject() {  # $1=host  $2=event(session|prompt)  $3=session_id  $4=prompt  $5=cwd
  local host="$1" event="$2" sid="$3" prompt="${4:-}" cwd="${5:-}"
  command -v agenote >/dev/null 2>&1 || return 0

  local args=(inject --host "$host" --session-id "$sid" --event "$event" --format json)
  if [[ "$event" == "prompt" ]]; then
    args+=(--prompt "$prompt" --budget "$AGENOTE_INJECTION_RECALL_BUDGET")
  else
    args+=(--budget "$AGENOTE_INJECTION_BRIEF_BUDGET")
  fi

  # CLI 必须限时：hook 里挂起的子进程会拖住宿主整个钩子（F10 故障静默——
  # 超时也按"本轮不注入"降级）。数值取 hook 超时余量内的保守值。
  local out
  out="$( cd "$cwd" 2>/dev/null || true
    AGENOTE_AGENT="$host" timeout 10 agenote "${args[@]}" 2>/dev/null
  )" || return 0

  printf '%s' "$out" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(data, dict) and data.get("action") == "inject":
    sys.stdout.write(data.get("content") or "")
'
}

# ═══ 注入器入口（宿主脚本只调这两个；stdout = 注入正文，空 = 不注任何东西）════

# 每轮通道（UserPromptSubmit）。
_agenote_recall_turn() {  # $1=host  $2=cwd  $3=session_id  $4=prompt
  _agenote_inject "$1" prompt "$3" "${4:-}" "$2"
}

# 会话通道（SessionStart/compact）：CLI 侧同时完成状态重置与简报缓存重放。
_agenote_session_turn() {  # $1=host  $2=cwd  $3=session_id
  _agenote_inject "$1" session "$3" "" "$2"
}

# ─── 协议封包（claude / codex 同款：hookSpecificOutput.additionalContext）────
# 空正文由调用方负责不调本函数（= 省略字段，宿主零注入）。
_agenote_emit() {  # $1=hookEventName  $2=正文
  printf '%s' "$2" | python3 -c '
import json, sys
print(json.dumps(
    {"hookSpecificOutput": {"hookEventName": sys.argv[1],
                            "additionalContext": sys.stdin.read()}},
    ensure_ascii=False))
' "$1"
}

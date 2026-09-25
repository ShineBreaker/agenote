#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

# agenote → codex UserPromptSubmit 注入器（recipe 级模板，设计 C4 W2）
#
# 每轮 recall 通道：query = prompt 前 200 字符 + cwd basename 伪词，追加型
# 三件套（指纹未变不重注 / query 门槛与分数下限滤空跳过 / 单会话累计 24000
# 停 recall）全在 ../lib.sh。预算 2800 字符（CJK 折算保守值，防 spill）。
# 注意：codex 的 hook additionalContext 进 rollout 持久化，注入体量影响存储
# ——累计预算触顶后本通道自动静默。
#
# [hooks] config.toml 挂接示例与首次信任确认说明见同目录 README.md。
#
# 手动 dry-run：
#   printf '%s\n' '{"hook_event_name":"UserPromptSubmit","session_id":"s1",
#     "cwd":"/path/to/project","prompt":"recall 的预算怎么折算"}' \
#     | bash user-prompt-submit.sh

set -uo pipefail

: "${AGENOTE_INJECTION_BRIEF_BUDGET:=2800}"   # codex CJK 折算保守值（字符）
: "${AGENOTE_INJECTION_RECALL_BUDGET:=2800}"

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$LIB_DIR/../lib.sh"

INPUT="$(cat 2>/dev/null || true)"
SESSION_ID="$(_agenote_stdin_field "$INPUT" session_id)"
CWD="$(_agenote_stdin_field "$INPUT" cwd)"
CWD="${CWD:-$PWD}"
PROMPT="$(_agenote_stdin_field "$INPUT" prompt)"

CONTENT="$(_agenote_recall_turn codex "$CWD" "$SESSION_ID" "$PROMPT")" || exit 0
[[ -n "$CONTENT" ]] && _agenote_emit UserPromptSubmit "$CONTENT"
exit 0

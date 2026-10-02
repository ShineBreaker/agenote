#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

# agenote → codex SessionStart 注入器（recipe 级模板，设计 C4 W2）
#
# codex 内置 Claude 兼容 hooks 引擎（12 事件，SessionStart matcher:
# startup/resume/clear/compact/fork），additionalContext flatten 后进 developer
# 消息。预算 2800 字符 = CJK tokens 折算保守值（2500 tokens 上限，CJK 约
# 1–1.5 字符/token；调高需同步 per-hook additional_context_limit，否则超限
# 自动 spill 到临时文件并留路径回引）。
#
# 状态：recipe 级（宿主自带记忆默认已关、无紧迫性，不设验收门）。
# [hooks] config.toml 挂接示例与首次信任确认说明见同目录 README.md。
#
# 手动 dry-run（同 claude 协议形状）：
#   printf '%s\n' '{"hook_event_name":"SessionStart","session_id":"s1",
#     "cwd":"/path/to/project","source":"startup"}' | bash session-start.sh

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

CONTENT="$(_agenote_session_turn codex "$CWD" "$SESSION_ID")" || exit 0
[[ -n "$CONTENT" ]] && _agenote_emit SessionStart "$CONTENT"
exit 0

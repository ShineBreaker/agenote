#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

# agenote → claude-code SessionStart 注入器（成品级，设计 C4 W2）
#
# 挂点：~/.claude/settings.json hooks.SessionStart（startup/resume/clear/compact
# 四源全覆盖，compact 自动重跑即免费重注入）。单次预算：简报 8000 字符。
# 三件套/缓存逻辑在 ../lib.sh：本脚本只做协议适配（stdin 字段提取 + 输出封包）。
#
# 协议依据：code.claude.com/docs/en/hooks（2026-09 核对）——stdin 含
# session_id/cwd/hook_event_name/source；输出 hookSpecificOutput.additionalContext；
# status 非 ok（disabled/empty）时正文为空 → 本脚本不输出任何 JSON（省略字段）。
#
# 手动 dry-run：
#   printf '%s\n' '{"hook_event_name":"SessionStart","session_id":"s1",
#     "cwd":"/path/to/project","source":"startup"}' | bash session-start.sh

set -uo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$LIB_DIR/../lib.sh"

INPUT="$(cat 2>/dev/null || true)"
SESSION_ID="$(_agenote_stdin_field "$INPUT" session_id)"
CWD="$(_agenote_stdin_field "$INPUT" cwd)"
CWD="${CWD:-${CLAUDE_PROJECT_DIR:-$PWD}}"

CONTENT="$(_agenote_session_turn claude "$CWD" "$SESSION_ID")" || exit 0
[[ -n "$CONTENT" ]] && _agenote_emit SessionStart "$CONTENT"
exit 0

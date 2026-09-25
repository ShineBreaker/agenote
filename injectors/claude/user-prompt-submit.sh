#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

# agenote → claude-code UserPromptSubmit 注入器（成品级，设计 C4 W2）
#
# 挂点：~/.claude/settings.json hooks.UserPromptSubmit（每轮触发）。
# 单次预算：recall 4000 字符（宿主上限 10000，设计 C4 预算表）；追加型三件套
# （指纹未变不重注 / 短 prompt 与分数下限滤空跳过 / 单会话累计 24000 停 recall）
# 全在 ../lib.sh。query = prompt 折叠空白取前 200 字符 + cwd basename 伪词。
#
# 协议依据：code.claude.com/docs/en/hooks（2026-09 核对）——stdin 含
# session_id/prompt/cwd；输出 hookSpecificOutput.additionalContext；正文为空
# 时不输出任何 JSON。
#
# 手动 dry-run：
#   printf '%s\n' '{"hook_event_name":"UserPromptSubmit","session_id":"s1",
#     "cwd":"/path/to/project","prompt":"注入架构的 recall 门槛怎么定的"}' \
#     | bash user-prompt-submit.sh

set -uo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib.sh
source "$LIB_DIR/../lib.sh"

INPUT="$(cat 2>/dev/null || true)"
SESSION_ID="$(_agenote_stdin_field "$INPUT" session_id)"
CWD="$(_agenote_stdin_field "$INPUT" cwd)"
CWD="${CWD:-${CLAUDE_PROJECT_DIR:-$PWD}}"
PROMPT="$(_agenote_stdin_field "$INPUT" prompt)"

CONTENT="$(_agenote_recall_turn claude "$CWD" "$SESSION_ID" "$PROMPT")" || exit 0
[[ -n "$CONTENT" ]] && _agenote_emit UserPromptSubmit "$CONTENT"
exit 0

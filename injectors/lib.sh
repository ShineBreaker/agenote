# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

# agenote 注入器共享库（bash）——claude/codex 注入器 source 本库。
# 设计规格：AGENOTE_INJECTION_DESIGN.md §3 C4（通用约定 + 追加型三件套 + 缓存）。
#
# 职责边界：本库只负责「策略 + CLI 调用」，协议封包（additionalContext 的
# JSON 形状）由各宿主注入器脚本完成（claude/codex 用 _agenote_emit）。
# zcode/hermes 的等价实现分别在各宿主插件内（hooks/lib.mjs / hermes 插件
# __init__.py），逻辑与本库同构；opencode（重写型）只共享指纹缓存语义。
# 改三件套语义时需跨仓库同步。
#
# 依赖：bash、coreutils(stat)、python3（agenote 本身是 Python，python3 必在）。
# jq 非必需（全部 JSON 解析走 python3）。
# 本库不设置 shell 选项——由宿主注入器脚本自行 `set -uo pipefail` 后再 source。
#
# 返回码约定（_agenote_recall_turn / _agenote_session_turn）：
#   rc 1 = agenote CLI 缺失；rc 0 = 正常（stdout 为注入正文，空 = 不注）。

# ─── 可调参数（env 覆盖；语义开关的真相源在 agenote SCHEMA [injection] 节）──
: "${AGENOTE_INJECTION_BRIEF_BUDGET:=8000}"                # session 简报单次预算（字符）
: "${AGENOTE_INJECTION_RECALL_BUDGET:=4000}"               # recall 单次预算（字符）
: "${AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET:=24000}"  # 单会话累计预算（与 SCHEMA 同名 env 同口径）
: "${AGENOTE_INJECTION_MIN_QUERY:=6}"                      # recall 有效 query 最短字符（镜像 recall_min_query）
_AGENOTE_QUERY_MAX_CHARS=200                               # recall query 取 prompt 前 N 字符（设计 C4 规定值）

# ─── 状态目录：~/.cache/agenote/injectors/（整目录可清理，无副作用）──────────
_agenote_state_dir() {
  printf '%s' "${AGENOTE_INJECTOR_STATE_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/agenote/injectors}"
}

# 状态文件路径：<host>-<session_id>.json；session_id 取不到则退化为按 host 单文件
_agenote_state_path() {  # $1=host  $2=session_id
  local host="$1" sid="${2//\//_}"
  if [[ -n "$sid" ]]; then
    printf '%s/%s-%s.json' "$(_agenote_state_dir)" "$host" "$sid"
  else
    printf '%s/%s.json' "$(_agenote_state_dir)" "$host"
  fi
}

# ─── KB agent 域根解析：与 CLI 同口径（env KB_ROOT > config.toml > 默认）──────
# context 命令读 agenote 域（KB_ROOT/<agenote_dir>/MEMORY.org，双域模型下
# 注入语料只取 agent 域），故指纹也定位到域根。config.toml 只取 paths 两键，
# 解析失败回退默认；镜像实现，CLI 仍以其自身配置为最终裁决。
_agenote_kb_root() {
  python3 - <<'PYEOF' 2>/dev/null || printf '%s' "$HOME/Documents/Org/agenote"
import os
from pathlib import Path
try:
    kb = os.environ.get("KB_ROOT") or ""
    agenote_dir = "agenote"
    if not kb:
        import tomllib
        cfg = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "agenote" / "config.toml"
        with open(cfg, "rb") as f:
            paths = tomllib.load(f).get("paths", {})
        kb = paths.get("kb_root", "")
        agenote_dir = paths.get("agenote_dir") or "agenote"
    if not kb:
        raise SystemExit(1)
    print(Path(kb).expanduser() / agenote_dir)
except Exception:
    raise SystemExit(1)
PYEOF
}

# KB 指纹：agenote 域 MEMORY.org 与 memories/ 的 mtime+size（设计 D8；
# 秒级 mtime 以 size 兜底）。MEMORY.org 不存在 → rc 1（此时 CLI 也只会输出
# empty，提前跳过省一次 spawn）。
_agenote_kb_fingerprint() {
  local kb fp mem
  kb="$(_agenote_kb_root)"
  [[ -n "$kb" && -f "$kb/MEMORY.org" ]] || return 1
  fp="$(stat -c '%Y:%s' "$kb/MEMORY.org" 2>/dev/null)" || return 1
  if [[ -d "$kb/memories" ]]; then
    mem="$(stat -c '%Y:%s' "$kb/memories" 2>/dev/null)" || mem="-"
  else
    mem="-"
  fi
  printf '%s|%s' "$fp" "$mem"
}

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

# ─── 调 CLI（唯一允许的 agenote 调用形态；缺失/失败一律空串，绝不炸宿主会话）──
_agenote_context_cli() {  # $1=host  $2=mode  $3=budget  $4=query(可空)
  local host="$1" mode="$2" budget="$3" query="${4:-}"
  local args=(context --mode "$mode" --budget "$budget" --host "$host" --format json)
  [[ -n "$query" ]] && args+=(--query "$query")
  AGENOTE_AGENT="$host" agenote "${args[@]}" 2>/dev/null || true
}

# ─── 每轮通道决策（query 门槛 + 三件套①③）：输出 `GO <累计值>` + query 两行。
# rc 0 = GO；rc 2 = query 门槛不过；rc 3 = 指纹+query 未变（含上次滤空）；rc 4 = 累计触顶。
# 触顶时顺手记 recall_key，防同 query 反复 spawn。状态文件缺失/损坏按无状态处理。
_agenote_recall_decide() {  # $1=state  $2=fp  $3=prompt  $4=cwd
  python3 - "$1" "$2" "$3" "$4" \
    "$AGENOTE_INJECTION_MIN_QUERY" "$_AGENOTE_QUERY_MAX_CHARS" \
    "$AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET" <<'PYEOF'
import json, os, sys
state_path, fp, prompt, cwd = sys.argv[1:5]
min_q, max_c, budget = int(sys.argv[5] or 6), int(sys.argv[6] or 200), int(sys.argv[7] or 24000)

# 三件套②前置：prompt 折叠空白取前 200 字符；门槛按 prompt 部分长度判——
# cwd basename 伪词不计入，否则「继续」+ 项目名也能过门槛，违背短 prompt 跳过语义
q_text = " ".join((prompt or "").split())[:max_c]
if len(q_text.strip()) < min_q:
    sys.exit(2)
pseudo = os.path.basename((cwd or "").rstrip("/"))
q = (q_text + " " + pseudo).strip() if pseudo and pseudo != "." else q_text
key = fp + "::" + q

try:
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        state = {}
except Exception:
    state = {}

if state.get("recall_key") == key:
    sys.exit(3)  # 三件套①：指纹+query 均未变 → 不重注
if int(state.get("cumulative") or 0) >= budget:
    state["recall_key"] = key  # 三件套③：触顶停 recall，记 key 防重复 spawn
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        os.replace(tmp, state_path)
    except OSError:
        pass
    sys.exit(4)

print("GO", state.get("cumulative") or 0)
print(q, end="")
PYEOF
}

# ─── 解析 CLI 输出 + 提交状态（累计记账）→ stdout 输出注入正文或空 ───────────
# status 非 ok / content 空 → 只记 recall_key 不注（「分数下限滤空」由此落账，
# 下轮同 query 零 spawn）。CLI 输出经 argv 传入（heredoc 会占住 python3 的
# stdin，数据不能走 stdin）。
_agenote_recall_commit() {  # $1=state  $2=fp  $3=query  $4=累计前值  $5=CLI json
  python3 - "$1" "$2" "$3" "$4" "$5" <<'PYEOF'
import json, os, sys
state_path, fp, q, prev = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4] or 0)
content = ""
try:
    data = json.loads(sys.argv[5])
    if isinstance(data, dict) and data.get("status") == "ok":
        content = data.get("content") or ""
except Exception:
    content = ""

try:
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        state = {}
except Exception:
    state = {}
state["recall_key"] = fp + "::" + q
if content:
    state["cumulative"] = prev + len(content)

try:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, state_path)
except OSError:
    pass  # 状态写失败宁可下轮重复注入，也不中断本轮
sys.stdout.write(content)
PYEOF
}

# ─── 会话通道准备：SessionStart/compact 重置三件套状态 + 指纹缓存判定 ─────────
# stdout 首行 `CACHED`（后续行为缓存正文，零 spawn）或 `FRESH`（需调 CLI）。
# 重置语义：清 recall 记账（累计清零 + recall 解禁），brief 缓存保留按指纹失效。
_agenote_session_pre() {  # $1=state  $2=fp
  python3 - "$1" "$2" <<'PYEOF'
import json, os, sys
state_path, fp = sys.argv[1], sys.argv[2]
try:
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        state = {}
except Exception:
    state = {}

state.pop("recall_key", None)  # SessionStart/compact 重置：recall 解禁
state["cumulative"] = 0

def _save(dict_val):
    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        tmp = state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict_val, f, ensure_ascii=False)
        os.replace(tmp, state_path)
    except OSError:
        pass

if state.get("brief_key") == fp and state.get("brief_content"):
    sys.stdout.write("CACHED\n" + state["brief_content"])
    _save(state)
    sys.exit(0)

state.pop("brief_key", None)
state.pop("brief_content", None)
_save(state)
print("FRESH")
PYEOF
}

# 会话简报落库：ok 且非空才缓存（disabled/empty 不缓存——开关重开后应立即生效，
# 不必等 KB 指纹变化）。stdout 输出正文（可空）。CLI json 经 argv 传入（同上）。
_agenote_session_store() {  # $1=state  $2=fp  $3=CLI json
  python3 - "$1" "$2" "$3" <<'PYEOF'
import json, os, sys
state_path, fp = sys.argv[1], sys.argv[2]
content = ""
try:
    data = json.loads(sys.argv[3])
    if isinstance(data, dict) and data.get("status") == "ok":
        content = data.get("content") or ""
except Exception:
    content = ""

try:
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    if not isinstance(state, dict):
        state = {}
except Exception:
    state = {}
if content:
    state["brief_key"] = fp
    state["brief_content"] = content
try:
    os.makedirs(os.path.dirname(state_path), exist_ok=True)
    tmp = state_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, state_path)
except OSError:
    pass
sys.stdout.write(content)
PYEOF
}

# ═══ 注入器入口（宿主脚本只调这两个；stdout = 注入正文，空 = 不注任何东西）════

# 每轮通道（UserPromptSubmit）：追加型三件套全量。
_agenote_recall_turn() {  # $1=host  $2=cwd  $3=session_id  $4=prompt
  local host="$1" cwd="$2" sid="$3" prompt="$4"
  command -v agenote >/dev/null 2>&1 || return 1

  local state fp decision rc prev q
  state="$(_agenote_state_path "$host" "$sid")"
  fp="$(_agenote_kb_fingerprint)" || return 0

  decision="$(_agenote_recall_decide "$state" "$fp" "$prompt" "$cwd")"
  rc=$?
  (( rc == 0 )) || return 0        # rc 2/3/4：门槛/指纹/预算 → 静默不注
  prev="${decision%%$'\n'*}"; prev="${prev#GO }"
  q="${decision#*$'\n'}"
  [[ -n "$q" ]] || return 0

  local cli_out
  cli_out="$(_agenote_context_cli "$host" recall "$AGENOTE_INJECTION_RECALL_BUDGET" "$q")"
  _agenote_recall_commit "$state" "$fp" "$q" "$prev" "$cli_out"
}

# 会话通道（SessionStart）：重置状态 + 指纹缓存重放。
_agenote_session_turn() {  # $1=host  $2=cwd  $3=session_id
  local host="$1" cwd="$2" sid="$3"
  command -v agenote >/dev/null 2>&1 || return 1

  local state fp pre
  state="$(_agenote_state_path "$host" "$sid")"
  fp="$(_agenote_kb_fingerprint)" || return 0

  pre="$(_agenote_session_pre "$state" "$fp")"
  if [[ "${pre%%$'\n'*}" == "CACHED" ]]; then
    printf '%s' "${pre#*$'\n'}"
    return 0
  fi

  # cd 到会话 cwd：CLI 侧 P 类项目匹配用 Path.cwd()（子壳内 cd，不污染调用方）
  local cli_out
  cli_out="$( cd "$cwd" 2>/dev/null || true
    _agenote_context_cli "$host" session "$AGENOTE_INJECTION_BRIEF_BUDGET" ""
  )"
  _agenote_session_store "$state" "$fp" "$cli_out"
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

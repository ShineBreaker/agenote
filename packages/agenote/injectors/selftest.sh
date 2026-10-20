#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT

# injectors/selftest.sh — 注入器离线验收（开发/验收两用）
#
# 构造临时 KB（agenote 域布局）+ spy agenote 包装器（计数 CLI spawn）+
# mock hook stdin，断言：宿主协议封包合法、CLI 缺失静默 0、缓存重放、
# 三件套门槛生效、语义开关零字节。注入状态机本身由 CLI 的
# tests/test_inject.py 覆盖——这里只验「宿主事件 → inject 参数 → 协议封包」。
# zcode(mjs)/hermes(py)/dsh(js) 实装就在本仓 packages/ 下，合仓后默认直接
# 验证仓内源码（CI 也跑这些 section）；要验部署副本时用 env 覆盖路径。
#
# 用法：bash injectors/selftest.sh
# 可调：AGENOTE_BIN=路径（默认找包内 .venv/bin/agenote 或 PATH 下含 inject
#       子命令的 agenote）；AGENOTE_ZCODE_PLUGIN_SRC / AGENOTE_HERMES_PLUGIN_SRC /
#       AGENOTE_DSH_PLUGIN_SRC

set -uo pipefail

INJ_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_ROOT="$(cd "$INJ_DIR/.." && pwd)"          # packages/agenote（本包）
MONOREPO_ROOT="$(cd "$PKG_ROOT/../.." && pwd)" # monorepo 根
PASS=0 FAIL=0
ok() { PASS=$((PASS + 1)); printf '  ✓ %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf '  ✗ %s\n' "$1"; }
section() { printf '\n== %s\n' "$1"; }

# ── 定位含 inject 子命令的 agenote（注入状态机下沉后的最低版本门）────────────
BIN=""
for cand in "${AGENOTE_BIN:-}" "$PKG_ROOT/.venv/bin/agenote"; do
  if [[ -n "$cand" && -x "$cand" ]] && "$cand" inject --help >/dev/null 2>&1; then
    BIN="$cand"
    break
  fi
done
if [[ -z "$BIN" ]] && command -v agenote >/dev/null 2>&1 \
  && agenote inject --help >/dev/null 2>&1; then
  BIN="$(command -v agenote)"
fi
if [[ -z "$BIN" ]]; then
  printf 'SKIP: 找不到含 context 子命令的 agenote（设 AGENOTE_BIN 或先 uv sync）\n' >&2
  exit 1
fi

# ── 临时 KB + spy PATH ──────────────────────────────────────────────────────
T="$(mktemp -d /tmp/agenote-injectors-selftest.XXXXXX)"
trap 'rm -rf "$T"' EXIT
# 预建 CLI 首跑会自举的目录（ensure_dirs 副作用），否则首次 CLI 调用改变
# memories/ 的 dir size 指纹，导致缓存测试非确定
mkdir -p "$T/kb/agenote/memories/projects" "$T/kb/agenote/experiences" \
  "$T/state" "$T/bin" "$T/proj"
: > "$T/kb/agenote/inbox.org"
echo '{}' > "$T/kb/agenote/index.json"

cat > "$T/kb/agenote/MEMORY.org" <<'EOF'
* User

** U1 沟通风格
:CREATED: [2026-09-20]
# 用户偏好简洁中文回复
偏好简洁的中文回复，代码注释可用英文。

* Feedback

** F1 测试纪律
:CREATED: [2026-09-21]
# 改动必须带测试
所有功能改动先写失败测试再实现，测试红灯后写实现。

* Project

selftest
** P1 selftest 注入架构
:PROJECT: selftest
:CREATED: [2026-09-22]
# context 命令是注入唯一通道
agenote context 命令输出注入简报，宿主插件实时调用。

* Environment

** E1 本机工具链
:CREATED: [2026-09-20]
# Guix 系统，uv 管理 Python
本机为 Guix 系统，Python 项目用 uv 管理依赖。

* Reference

** R1 BM25 检索参数
:CREATED: [2026-09-23]
# k1 1.5 b 0.75
知识库检索使用 BM25 算法，参数 k1=1.5，b=0.75。
EOF

cat > "$T/bin/agenote" <<EOF
#!/usr/bin/env bash
echo "ARGS: \$*" >> "\$SPY_LOG"
exec "$BIN" "\$@"
EOF
chmod +x "$T/bin/agenote"

export KB_ROOT="$T/kb" AGENOTE_INJECTOR_STATE_DIR="$T/state"
export SPY_LOG="$T/spy.log" PATH="$T/bin:$PATH"

# 注入策略已下沉 CLI：注入器每轮只 spawn 一次 `agenote inject`（不再 spawn
# context，也不再在宿主侧做三件套）。计数用于验证「注入器不重复生成内容」。
ctx_spawns() { grep -c '^ARGS: inject' "$SPY_LOG" 2>/dev/null || true; }
reset_state() { rm -f "$T"/state/*.json; : > "$SPY_LOG"; }
run_hook() { printf '%s\n' "$2" | bash "$INJ_DIR/$1" 2>/dev/null; }

SS='{"hook_event_name":"SessionStart","session_id":"s1","cwd":"'"$T"'","source":"startup"}'
P_LONG='{"hook_event_name":"UserPromptSubmit","session_id":"s1","cwd":"'"$T"'","prompt":"BM25 检索参数是什么来着"}'
P_LONG2='{"hook_event_name":"UserPromptSubmit","session_id":"s1","cwd":"'"$T"'","prompt":"Guix 系统工具链怎么管理"}'
P_SHORT='{"hook_event_name":"UserPromptSubmit","session_id":"s1","cwd":"'"$T"'","prompt":"继续"}'

# ═══ claude 注入器 ═══════════════════════════════════════════════════════════
section "claude/session-start.sh + user-prompt-submit.sh"
reset_state

OUT="$(run_hook claude/session-start.sh "$SS")"
python3 -c '
import json, sys
d = json.loads(sys.argv[1])["hookSpecificOutput"]
assert d["hookEventName"] == "SessionStart"
assert d["additionalContext"].startswith("<!-- agenote-context v1 mode=session")
' "$OUT" 2>/dev/null && ok "session-start 输出合法 JSON + 简报 marker" \
  || bad "session-start 输出合法 JSON + 简报 marker"

OUT2="$(run_hook claude/session-start.sh \
  '{"hook_event_name":"SessionStart","session_id":"s1","cwd":"'"$T"'","source":"compact"}')"
[[ -n "$OUT2" && "$OUT2" == "$OUT" ]] \
  && ok "指纹未变重触发：CLI 缓存重放同一简报" \
  || bad "指纹未变重触发应重放同一简报"

OUT="$(run_hook claude/user-prompt-submit.sh "$P_LONG")"
printf '%s' "$OUT" | python3 -c '
import json, sys
d = json.load(sys.stdin)["hookSpecificOutput"]
assert d["hookEventName"] == "UserPromptSubmit"
assert "mode=recall" in d["additionalContext"]
' 2>/dev/null && ok "recall 注入合法 JSON（query=前200字+伪词）" \
  || bad "recall 注入应输出合法 JSON"

[[ -z "$(run_hook claude/user-prompt-submit.sh "$P_LONG")" ]] \
  && ok "三件套①同 query 同指纹：CLI 判 duplicate，零输出" \
  || bad "三件套①同 query 应静默跳过"

[[ -z "$(run_hook claude/user-prompt-submit.sh "$P_SHORT")" ]] \
  && ok "三件套②短 prompt（<6 字符）：CLI 判 short，零输出" \
  || bad "三件套②短 prompt 应跳过"

S2="$(ctx_spawns)"
touch -d "2026-01-01" "$T/kb/agenote/MEMORY.org"
OUT_FRESH="$(run_hook claude/user-prompt-submit.sh "$P_LONG")"
[[ -n "$OUT_FRESH" && "$(ctx_spawns)" -gt "$S2" ]] \
  && ok "指纹失效（KB mtime 变化）：同 query 重新召回" \
  || bad "指纹失效后应重新召回"

CUM="$(python3 -c "import json;print(json.load(open('$T/state/claude-s1.json'))['cumulative'])" 2>/dev/null || echo 0)"
OUT_CUM="$(AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET="$((CUM + 1))" \
  run_hook claude/user-prompt-submit.sh "$P_LONG2")"
[[ -n "$OUT_CUM" ]] && ok "三件套③累计预算临界：最后一条仍可注入" \
  || bad "三件套③累计预算临界应仍注入"
[[ -z "$(AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET="$CUM" \
  run_hook claude/user-prompt-submit.sh \
  '{"hook_event_name":"UserPromptSubmit","session_id":"s1","cwd":"'"$T"'","prompt":"另一条足够长的不同问题"}')" ]] \
  && ok "三件套③累计触顶：停 recall（零输出）" \
  || bad "三件套③触顶后应停 recall"

reset_state
# 语义开关由 CLI 裁决：注入器照常 spawn 一次问询，CLI 应答 disabled → 零字节不注
[[ -z "$(AGENOTE_INJECTION_ENABLED=false run_hook claude/session-start.sh "$SS")" ]] \
  && ok "AGENOTE_INJECTION_ENABLED=false：CLI 应答 disabled，零字节不注" \
  || bad "总开关关闭应零字节不注"

# CLI 缺失：PATH 指向空目录（host 脚本用绝对 bash 启动，agent 不可见即缺 CLI）
mkdir -p "$T/emptybin"
NOCLI_OUT="$(mktemp)"
printf '%s\n' "$P_LONG" | env -i PATH="$T/emptybin" HOME="$HOME" KB_ROOT="$T/kb" \
  AGENOTE_INJECTOR_STATE_DIR="$T/state" "$(command -v bash)" \
  "$INJ_DIR/claude/user-prompt-submit.sh" > "$NOCLI_OUT" 2>/dev/null
[[ $? -eq 0 && ! -s "$NOCLI_OUT" ]] \
  && ok "CLI 缺失：静默退出 0 零输出" \
  || bad "CLI 缺失应静默退出 0"

# ═══ codex 注入器（recipe 模板，协议形状同 claude）═══════════════════════════
section "codex/（recipe 模板：2800 字符预算）"
reset_state
printf '%s\n' "$SS" | bash "$INJ_DIR/codex/session-start.sh" 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)["hookSpecificOutput"]
c = d["additionalContext"]
assert d["hookEventName"] == "SessionStart"
assert c.startswith("<!-- agenote-context v1 mode=session budget=2800")
assert len(c) <= 2800
' 2>/dev/null && ok "codex 简报 ≤2800 字符（CJK 折算保守值）" \
  || bad "codex 简报应 ≤2800 字符且带 codex host 标记"
printf '%s\n' "$P_LONG" | bash "$INJ_DIR/codex/user-prompt-submit.sh" 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)["hookSpecificOutput"]
assert d["hookEventName"] == "UserPromptSubmit" and "mode=recall" in d["additionalContext"]
' 2>/dev/null && ok "codex recall 通道协议形状 ✓" \
  || bad "codex recall 通道应输出合法 JSON"

# ═══ zcode 插件（本仓实装，env 可覆盖为部署副本）══════════════════════════════
ZZ_SRC="${AGENOTE_ZCODE_PLUGIN_SRC:-$MONOREPO_ROOT/packages/agenote-zcode}"
section "zcode agenote-zcode 插件（mjs）"
if [[ -d "$ZZ_SRC" ]] && command -v node >/dev/null 2>&1; then
  HOOKS="$ZZ_SRC/hooks"
  node --check "$HOOKS/lib.mjs" 2>/dev/null && ok "lib.mjs 语法 ✓" || bad "lib.mjs 语法"
  node --check "$HOOKS/prompt-inject.mjs" 2>/dev/null && ok "prompt-inject.mjs 语法 ✓" || bad "prompt-inject.mjs 语法"
  node --check "$HOOKS/session-start.mjs" 2>/dev/null && ok "session-start.mjs 语法 ✓" || bad "session-start.mjs 语法"
  python3 -m json.tool "$HOOKS/hooks.json" >/dev/null 2>&1 && ok "hooks.json 合法 JSON ✓" || bad "hooks.json 非法"
  grep -q 'prompt-inject.mjs' "$HOOKS/hooks.json" \
    && ok "hooks.json 含无 matcher 的每轮召回条目 ✓" || bad "hooks.json 缺召回条目"

  reset_state
  ZOUT="$(printf '%s\n' '{"hook_event_name":"SessionStart","session_id":"z1","source":"startup","cwd":"'"$T"'"}' \
    | node "$HOOKS/session-start.mjs" 2>/dev/null)"
  printf '%s' "$ZOUT" | python3 -c '
import json, sys
c = json.load(sys.stdin)["hookSpecificOutput"]["additionalContext"]
assert "agenote-context v1 mode=session" in c  # 简报段已追加
' 2>/dev/null && ok "zcode SessionStart 注入简报段 ✓" || bad "zcode SessionStart 简报段缺失"
  ZOUT2="$(printf '%s\n' '{"hook_event_name":"SessionStart","session_id":"z1","source":"compact","cwd":"'"$T"'"}' \
    | node "$HOOKS/session-start.mjs" 2>/dev/null)"
  printf '%s' "$ZOUT2" | grep -q 'agenote-context v1 mode=session' \
    && ok "zcode SessionStart 重触发：CLI 缓存重放简报 ✓" \
    || bad "zcode 重触发应重放简报"

  ZOUT="$(printf '%s\n' "$P_LONG" | node "$HOOKS/prompt-inject.mjs" 2>/dev/null)"
  printf '%s' "$ZOUT" | grep -q 'mode=recall' && ok "zcode prompt-inject recall ✓" || bad "zcode recall 注入失败"
  [[ -z "$(printf '%s\n' "$P_LONG" | node "$HOOKS/prompt-inject.mjs" 2>/dev/null)" ]] \
    && ok "zcode 三件套①同 query 去重 ✓" || bad "zcode 同 query 应去重"
  [[ -z "$(printf '%s\n' "$P_SHORT" | node "$HOOKS/prompt-inject.mjs" 2>/dev/null)" ]] \
    && ok "zcode 三件套②短 prompt 门槛 ✓" || bad "zcode 短 prompt 应跳过"
  # 防抖状态隔离到临时目录：不污染用户真实 ~/.local/state，且自测可重复触发
  mkdir -p "$T/zdata"
  printf '%s\n' '{"hook_event_name":"UserPromptSubmit","session_id":"z1","prompt":"搞定了"}' \
    | ZCODE_PLUGIN_DATA="$T/zdata" node "$ZZ_SRC/hooks/prompt-submit.mjs" 2>/dev/null | grep -q 'agenote-hook' \
    && ok "既有完成信号 hook 未受影响 ✓" || bad "完成信号 hook 被破坏"
elif [[ ! -d "$ZZ_SRC" ]]; then
  printf '  - 跳过（未找到 %s）\n' "$ZZ_SRC"
else
  printf '  - 跳过（node 不可用）\n'
fi

# ═══ hermes 插件（本仓实装，env 可覆盖为部署副本）═════════════════════════════
HZ_SRC="${AGENOTE_HERMES_PLUGIN_SRC:-$MONOREPO_ROOT/packages/agenote-hermes}"
section "hermes agenote 插件（python）"
if [[ -f "$HZ_SRC/__init__.py" ]]; then
  python3 -c "import ast; ast.parse(open('$HZ_SRC/__init__.py').read())" 2>/dev/null \
    && ok "__init__.py AST 语法 ✓" || bad "__init__.py 语法错误"
  reset_state
  HOUT="$(KB_ROOT="$T/kb" AGENOTE_INJECTOR_STATE_DIR="$T/state" \
    AGENOTE_BIN="$T/bin/agenote" python3 - "$HZ_SRC" "$T" <<'PYEOF'
import importlib.util, json, os, sys
src, tmp = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("agenote_hermes_test", os.path.join(src, "__init__.py"))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
mod._RESOLVED_BIN = os.path.join(tmp, "bin", "agenote")  # spy：计数并转发真 CLI

brief = mod._render_brief({"platform": "main", "session_id": "h1", "cwd": tmp})
assert brief.startswith("<!-- agenote-context v1 mode=session"), brief[:60]
assert mod._render_brief({"platform": "main", "session_id": "h1", "cwd": tmp}) == brief
assert mod._render_brief({"platform": "subagent", "session_id": "h1", "cwd": tmp}) == ""
recall = mod._recall_context("h1", "BM25 检索参数是什么来着")
assert recall.startswith("<!-- agenote-context v1 mode=recall"), recall[:60]
assert mod._recall_context("h1", "BM25 检索参数是什么来着") == "", "去重"
assert mod._recall_context("h1", "继续") == "", "短门槛"
state = json.load(open(os.path.join(tmp, "state", "hermes-h1.json"), encoding="utf-8"))
assert state.get("cumulative") == len(recall) and state.get("recall_key"), state
assert mod._pre_llm_call(session_id="x", user_message="搞定了", platform="main") is not None
assert mod._pre_llm_call(session_id="x", user_message="搞定了", platform="subagent") is None
print(json.dumps({"ok": True}))
PYEOF
  )" 2>/dev/null
  [[ "$HOUT" == *'"ok": true'* ]] \
    && ok "导入级：简报缓存/recall/完成信号（策略由 CLI 统一）✓" \
    || bad "hermes 插件功能验证失败: $HOUT"
else
  printf '  - 跳过（未找到 %s）\n' "$HZ_SRC/__init__.py"
fi

# ═══ dsh 插件（本仓实装，重写型：简报 + 召回同一快照）═════════════════════════
DSH_SRC="${AGENOTE_DSH_PLUGIN_SRC:-$MONOREPO_ROOT/packages/dsh-agenote}"
section "dsh dsh-agenote 插件（mjs）"
if [[ -d "$DSH_SRC" ]] && command -v node >/dev/null 2>&1; then
  for f in "$DSH_SRC"/index.js "$DSH_SRC"/hooks.js "$DSH_SRC"/commands.js \
           "$DSH_SRC"/tools.js "$DSH_SRC"/lib.js; do
    cp "$f" "/tmp/dsh-$(basename "${f%.js}").mjs"
    node --check "/tmp/dsh-$(basename "${f%.js}").mjs" 2>/dev/null \
      && ok "$(basename "$f") 语法 ✓" || bad "$(basename "$f") 语法"
  done
  reset_state
  DOUT="$(node --input-type=module - "$DSH_SRC" "$T" <<'JSEOF'
const [src, tmp] = process.argv.slice(2);
const { pathToFileURL } = await import("node:url");
const { readFileSync } = await import("node:fs");
const hooks = await import(pathToFileURL(`${src}/hooks.js`).href);

const handlers = new Map();
const contexts = [];
const ctx = {
  on: (name, fn) => handlers.set(name, fn),
  systemPrompt: { section: () => {}, context: (c) => contexts.push(c) },
  logger: { warn: () => {} },
};
hooks.apply(ctx, {
  enabled: true, status: false, brief: true, recall: true,
  completionSignals: true, idleFallback: false,
  signals: hooks.COMPLETION_SIGNALS, debounceMs: 300000,
  idleMs: 300000, statusTtlMs: 300000,
});

const spawns = () => {
  try {
    return readFileSync(process.env.SPY_LOG, "utf8")
      .split("\n").filter((l) => l.startsWith("ARGS: context")).length;
  } catch { return 0; }
};
const userEvent = (text) => ({
  type: "user/message",
  data: { source: { kind: "user" }, content: [{ type: "text", text }] },
});
const events = [userEvent("BM25 检索参数是什么来着")];
const session = {
  header: { origin: "main", cwd: tmp },
  surface: { nodes: [0] },
  eventAt: (seq) => events[seq],
};
const followed = [];
const agent = { id: "a1", session, followup: (m) => followed.push(m) };
const memory = contexts.find((c) => c.name === "agenote-memory");
if (!memory) throw new Error("agenote-memory context 未注册");

const text = memory.text({ agent });
const s1 = spawns();
const again = memory.text({ agent });

events[0] = userEvent("继续");
const shortText = memory.text({ agent });
const shortSpawns = spawns();

session.header.origin = "subagent";
const subagentText = memory.text({ agent });
session.header.origin = "main";

handlers.get("agent/inbox/inserted")(
  { agent, message: { source: { kind: "user" }, content: [{ type: "text", text: "搞定了" }] } },
);
handlers.get("agent/turn-stopping")({ agent });

console.log(JSON.stringify({
  session: text.includes("mode=session"),
  recall: text.includes("mode=recall"),
  idempotent: again === text && spawns() === s1,
  short: shortText.includes("mode=session") && !shortText.includes("mode=recall") && shortSpawns === s1,
  subagent: subagentText === "",
  review: followed.length === 1 && followed[0].content[0].text.includes("agenote-hook"),
  spawns: s1,
}));
JSEOF
  )" 2>/dev/null
  printf '%s' "$DOUT" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["session"],d' 2>/dev/null \
    && ok "简报注入上下文（mode=session）✓" || bad "缺乏会话简报注入"
  printf '%s' "$DOUT" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["recall"],d' 2>/dev/null \
    && ok "按最近用户消息召回（mode=recall）✓" || bad "缺乏每轮召回"
  printf '%s' "$DOUT" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["idempotent"] and d["spawns"]==2,d' 2>/dev/null \
    && ok "指纹+query 缓存：二次求值零 spawn ✓" || bad "缓存未生效或 CLI 调用次数异常"
  printf '%s' "$DOUT" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["short"],d' 2>/dev/null \
    && ok "短 prompt（<6 字符）跳过召回、保留简报 ✓" || bad "短 prompt 门槛未生效"
  printf '%s' "$DOUT" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["subagent"],d' 2>/dev/null \
    && ok "subagent 会话不注入 ✓" || bad "subagent 未豁免"
  printf '%s' "$DOUT" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["review"],d' 2>/dev/null \
    && ok "完成信号触发 review 提示 ✓" || bad "完成信号未触发"
  DOUT2="$(AGENOTE_INJECTION_ENABLED=false node --input-type=module - "$DSH_SRC" "$T" <<'JSEOF2'
const [src, tmp] = process.argv.slice(2);
const { pathToFileURL } = await import("node:url");
const hooks = await import(pathToFileURL(`${src}/hooks.js`).href);
const contexts = [];
const ctx = {
  on: () => {},
  systemPrompt: { section: () => {}, context: (c) => contexts.push(c) },
  logger: { warn: () => {} },
};
hooks.apply(ctx, {
  enabled: true, status: false, brief: true, recall: true,
  completionSignals: false, idleFallback: false, signals: [], debounceMs: 300000,
  idleMs: 300000, statusTtlMs: 300000,
});
const events = [{ type: "user/message", data: { source: { kind: "user" }, content: [{ type: "text", text: "BM25 检索参数是什么来着" }] } }];
const agent = {
  id: "a2",
  session: { header: { origin: "main", cwd: tmp }, surface: { nodes: [0] }, eventAt: (s) => events[s] },
};
console.log(JSON.stringify({ disabled: contexts[0].text({ agent }) === "" }));
JSEOF2
  )" 2>/dev/null
  printf '%s' "$DOUT2" | python3 -c 'import json,sys;d=json.load(sys.stdin);assert d["disabled"],d' 2>/dev/null \
    && ok "AGENOTE_INJECTION_ENABLED=false：CLI 应答 disabled，零字节不注 ✓" \
    || bad "语义开关关闭后仍有注入"
elif [[ ! -d "$DSH_SRC" ]]; then
  printf '  - 跳过（未找到 %s）\n' "$DSH_SRC"
else
  printf '  - 跳过（node 不可用）\n'
fi

# ═══ opencode 模板（结构自洽 + 类型签名对照）═════════════════════════════════
section "opencode agenote-context.ts（recipe 模板结构）"
TS="$INJ_DIR/opencode/agenote-context.ts"
grep -q 'export type { Plugin }\|from "@opencode-ai/plugin"' "$TS" 2>/dev/null \
  && ok "引用官方 SDK 类型（@opencode-ai/plugin）✓" || bad "未引用官方 SDK 类型"
grep -q '"experimental.chat.messages.transform"' "$TS" 2>/dev/null \
  && ok "挂点 = experimental.chat.messages.transform ✓" || bad "挂点签名不符"
grep -q ' agenote-context' "$TS" && grep -q '/agenote-context' "$TS" 2>/dev/null \
  && ok "重写型幂等：剥旧块标记对（BEGIN/END）齐备 ✓" || bad "剥块标记缺失"
grep -q 'kbFingerprint\|mtimeMs' "$TS" 2>/dev/null \
  && ok "指纹缓存（mtime+size）✓" || bad "缺指纹缓存"
DTS="$HOME/.config/opencode/node_modules/@opencode-ai/plugin/dist/index.d.ts"
if [[ -f "$DTS" ]]; then
  grep -q 'experimental.chat.messages.transform' "$DTS" \
    && ok "宿主 SDK d.ts 仍含该 experimental 签名（1.18.18 基线）✓" \
    || bad "宿主 SDK 已漂移：核对新版 d.ts"
else
  printf '  - 跳过 d.ts 对照（本机无 opencode SDK）\n'
fi

# ═══ 汇总 ════════════════════════════════════════════════════════════════════
printf '\n══ 结果：%d passed, %d failed ══\n' "$PASS" "$FAIL"
[[ "$FAIL" == 0 ]]

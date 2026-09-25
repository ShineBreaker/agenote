# agenote → zcode 注入（实装在 agenote-zcode 插件内，本目录无脚本）

zcode 的注入**不在本仓库交付脚本**，而是直接实装在生态既有的 zcode 插件
`agenote-zcode` 里（与 claude/codex 的独立脚本不同——zcode 侧挂点在插件
hooks.json 内统一管理，避免双挂接入口）：

- **单一真相源**：`~/Projects/Config/Guix-configs/dotfiles/mutable/agenote/.zcode/plugins/agenote-zcode/`
  （Guix-configs 主仓库直接跟踪）
- **装载点**：`~/.zcode/plugins/agenote-zcode/`（hooks 文件为指向源的单文件
  symlink，源改动即生效）

## 挂点（由插件 hooks.json 自带，无需用户另配）

| 挂点 | 条目 | 行为 |
|---|---|---|
| SessionStart（matcher startup\|resume\|clear\|compact） | `hooks/session-start.mjs`（既有条目扩展） | 使用规则 + 健康度摘要之后追加 `agenote context --mode session --host zcode --budget 8000` 简报段（text 空则不追加）；同时承担三件套的会话重置（累计清零 + recall 解禁） |
| UserPromptSubmit（无 matcher = 每轮） | `hooks/prompt-inject.mjs`（新增条目，与既有完成信号条目并存） | recall 注入：query = prompt 前 200 字符 + cwd basename 伪词，预算 4000，三件套全量 |
| UserPromptSubmit（完成信号 matcher） | `hooks/prompt-submit.mjs`（既有，未改动） | 任务完成信号 → review 提示 |

三件套/缓存的 mjs 实现在 `hooks/lib.mjs`（指纹 = MEMORY.org+memories/
mtime+size；状态文件 `~/.cache/agenote/injectors/zcode-<session_id>.json`），
与 `injectors/lib.sh`（claude/codex 用）同构——改语义需两处同步。

## 预算

简报 8000 / recall 4000 字符（zcode 单事件上限 24000，静默截断）；
累计预算 24000 触顶停 recall。env 覆盖同其他宿主（见总览 README）。

## 验收

```bash
# 离线 dry-run（构造临时 KB + env 覆盖，见 injectors/selftest.sh 的 zcode 节）
printf '%s\n' '{"hook_event_name":"SessionStart","session_id":"manual","source":"startup"}' \
  | node ~/.zcode/plugins/agenote-zcode/hooks/session-start.mjs

# 宿主内验收（调研已验证的观测面）：
#   用户级 ~/.zcode/cli/config.json 设 logging.level=debug，会话日志里看
#   context.built 与 hook 执行记录
```

## 待实装验证项

- zcode UserPromptSubmit stdin 的 prompt 字段实际下发名（prompt-inject.mjs
  防御式解析 `prompt`/`user_prompt`/`userPrompt`；若全缺则本轮不注，仅指纹
  检查——与协议现状一致，见脚本头注）。
- 同事件多 hook 条目的 additionalContext 合并顺序（完成信号条目 + 召回
  条目同轮命中时）。
- workspace hooks 首次信任准入：插件 hooks.json 变更后 zcode 可能提示确认。

## 安装前置

agenote CLI 须含 `context` 子命令（旧版 CLI 静默空串 = 不注入，会话不受影响）；
语义开关 `AGENOTE_INJECTION_ENABLED=false` 一键全关。

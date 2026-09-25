# injectors/ — agenote 记忆注入器包

宿主插件/hook 层实时调 `agenote context` 取记忆注入会话（设计
`AGENOTE_INJECTION_DESIGN.md` §3 C4）。agenote 本体保持纯粹 CLI：无常驻
进程、无调度器，注入职责全在本包 / 各宿主插件内。

## 架构（文字图）

```
                       ┌─ agenote SCHEMA [injection]（语义开关单一真相源）
                       │    enabled / hosts.*_enabled / 累计预算 / recall 门槛
KB (MEMORY.org + memories/)
   │  mtime+size 指纹（D8）
   ▼
agenote context --mode session|recall --query Q --budget N --host H [--format json]
   │  三态：ok / empty / disabled（非 ok → text 零字节 → 注入器不注任何东西）
   ▼
┌──────────────┬───────────────┬────────────────┬──────────────┬───────────┐
│ zcode        │ claude        │ codex          │ opencode     │ hermes    │
│ 追加型       │ 追加型        │ 追加型         │ 重写型       │ 追加型    │
│ agenote-zcode│ injectors/    │ injectors/     │ injectors/   │ agenote   │
│ 插件(mjs)    │ claude/ (sh)  │ codex/ (sh 模板)│ opencode/(ts)│ 插件(py)  │
└──────────────┴───────────────┴────────────────┴──────────────┴───────────┘
     │               │              │                │              │
     └── 状态文件统一：~/.cache/agenote/injectors/<host>-<session_id>.json
         （整目录可清理，无副作用；hermes 插件内另有进程内简报缓存）
```

**追加型三件套**（zcode/claude/codex/hermes 的每轮通道，防上下文累积膨胀）：
①指纹+query 未变不重注（含被 `recall_min_score` 滤空的情况，记键零重跑）；
②recall 门槛（prompt 有效长度 < `recall_min_query` 跳过，「继续/ok」类不注）；
③单会话累计 ≤ 24000 字符触顶停 recall（SessionStart/compact 重置）。
**重写型**（opencode）：每请求幂等（先剥旧块再注缓存块），只需指纹缓存。

## 目录

| 路径 | 形态 | 挂点 | 单次预算（简报/recall） |
|---|---|---|---|
| `zcode/` | 仅 README——实装在 agenote-zcode 插件（Guix-configs） | SessionStart + UserPromptSubmit（插件 hooks.json） | 8000 / 4000 |
| `claude/` | 成品脚本（sh）+ README | `~/.claude/settings.json` hooks 节 | 8000 / 4000 |
| `codex/` | recipe 模板（sh）+ README | `~/.codex/config.toml` [hooks]（示例待实装核对） | 2800 / 2800（CJK 折算保守值） |
| `opencode/` | recipe 模板（ts）+ README | `experimental.chat.messages.transform` | 8000 / —（重写型） |
| `hermes/` | 仅 README——实装在 hermes agenote 插件（Guix-configs） | system prompt section + pre_llm_call | 3800 / 2000 |
| `lib.sh` | bash 共享库（claude/codex source） | — | — |
| `selftest.sh` | 离线验收（mock stdin + 临时 KB + spy PATH） | — | — |

## 安装总则

1. **前置**：agenote CLI 含 `context` 子命令（旧版 CLI 静默空串 = 不注入，
   会话永不受影响）。每次注入都带 `AGENOTE_AGENT=<host>` 归因前缀。
2. **挂接**：按各宿主子目录 README 操作；注入器绝不代改宿主配置。
3. **写侧禁用**（防双真相源，接管硬前提）：claude 关
   `autoMemoryEnabled`+`autoDreamEnabled`；codex 保持 `[features] memories`
   默认关；hermes 关 `memory.memory_enabled`+`user_profile_enabled`；
   zcode 关 `features.memory`/`memory.use`。状态用 `agenote doctor` 例行检测。
4. **验收**：先 `bash injectors/selftest.sh`（离线全量），再按各 README 的
   宿主内观测面复核；每个 README 标注协议来源版本（R2 漂移应对）。

## 开关（一键全关）

语义开关真相源在 agenote 侧，注入器无独立开关逻辑：

```bash
AGENOTE_INJECTION_ENABLED=false        # 总开关：context 输出零字节 → 全宿主自然静默
AGENOTE_INJECTION_HOSTS_ZCODE_ENABLED=false   # 单宿主关闭
```

任一开关关闭时 CLI 返回 `status: disabled` / 零字节，注入器直接不注——
无需动插件配置。物理移除注入器（删挂接条目）是最后手段；改行为优先改
SCHEMA（同agenote-curator skill 的「注入器与开关」节）。

## 可调 env（注入器侧旋钮；均有默认值，可不设）

| env | 默认 | 语义 |
|---|---|---|
| `AGENOTE_INJECTION_BRIEF_BUDGET` | 8000（codex 2800） | 简报单次预算（字符） |
| `AGENOTE_INJECTION_RECALL_BUDGET` | 4000（codex 2800） | recall 单次预算（字符） |
| `AGENOTE_INJECTION_SESSION_CUMULATIVE_BUDGET` | 24000 | 单会话累计预算（追加型；SessionStart/compact 重置） |
| `AGENOTE_INJECTION_MIN_QUERY` | 6 | recall 有效 query 最短字符（镜像 `recall_min_query`，只省 spawn；CLI 侧仍是最终裁决） |
| `AGENOTE_INJECTOR_STATE_DIR` | `~/.cache/agenote/injectors` | 状态目录 |
| `KB_ROOT` | — | 与 CLI 同名 env 同口径（指纹定位用） |

## 与 context-select 决策核的关系（两层并列，D7）

- `~/.config/agents/context-select.sh`（zcode/omp/hermes/crush 共享）注入的
  是**原则层**：怎么做事的稳定规范（00-core/INDEX/domains），静态门控。
- agenote 注入器注入的是**事实层**：动态、会更新会裁决的记忆（画像/项目/
  环境/经验），指纹失效自动换新。
- 两层并列不融合：任务规则看前者，事实记忆看后者；未来若融合，agenote
  输出可作为 selector 的上游之一，不强制。

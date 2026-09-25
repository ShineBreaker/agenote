# agenote → hermes 注入（实装在 agenote 插件内，本目录无模板代码）

hermes 的注入直接实装在生态既有的 hermes 插件 `agenote` 里（不做独立
`agenote-inject` 模板——原插件已是生态唯一 hermes 接入点，注入能力原地合入，
`register` 时同时注册注入挂点与既有命令入口，无命名冲突问题）：

- **单一真相源（已收编进 agenote stow 包）**：
  `~/Projects/Config/Guix-configs/dotfiles/mutable/agenote/.local/share/hermes/plugins/agenote/`
  （`__init__.py` + `plugin.yaml` + `.gitignore`，Guix-configs 主仓库跟踪）
- **装载点**：`~/.local/share/hermes/plugins/agenote/`（`__init__.py`、
  `plugin.yaml` 为指向源的单文件相对 symlink，pi/omp 插件同类模式；
  `__pycache__` 留装载点本地不迁移）
- 改造前旧版备份：`/tmp/hermes-agenote-backup/`（一次性，勿长期依赖）

宿主版本（验证时）：hermes v0.21.3（2026-09，`$HERMES_HOME` 实装
`hermes --version`；register_system_prompt_section / pre_llm_call /
register_command 三挂点 API 形态以该版本为准，升级后需复测）。

## 挂点（同一插件内两通道 + 既有能力共存）

| 挂点 | 行为 | 预算 |
|---|---|---|
| `register_system_prompt_section`（`agenote-context-brief` 节，框架默认 after_memory 锚点） | 会话级简报（`--mode session`）；进程内指纹缓存——指纹未变零 spawn 重放；**compact 重建时 render 被重新调用，从缓存返回而非空串**（否则重建后丢简报）；空 content（disabled/empty）不入缓存——开关转开即时生效（对齐 lib.sh；disabled 期每次重建多一次 spawn，render 频率低可接受） | 3800 字符（框架单节上限 4000） |
| `pre_llm_call`（每回合） | recall 注入（追加型三件套：指纹+query 未变不重注 / 短 prompt 门槛 / 累计 24000 触顶停 recall），与既有任务完成信号检测共存——两者独立触发，同回合命中合并为一个 context；subagent/cron 平台整体豁免 | 2000 字符 |
| 状态文件 | `~/.cache/agenote/injectors/hermes-<session_id>.json`（与 bash 注入器同款布局；session_id 缺失退化 `hermes.json`） | — |
| 既有能力 | 完成信号检测、`/agenote-summarize`、`/agenote-health` 未改动；`/agenote-curate` 改为注入策展任务提示（agent 按 agenote-curator skill 执行，对齐 pi f8ccec3——CLI 无 `curate` 子命令） | — |

## 安装前置（写侧禁用，防双真相源）

hermes cli 配置 `memory.memory_enabled=false` + `memory.user_profile_enabled=false`
（双键关停内置记忆 store 与工具）。`agenote doctor` 可检测。

## 验收

```bash
# 1. 语法级（本仓库 selftest 已含）：
python3 -c "import ast; ast.parse(open('$HOME/.local/share/hermes/plugins/agenote/__init__.py').read())"

# 2. 宿主内验收（真机步骤）：
#   a. HERMES_PLUGINS_DEBUG=1 启动 hermes，确认 agenote 插件加载无错
#   b. /context 命令：memory/user 类目之外应能看到 agenote 节的占用
#   c. sidecar 审计：每回合注入字节持久化为 api_content sidecar，可事后
#      核对 recall 注入内容与预算
#   d. 会话中途改 KB 后 compact，验证简报随重建刷新（指纹失效重跑）
```

## 待实装验证项

- after_memory 锚点为框架默认位（global-context 插件先例：section 注册无需
  plugin.yaml 声明、无锚点参数）——实际拼接位置以 `/context` 实测为准。
- hermes 会话内 section 渲染冻结时点：会话中段 KB 更新要等下次重建才可见
  （框架快照语义 + 本插件指纹缓存双重叠加，最坏延迟 = 到下次 compact/新会话）。
- 旧版 agenote CLI（无 context 子命令）静默空串 = 不注入，会话不受影响。

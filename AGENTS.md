# AGENTS.md — agenote monorepo

跨 Agent 经验平台。**单仓 7 个组件**：CLI 是唯一实现层，其余是宿主适配层与行为规范
载体。布局与发布决策见
[`docs/adr/0005`](docs/adr/0005-monorepo-unify-seven-components.md)，领域术语见
[`CONTEXT.md`](CONTEXT.md)。

改动前先定位分支：**你在改哪一层**决定了其余全部动作。各分支的细则按分支分节写在
[`CODING_STANDARDS.md`](CODING_STANDARDS.md)——只读你那一节，不必通读。

## 1. 组件地图

| 组件 | 是什么 | 改它时读 |
|---|---|---|
| `packages/agenote` | Python CLI（PyPI: `agenote`），**唯一实现层** | [§agenote](CODING_STANDARDS.md#agenote) |
| `packages/agenote-el` | Emacs 插件（ELPA），纯 CLI 适配层 | [§agenote-el](CODING_STANDARDS.md#agenote-el) |
| `packages/agenote-zcode` / `agenote-hermes` / `agenote-pi` | 宿主注入插件：zcode hooks、hermes Python 插件、omp 单文件 TS | [§宿主适配](CODING_STANDARDS.md#宿主适配) |
| `packages/dsh-agenote` | DSH cordis bundle（npm），本包另有自带 `AGENTS.md`，冲突时以它为准 | [§宿主适配](CODING_STANDARDS.md#宿主适配) |
| `packages/agenote-skills` | 3 个 agent skill（base / curator / review），**行为规范的载体** | [§agenote-skills](CODING_STANDARDS.md#agenote-skills) |
| `spec/injection.toml` | 跨组件语义源（信号 / 预算 / 时序），被 `tools/codegen` 消费 | [§spec](CODING_STANDARDS.md#spec) |
| `tools/` | `codegen/` 生成器与契约校验器；`release/build.py` 产物构建 | [§验证](CODING_STANDARDS.md#验证) |

**包目录不重命名**——名字已出现在 URL、插件名、import 路径与用户文档中。

**不在本仓**：知识库本体 `~/Documents/Org`（CLI 的 `KB_ROOT`）是独立 git 仓；
dotfiles 仓 `Guix-configs` 通过钉版本锁文件消费本仓的发布产物。

## 2. 单一真相源分层

**只有一处用 → 留在代码里写注释；两处以上用 → 进 `spec/`。** 跨层放错位置是本仓最
常见的走偏方式。

| 语义 | 真相源 |
|---|---|
| 跨宿主复制的信号 / 预算 / 时序 | `spec/injection.toml` |
| CLI 的配置键与默认值 | `packages/agenote/src/agenote/config.py` 的 `SCHEMA` |
| 各插件的 Config schema 默认值 | 各包导出的 `Config` 声明 |
| 行为规范（写入流程 / 卡片格式 / 策展策略） | `packages/agenote-skills/*/SKILL.md`——文档即载体，改策略不该发版 |
| 卡片状态机 / 领域术语 | `CONTEXT.md` + 各包 `docs/adr/` |

## 3. 全仓铁律

- **落盘**：`safeio.atomic_write` + `kb_lock`（fcntl.flock，默认 10s 超时，可在
  `[safeio].lock_timeout_seconds` 覆盖）。
- **配置**：只改 `config.py` 的 `SCHEMA`，其他模块从 schema 读。
- **生成块**：`COMPLETION_SIGNALS` 等 `BEGIN`/`END` 标记之间的内容由
  `tools/codegen/generate.py` 从 spec 写入。改语义改 spec 再重新生成；生成物随仓提交
  （宿主插件独立安装，运行时没有 monorepo）。生成块可以嵌在文件中间。
- **提交**：`type(scope): 简短描述`（动词开头、小写、无句号），按实际宿主与模型附
  `Co-authored-by`。细则见 [§commit](CODING_STANDARDS.md#commit)。

## 4. 静默失败

这四类故障**都不在本地留痕**：CI 全绿、commit 干净、本地 tag 看着正常。

- **跑 CLI 带 `AGENOTE_AGENT=<宿主>`**（`AGENOTE_AGENT=zcode agenote ...`），否则卡片
  归因会落到默认 agent（`omp`）。该变量只打标签，不做写入隔离。
- **`min_query` 两处不等值是设计，不是漂移**：`spec/injection.toml` 的
  `[append].min_query = 6` 对应 CLI 的 `recall_min_query`，两边仅通过 env
  （`AGENOTE_INJECTION_MIN_QUERY`）联动。**CLI 侧始终是最终裁决**，注入器那道门槛只为
  省一次 spawn。改 `config.toml` 时不必同步它。
- **tag 漏推 = GitHub 无 Release**：`release.yml` 认 `on: push: tags`，漏推则远端无
  tag 事件、workflow 根本不跑，而本地一切正常。**每次发版必做自查**：
  `git ls-remote --tags origin | grep <tag>` 确认远端 tag 存在。
- **一次 push 最多 3 个 tag**：超过则 tag 推成功但**不产生任何 push 事件**，所有
  workflow 不触发。分批推或逐个推。

发版流程与两个 tag 陷阱的完整处置（含 `workflow_dispatch` 回填、悬空 tag 诊断）见
[§发版](CODING_STANDARDS.md#发版)。

## 5. 分支路由

- **改 CLI**（子命令、配置键、卡片、检索、`extract/`）→ [§agenote](CODING_STANDARDS.md#agenote)
- **改 Emacs 端**，或改 CLI 的自由文本 / `--json` 输出 → [§agenote-el](CODING_STANDARDS.md#agenote-el)（**脆弱耦合**，附 golden 快照重生命令）
- **改注入器**（zcode / hermes / pi / dsh / claude / codex / opencode）→ [§宿主适配](CODING_STANDARDS.md#宿主适配)
- **改跨组件语义**（完成信号、注入预算、时序阈值）→ [§spec](CODING_STANDARDS.md#spec)
- **改行为规范**（写入流程 / 卡片格式 / 策展策略）→ [§agenote-skills](CODING_STANDARDS.md#agenote-skills)
- **发版**（改版本、写 CHANGELOG、打 tag）→ [§发版](CODING_STANDARDS.md#发版)
- **让 dotfiles 侧生效** → [§dotfiles](CODING_STANDARDS.md#dotfiles)
- **验证**：按改动分支跑对应命令，全量命令集见 [§验证](CODING_STANDARDS.md#验证)。
  改适配器时**顺手跑一遍对应语言的语法检查**——codegen 过程中真实踩到过两个只有
  语法检查能抓的错误（Python 列表漏尾逗号、ESM 里丢 `const`）。
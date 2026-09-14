# Changelog

本项目的所有显著变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.10] - 2026-09-14

### Fixed

- **hermes 源记忆库按日整份重复导出**（`extract/hermes.py`）：hermes facts 不填 `timestamp`，而日期过滤对空时间戳「不过滤」（防静默丢数据），117 条记忆在 14 天产物里各出现一次。改为取 DB 的 `updated_at`（改写即算当天浮现）、缺值回落 `created_at`；实测 09-13 从 117 条降到 1 条，14 天语料 1.9MB → 0.85MB。
- **`list --unused-days` 把复核过的老卡列回候选**（`cards.py`）：判定只看 `last_used`，转 `stable` 后仍被列出，同一批老卡每轮策展重审一遍。候选改为只收 `status=done`（`stable` 已是复核终态）。
- **`memory --stale` 报出已归档条目**（`memory.py`）：`deprecated` 节下的条目仍按 `UPDATED` 日期出现在陈旧清单里。改为跟踪一级节并跳过 `deprecated`。

## [0.1.9] - 2026-09-14

### Fixed

- **`scan-memories` 在 pi 源崩溃**（`config.py`）：`memories.sources` 键名 `pi_agent_dir` 与 `resolve_xdg_path` 的查表口径（`env.lower()` = `pi_coding_agent_dir`）不一致，`PI_CODING_AGENT_DIR` 未设时（日常终端）直接 `KeyError`。键名对齐 SCHEMA 注释声明的「键名 = env 小写」，并补两项回归测试：六源 env 与 SCHEMA 键一致性、env 未设走配置/XDG 分支。
- **lint 误报 fingerprint 字段数**（`lint.py` / `core.py`）：构建器在 tech 与 category 相同、entry 为空时按设计省略对应段，lint 却按固定 5 段判定，把合规卡片成片报成问题。现将 fingerprint 收敛为单一真相源 `core.build_fingerprint_line`，add 组装与 lint 校验共用，lint 改为逐字比对期望值。
- **`search` 裸多关键词报错**（`cli.py` / `search.py`）：位置参数改 `nargs="+"`，`agenote search a b` 不再报 `unrecognized arguments`，与 `--help` 的 `<关键词...>` 一致；多词在 `cmd_search` 内合并回单串，下游拆分逻辑不变。

### Changed

- **extract 落盘前过滤 harness 元消息**（`extract/base.py` / `core.py`）：复用 reconcile 的 `is_noise_fact`（口径统一），剔除 TodoWrite 提醒、`<task-notification>`、`<system-reminder>` 等模板消息；`NOISE_MARKERS` 补 `<task-notification>` / `<subagent-message>` / `[OUT-OF-BAND USER MESSAGE` / `Continuing toward your standing goal` 四类此前漏检的标记。实测单日 zcode 4323 条、opencode 1016 条被剔除，报告新增 `noise_filtered` 计数。

## [0.1.8] - 2026-09-12

### Added

- **type 正式性动态晋升**（`cli.py`）：移除静态 type 白名单。正式 type = 种子 6 类 ∪ 非归档卡片数达晋升阈值（`curation.type_promote_min`，默认 10）的 type；门禁按同一口径实时计算——非正式 type 写入（`add`/`update --type`）被拒绝、`--force` 逃生，持续写入满阈值后自动转正免检。

### Fixed

- **`update --tech` 同步标签与索引**（`cards.py`）：`update` 改 `--tech` / `--category` / `--owner` 后按属性重建 `:END:` 后的 fingerprint 标签行并刷新索引，修复属性与 `tags` / `agenote tags` / `fields` 漂移（此前仅 `--type` 会同步）。供策展 tech 聚拢流程使用；`--status` 保持轻量（仍由 `reindex` 刷新）。

## [0.1.7] - 2026-08-29

策展分工定型（ADR-0004）：CLI 收敛为「只读候选发现 + 原子写命令」两层，策展流程改由 agent 依据 agenote-curator skill 主导。

### Added

- **type 写入门禁**（`cli.py`）：`add` / `update` 对知识库中不存在的新 type 硬拒绝并提示已有合法集合，`--force` 逃生；替代原软警告（被各 agent 无视，非标准 type 持续增殖）。`update --type` 由仅改 `:TYPE:` 属性升级为完整重分类，同步标签行、文件名与索引，供策展 type 聚拢流程使用。
- **`scan-memories` 子命令**（`memscan`）：只读扫描 zcode / claude / codex / pi / reasonix / hermes 六源持久记忆文件，输出条目清单供策展 agent 审查后显式导入，不自动写 KB；根目录解析复用 `resolve_xdg_path`（env > config.toml > XDG > ~/），`resolve_xdg_path` 增加 section 参数接入 `memories.sources` 配置节。
- `list --json` compact 输出扩展 `status` / `last_used` / `usage_count` / `source_agent` / `file` 五字段（均为索引已有数据），供 agenote-el 总览界面做状态筛选、作者列与直开卡片文件等策展决策。
- `list --unused-days N`：降级候选的只读发现入口；`archive` 支持批量 id。

### Changed

- **BREAKING**：移除 `curate` 一键策展编排及全部自动写盘路径，策展决策（状态转换、去留、权重取舍）交由 agent 显式执行。连带移除：`review --fix`、`deduplicate --merge`、`memory --auto-archive-days` / `--auto-update`、`search --regex`、`list --cagetory`、`dream` / `distill` 的 `--dry-run`；`archive --stale` 反转为只读归档候选清单；WEIGHT 改为派生值（按 usage/新鲜度公式重算），`add` 不再写文件层 WEIGHT 属性；shim 仅保留 `health`。
- 决策记录 ADR-0004（含被拒方案：机械阶段一键 / `--apply` / 独立 reweight 命令）并同步 CONTEXT.md 术语（curate / distill / 候选清单）。

### Fixed

- omp 会话目录解析优先读 `PI_CODING_AGENT_SESSION_DIR`（顺序：`OMP_SESSIONS_DIR` > `PI_CODING_AGENT_SESSION_DIR` > config.toml > 默认值），修复 omp 18 迁移后 `reconcile` 的 omp 源一直抽不到事实的问题。

## [0.1.6] - 2026-08-24

核心设计移植自 [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian)（写入安全 / BM25 检索 / doctor 能力检测 / lint 分类报告，详见 README 致谢）。

### Added

- **写入安全层**（`safeio.py`）：变更类子命令统一持 flock 全局 KB 锁（`cli.py` 单点包锁，锁文件 `.agenote.lock`）；KB 内数据文件全部改走原子写（tmp + rename，containment 校验仅放行 KB_ROOT 内）；`add` 同秒并发撞车自动追加 ID 序号后缀。修复多 agent 并发 `add` 互相覆盖、index.json read-modify-write 丢条目、写一半崩溃留半文件三类缺陷。
- **BM25 检索**（`ranking.py`）：检索打分从子串计数升级为纯 stdlib Okapi BM25（k1/b 可配），CJK 1/2/3-gram 分词支持中英混检；两域卡片 + MEMORY + reconcile 事实统一进语料算 IDF，域权重/标题加成/短语加成/all-terms/snippet 全部保留。
- `doctor` 子命令：环境自诊断（外部工具 PATH 探测 + 缺失降级说明 + verification_reason 惯例；config.toml 未知键复用加载警告同一口径；两域 index 与磁盘卡片数一致性检查）。`--json` 供 agent 消费。
- `lint --json`：分类结构化报告（`summary.category_counts` + 条目 `{file, reason}`，分类键 format/missing_entry_type/enum_drift/fingerprint/missing_sections），修复前后可差分对比。

### Changed

- **BREAKING**：检索 `score`/`raw_score` 数值尺度变化（BM25 分），排序质量单调提升（稀有词与高频词有了 IDF 区分度）。
- **BREAKING**：配置 `weights` 节删除 `score_title_bonus` / `score_phrase_bonus`（改用 `[search]` 的 `title_boost` / `phrase_boost`，Okapi 尺度）；`score_term_hit` 保留但仅用于命中块展示排序。旧 config.toml 残留键会触发未知键警告（无害）。
- KB 侧 `.gitignore` 模板新增 `.agenote.lock` 条目（`init` 同步）。

## [0.1.5] - 2026-08-20

### Added

- 配置文件支持：`~/.config/agenote/config.toml`（TOML，遵循 XDG），84 个配置键覆盖知识库路径（`kb_root`、agent 域目录、各产物目录）、7 个 extract 源数据库路径、检索权重、策展阈值、dream/distill 启发式等；优先级为环境变量 > 配置文件 > 内置默认值，CLI 显式传参最优先。
- 新增 `agenote config` 子命令：`config init` 生成带注释的配置模板，`config show` 打印当前生效配置及来源（env / file / default）。
- GitHub Actions CI：push / PR 自动运行 pytest（Python 3.10–3.12 矩阵）。
- CHANGELOG.md 与 CONTRIBUTING.md（开发环境、Conventional Commits 规范）。

### Changed

- CLI 生成与建议的知识库 commit message 对齐 Conventional Commits：`agenote init` 初始提交改为 `chore(init): 初始化知识库`，策展提交建议改为 `chore(curate): …` / `feat(card): …` 格式。
- 全部模块常量接入配置层并收敛双源常量：去重阈值（4 处）、去重加权（2 份）、默认权重（viz 副本）、add 默认值（cards/index 两份）单源化；修复 KB_ROOT 三处独立硬编码不随配置变化的问题；hermes 源获得与其他 6 源一致的 env/config 覆盖链。

### Fixed

- `AGENOTE_AGENT` 设为纯空白时不再把空白串写入 SOURCE_AGENT（回落默认值）。
- 配置值类型错误时报键名退出，不再裸 traceback。
- lint `--fix` 写回前路径归一化并限定 `.org` 文件；viz serve 探测限定 localhost；HTML 骨架模板占位符白名单（静态审计加固）。

## [0.1.5.1] - 2026-08-20

### Added

- fish / zsh / bash 自动补全：`agenote completions <shell>` 动态生成，`completions/` 静态脚本随仓库分发（`agenote.fish` / `_agenote` / `agenote.bash`），覆盖全部 34 个子命令与常用枚举（`--domain` / `--type` / `--source` 等）；`completions.py` 为单一真相源，`completions/` 与动态输出逐字节一致。

## [0.1.2] - 2026-08-03

### Changed

- jieba 改为硬依赖，确保 `dream` 子命令开箱即用。
- 重构为 PEP 621 src layout + uv 打包。

### Removed

- 移除 `ag-ent`、`agenote-mcp`（MCP 层由 agent skills 仓库承担）。

## [0.1.0] - 2026-07-05

### Added

- 首个发布版本：卡片 CRUD、检索、记忆系统、策展、跨 agent reconcile / dream / distill、7 源对话抽取、HTML 可视化。更早的逐条变更见 git log。

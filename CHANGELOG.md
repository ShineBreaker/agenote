# Changelog

本项目的所有显著变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Fixed

- **zsh 补全的选项值列表全部失效**（`completions.py`）：`_arguments` 的 `:{a,b,c}` 写法会被 zsh 当成 shell 代码执行（实测 `command not found: aa,bb,cc`），`--source` / `--type` / `--theme` / `--domain` 的候选列表静默为空；改为 `:消息:(a b c)` 形式并重新生成 `completions/_agenote`（`test_zsh_completion_uses_separated_values` 同时锁住空格分隔形式）。
- **带 BOM 的卡片无法被策展**（`orgserde.py`）：首行 UTF-8 BOM 会让顶层标题匹配 `^\* ` 失配，`update` / `touch` / `archive` / `restore` / `merge` 一律以 `OrgPropertyDrawerError` 失败（fail-closed，不损坏数据但卡片等于只读）。现在只在标题匹配时剥离首行 BOM，写回仍使用原始行，BOM 字节保持不变。
- **`inbox-archive` 失败后留下 `ensure_dirs` 骨架文件**（`inbox_archive.py`）：事务快照原本在 `ensure_dirs` 之后读取，索引 / inbox 原本不存在时会被补建成空文件，回滚只能恢复到「空骨架」而非「不存在」。快照前移到 `ensure_dirs` 之前，并把 inbox 纳入回滚集（无论是否 `--prune`）。
- **reconcile 索引写入缺校验、读取放行非法数值**（`reconcile.py`）：`trust_score` / `weight` 增加有限性校验（NaN / Infinity 不再落盘或回读），`id` 必须以 `source` 前缀派生；顶层 `version` / `updated` / `by_source` 类型错误一律 fail-closed；`_save_reconcile_index()` 落盘前复用同一 `_valid_reconcile_fact()` 校验。
- **`reconcile --source all` 留下半更新索引**（`reconcile.py`）：非 dry-run 现在先计算所有已注册 source，全部成功后才一次性落盘并清理退役 source；任一 adapter 失败时索引保持不变。全量模式只保留本轮各注册源的新结果，退役 source 计入 `pruned`；单 source reconcile 仍只替换自己，dry-run 不落盘；单源或全量遇到 adapter 返回错误时同样 fail-closed，不覆盖 last-known-good 索引。
- **退役 source 的公共 CLI 把错误报告成成功**（`extract/base.py` / `reconcile.py` / `cli.py`）：`extract` / `reconcile --source hermes` 现在以专用 `UnknownSourceError` 退出 1、错误写 stderr 且不泄漏 traceback；adapter 内部 `ValueError` 不再被 session 级宽泛捕获吞掉。
- **`parse_org_prop` 忽略正文伪属性**（`orgserde.py` / `core.py`）：卡片元数据只读取一级标题后的顶层 `PROPERTIES` 抽屉；resolver 按真实 `:ID:` 精确匹配后回退唯一文件名片段，重复或模糊匹配返回未找到；直接路径、相对路径、符号链接和坏 UTF-8 候选均拒绝，空白/`.`/`..` selector 与 `experiences` 根符号链接也不能绕入其他域。
- **短 ID 模糊匹配可能误写派生卡片**（`core.py` / `cards.py`）：`20260924-111827` 会同时命中原卡和 `20260924-111827-2/3-*` 派生卡，旧 resolver 直接取目录遍历的第一个候选，导致 `update` / `touch` 写错卡。现在先按 Org `:ID:` 精确命中，再回退文件名片段；`get` 复用同一规则，并保留知识库外绝对路径拒绝。
- **卡片属性写入会命中正文示例**（`orgserde.py` / `core.py` / `cards.py` / `curator.py`）：`get --used`、`touch`、`update`、`archive` / `restore` 与 `merge` 现在通过顶层抽屉专用的 `set_org_prop` / `delete_org_prop` 读写，递增 `USAGE_COUNT` 不再改写正文里的 `:LAST_USED:` / `:USAGE_COUNT:` 示例。`get --used` 持 KB 锁，8 路并发不会静默丢计数。
- **shell 补全只做生成器自洽校验**（`completions.py` / `completions/*`）：删除不存在的 `curate` 候选；Bash 的 `memory --type` / `viz --theme` 改回空格分隔候选，补齐 `scan-memories --source`、Zsh 同项与全局 `--domain`；测试现在比较真实 CLI dispatch，并执行 Bash/Fish 补全与静态脚本生成结果。
- **extract / reconcile 错误路径仍留下半成品**（`extract/base.py` / `reconcile.py` / `index.py` / `core.py` / `orgserde.py`）：extract 先完成全部 source 渲染，adapter 失败不发布；发布阶段若第二个文件写入失败，会撤销本轮已写文件。reconcile（包括 dry-run 读取 LKG）严格验证已有索引，损坏索引不覆盖；`get --used` 在写卡片前验证索引形状与真实顶层 `PROPERTIES` 抽屉，失败时不输出正文、不改卡片、CLI 无 traceback。
- **批量 `merge` 后半程写失败留下部分归档**（`cards.py`）：先在内存完成全部卡片变换和索引准备；写阶段失败则恢复所有已写卡片，避免 secondary 已归档而 primary 尚未更新的半完成状态。
- **损坏的 reconcile 索引被读路径静默跳过**（`reconcile.py`）：不仅写路径，公共 `load_reconcile_facts()` 也要求每条事实符合 `ReconciledFact` 的完整序列化结构（`id`、`source`、`native_id`、`title`、`category`、`content`、数值信任权重、`tags` 与时间字段）；任一元素非法即 fail-closed，`dream` / `distill` 等消费者不再把半份事实当完整数据。
- **损坏的卡片索引被公共读路径伪装成空库**（`index.py` / `cards.py` / `curator.py` / `health.py` / `doctor.py` / `distill.py` / `viz/cli.py`）：已有 `index.json` 统一校验顶层计数与卡片条目结构，损坏时 `list` / `stats` / `health` / `doctor` / `distill` / `viz` 以 rc 1、可读 stderr、无 traceback 失败；只有索引文件不存在时才返回空骨架。
- **批量 `merge` 成功时 secondary 索引状态漂移**（`cards.py`）：合并时同时 upsert 所有 secondary 与 primary，成功输出延迟到索引提交后；提交失败会恢复卡片、索引原始字节和未误报的 stdout。
- **批量 `archive` 与 `update --category` 破坏数据完整性**（`core.py` / `cards.py` / `curator.py` / `inbox_archive.py`）：归档先解析并准备整批内容，任何 ID 无效或任一写入失败时不留下部分归档，并恢复索引原始字节；add、update 与 inbox archive 复用同一 category 边界校验，路径分隔符和 `..` 一律拒绝。

### Removed

- **退役 Hermes 旧 SQLite 记忆源的 extract/reconcile 管线**（旧 `extract/hermes.py`、`hermes_db` 与 `hermes_weight_cap` 配置项）：六个对话源继续由 adapter registry 管理；Hermes 的内置 `MEMORY.md` / `USER.md` 仍由只读 `scan-memories --source hermes` 单独扫描。历史 CHANGELOG/ADR 语境保留。

## [0.1.11] - 2026-09-22

### Changed

- **`KNOWN_AGENTS` 硬打表改为动态收录**（`core.py` / `index.py` / `cards.py`）：写死的 9 项 agent 白名单删掉，与 type 门禁（SEED_TYPES/formal_types）同构——已知 agent = `SEED_AGENTS` 种子集 ∪ index 中出现过的 `source_agent`，实时计算零持久状态。新 agent（如 dsh）首张卡给提示性警告（注明写入后自动收录），第二张起静默；无需为接入新 agent 改代码。
- **completions 枚举全部改为从真相源派生**（`completions.py`）：`CONFIG_SOURCES`/`MEMSCAN_SOURCES`/`TYPE_VALUES`/`OWNER_VALUES`/`ENTRY_VALUES`/`STATUS_VALUES` 等手抄第二份删掉，fish/bash/zsh 生成器内的内联字符串改为运行时从 extract registry、memscan registry、`core` 领域枚举拼装——新增 extract adapter 或领域枚举调整自动反映到三个 shell 的补全，不再出现补全与实际 choices 漂移。
- **`MEMORY_SECTIONS` 收敛为 `MEMORY_TYPES` 派生**（`core.py`）：`feedback/project/reference` 只在 `MEMORY_TYPES` 声明一次，`MEMORY_SECTIONS = [*MEMORY_TYPES, "deprecated"]`（deprecated 是生命周期终态语义特例）。

### Added

- 回归测试两套：`test_agent_registry.py`（首卡警告→收录→次卡静默、空 KB 种子集、归档卡写入者不失联）、`test_completions_derived.py`（枚举跟随真相源、生成脚本携带派生值）。全量 130 passed。

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

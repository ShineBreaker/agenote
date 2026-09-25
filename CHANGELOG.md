# Changelog

本项目的所有显著变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **orgfmt 中文技术文档规范阶段**（`_zh_style`，`orgfmt.py` 新增阶段 7）：把 zh-tech-doc-style-guide 的硬规则落到 Markdown 与 Org 两种格式，规则只写一份（复用已有的 `_iter_lines_with_block_state` 状态机，org block 与 ``` 围栏都识别）。自动修复 8 项——全角空格、全角标点旁多余空格、中英文之间补空格、破折号两侧空格、英文省略号转中文六点、连续感叹号、序数词「数字、」转「数字.」、数值与单位之间补空格；只报不改 4 项（的/地/得、顿号误用于分句、繁体用语、不规范缩略语）。保护：代码块、表格、drawer、org 元数据行（DEADLINE/SCHEDULED 的 repeat cookie、`:EFFORT:` 属性值）、行内公式一律不动。`orgfmt --check` 天然即 lint 门禁（退出码=问题数）。
- 测试 452 → 454（`tests/test_orgfmt_zh_style.py` 29 项：R1-R8 逐规则 + 幂等 + 保护 + L1-L4 只报不改）。

### Fixed

- `_normalize_blank_lines` 状态机漏掉文件首行：状态更新整块在 `if out:` 内，导致以 `:PROPERTIES:` 或 `#+begin_src` 开头的文件状态永不置位，drawer 内被插入空行。状态推进抽为 `_advance_blank_state()` 纯函数，首行走同一路径。
- `_classify_line` 不识别 markdown ``` 围栏：非 strict 模式下围栏内容被当 normal 段落，行间被插入空行。新增 `md_fence` 行类型与 `in_fence` 状态跟踪。
- orgfmt 与 Guix-configs `tools/doc-punct.py` 的重叠规则口径不一致（两工具交替使用时结果不同）：
  - 省略号原先一刀切转，会把 `v1.2.3`、`v2...v3` 版本号/范围误转；改为三点两侧挨数字时跳过（`so...that...` 这类句型省略两侧是单词，仍转）。
  - 中英补空格原先只跳表格行，会在 `[connection]段`、`0=disable省电`、行首标记后误插空格；逐位扫描 + `_skip_zh_latin_at()` 位置判断，与 doc-punct 同口径。
- 注：`tools/doc-punct.py`（Guix-configs）另有一批 orgfmt 未覆盖的规则——半角 `,;:?!` 转全角、括号按内容判全半角、行内代码/URL/`{{}}` 保护、`主:次` 字段不转、单个一字线连接号不碰。两者职责不同（doc-punct 服务仓库文档的半角→全角，orgfmt 服务 org 结构化格式化），不合并。

## [0.2.0.1] - 2026-09-25

对照 agent 记忆系统研究报告补齐记忆模型：项目/工作区分区隔离、受限条目不离开 SSOT、写入侧 secret 门禁与生命周期元数据。

### Added

- **`:PROJECT:` 全类型分区键**（`memory.py` / `context.py` / `projector.py` / `memory_import.py` / `memscan.py`）：memscan 提取的 `projects/<slug>` 原值经 import 落盘；`project_key_matches` 判定扁平名 / `-<16hex>` 工作区哈希后缀 / claude 消毒路径三形态 slug；context 注入与 export 投影对带分区键的 U/F/P/E/R 条目统一按当前项目门禁——无法确定项目上下文时不注入、不投影。linked git worktree 的 `.git` gitfile 解析回主仓根，worktree 与主仓共享项目身份。
- **`:SENSITIVITY:` 受限标记**：非空即不注入不投影、仅存 SSOT（`context._load_entries` 与 `projector._select_entries` 同口径排除）；`memory --add --sensitivity <LEVEL>` 落盘；`--list` / `--json` 以 sensitive 标记可见，不回显内容。
- **写入侧 secret 门禁**（`core.py` `gate_secret_write` / `warn_secret_write`）：与 import/export 共用 `SECRET_PATTERNS` 和 `[memories].secret_scan_enabled` 开关。`add` / `update --append|--stdin` / `memory --add` 直入 SSOT 命中即拒写；`inbox` 草稿捕获温和告警；`inbox-archive` 逐条跳过不阻断整批；四路均有 `--allow-secret` 显式豁免。
- **doctor `kb-secrets` 事后审计**：扫描 MEMORY.org / 卡片 / inbox 的密钥形态内容，只报类别与文件名不回显值——写侧门禁的存量兜底。
- **记忆生命周期字段**：`memory --touch` 递增 `USAGE_COUNT`；`--archive` / `--supersede` 落 `ARCHIVED_AT` / `SUPERSEDED_BY` 墓碑。

### Fixed

- `cmd_touch` 单次调用 USAGE_COUNT 计两次：默认路径连续两次 `touch_card`（LAST_USED + LAST_VERIFIED），`touch_card` 新增 `count` 参数，第二次只刷时间戳；`count=False` 调用不再消耗 session 幂等键。
- 项目作用域门禁传播：P 条目经项目索引行 PATH 命中后，`:PROJECT:` 指向索引行名字的条目可被正确放行（`_scope_gate` 纳入已命中项目身份集合）。
- 测试 404 → 425（新增 `tests/test_memory_isolation.py`：分区/敏感/secret/生命周期回归 21 项）。

## [0.2.0] - 2026-09-25

跨 agent 记忆注入架构（C 线）落地：agenote 保持纯粹 CLI，各宿主插件/hook 实时调 CLI 注入记忆简报，替代宿主自带记忆系统；含上一轮实现审查的 22 项修复闭环。

### Added

- **`agenote context` 注入简报命令**（`context.py`）：`--mode session` 会话简报（U/E/P/F/R 保头降级选集、预算硬上限恒字符、末尾固定「如何查更多」指引）与 `--mode recall` 召回（BM25 + 分数下限 8.0（真实语料 600 样本标定）+ topk + 最短 query 门槛）；三态语义——ok 首行 marker `<!-- agenote-context v1 ... -->`（无时间戳，幂等可识别），empty/disabled 输出零字节；`--project` 确定性三步匹配（精确路径 → git root basename → 不猜）；只读免锁、不进 `MUTATING_COMMANDS`、全新 KB 不写骨架。
- **`[injection]` / `[injection.hosts]` 配置节**（`config.py`）：总开关 `enabled`、默认预算、会话累计预算、召回参数与 per-host 平铺开关键；`AGENOTE_INJECTION_ENABLED=false` 一键全关，任何注入器随之静默熄火——开关的单一真相源在 agenote 侧。
- **doctor 宿主记忆六项检测**（`doctor.py`）：zcode/claude/codex/omp/hermes 五宿主自带记忆开关只读探测（未安装跳过）+「投影 targets 非空且注入开启」双通道并存警告（附退役指引）；既有 8 项检测措辞不变。
- **`injectors/` 注入器包**：zcode/claude 成品（SessionStart 简报 + UserPromptSubmit 每轮 recall；追加型三件套——指纹未变不注、recall 门槛、单会话累计预算；状态文件 `~/.cache/agenote/injectors/<host>-<sid>.json`）、codex/opencode recipe 模板（experimental/待信任确认项如实标注）、共享库 `lib.sh` 与 `selftest.sh`（30 项断言）。
- **shim `context` 子命令透传**（`shim.py`）：pi 侧 `agenote-cli` 入口支持 context（参数与主 CLI 对齐），pi 注入链路打通。
- **import 条目元数据闭环**（`memory_import.py` / `memscan.py`）：条目落盘 `:ORIGIN_PATH:`、E 类落 `:MACHINE:`（切机重验对自家产物生效）、`:NEEDS_REVIEW:` 落盘；新增 `memory --migrate` 一次性迁移命令（ORIGIN_ID 相对化 + EXPIRES_AFTER 天数化）。
- 测试 329 → 404（context 三态/预算裁剪/doctor 探测/orgserde 与 safeio 等）。

### Changed

- **注入主通道裁决**：宿主插件/hook 实时调 `agenote context` 注入为主通道（五宿主有一等挂接点），`[memories.targets]` 文件投影降级为遗留通道——宿主记忆目录是共享读写沙箱，投影会被宿主抽取代理改写（结构性双写冲突）。
- ORIGIN_ID 改为相对源根路径派生（`sha256(AGENT:相对路径:标题)[:16]`），源根改名/换机不再全量碎裂；读取侧双算兼容旧绝对路径 ID（保留一个版本）。
- EXPIRES_AFTER 改天数语义（自 UPDATED/CREATED 起算）；旧日期形态兼容判定 + 迁移警告。
- dream snapshot 未变化时清空 candidates 并指回游标（`unchanged` 与「无候选」可区分，消费方不再重复复核）。
- `memory --add --type P` 合一到 MEMORY.org 单一写路径（侧文件路径退役）；secret 扫描清单合一到 `core.SECRET_PATTERNS`；聚合投影文件去日期戳（跨天字节级幂等）；时效口径统一（VALIDATED_AT → UPDATED → CREATED）。
- KB 标题收集合一到 `orgserde.collect_card_titles`（BOM 剥离）。

### Fixed

- **回声防护闭环（P0）**：import 侧消费投影 marker（`x-agenote-projected` / `x-agenote-pointer`）拒收自身投影，targets 前缀排除改 realpath 归一化（symlink/大小写免疫）——清空 targets 配置后投影副本不再被读回 MEMORY.org 造成递归污染。
- import 的 BOM/CRLF 归一化：CRLF 源文件不再把 `\r` 字节写入 MEMORY.org、frontmatter 不再整段退化。
- E 类切机重验失效（import 产物缺 `:MACHINE:` 导致 `--revalidate` 恒空）；孤儿检测接通（ORIGIN_PATH 落盘后 supersede 孤儿与源消失可检出）。
- reasonix 投影漂移改 marker-hash 比对（宿主改写只报告不覆盖）；陈旧投影自洽才清理。
- dedupe 候选排除 deprecated 终态条目；`memory_import` 读取收口 `safe_read`（lstat/fstat 统一防护）；safeio 读侧补 lstat↔fd 身份比对。
- el 契约测试锁定 `list --json` 完整旧 12 字段集；S5 errors>0 拒绝落盘补回归；sweep `--apply` 候选缺失路径先备后写锁。

## [0.1.12] - 2026-09-25

### Added

- **KB 锁超时可配置**（`config.py` / `safeio.py`）：新增 `safeio.lock_timeout_seconds` 配置键（默认 10s，env `AGENOTE_LOCK_TIMEOUT_SECONDS`，非数值或不大于 0 回落默认），`kb_lock` 显式传参优先于配置值。
- 回归测试 38 个（全量 214 → 252）：BOM title 进索引、重命名回滚双失败保副本、部分安装 extract/reconcile、bash 全局选项后补子命令、orgfmt 失败报告、viz serve 容错、属性值换行拒绝、锁超时配置化、crush 路径覆盖判定等。

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
- **BOM 卡片 title 全链路解析**（`orgserde.py` / `reconcile.py`）：`read_org_title` 与 `_kb_titles` 对首行 BOM 容错，BOM 卡片 `touch` / 策展后 index 与 reconcile 去重不再落到 `unknown`，此前的 BOM 修复从属性抽屉补全到标题链路。
- **`cmd_update` 重命名回滚可能双删卡片**（`cards.py`）：索引写失败且卡片恢复写也失败时，不再删除改名后的新副本（至少一个副本存活），失败明细聚合后仍 fail-loud 抛出。
- **未安装源打穿 `extract --source all` / `reconcile`**（`extract/*` / `reconcile.py`）：源数据不存在降级为 `[skip]` 报告项，不再计为失败、不再阻塞整批落盘；真实错误（坏库、解析失败）仍 fail-closed 整批拒绝。源 0 facts 不再视为错误，清空源数据后可正常更新索引。
- **bash 补全在全局选项后无法补全子命令**（`completions.py` / `completions/agenote.bash`）：`agenote --version <TAB>`、`agenote --domain human <TAB>` 恢复子命令候选；fish 补齐 `reconcile` / `extract` 的 `--dry-run`，zsh `get` 分支补齐 `-h/--help`。
- **`orgfmt` 失败时吞掉已处理文件的报告**（`orgfmt.py` / `orgfmt_cli.py`）：失败退出码保留，但先打印已完成文件的报告与汇总再退出。
- **`viz --serve` 浏览器打开失败杀死服务器**（`viz/cli.py`）：`xdg-open` 失败降级为 stderr 警告，访问 URL 无条件打印，服务器继续运行。
- **属性值注入防护与错误消息失实**（`orgserde.py` / `core.py` / `cli.py` / `extract/*`）：`set_org_prop` 拒绝含换行的值（防伪 `:END:` 抽屉注入），category 校验同步拒绝换行；KB 锁超时的可操作提示完整透传到 stderr；git 失败消息不再虚构异常类型名；claude / codex / omp 未安装消息不再携带完整本地路径；`safe_adapter_error` 对字符串输入不再虚构「Exception」表述；crush 单 session 错误补齐缺失导入。
- **crush 全局库判定不遵守配置覆盖**（`extract/crush.py`）：由 `.config/crush` 字符串包含改为与 `CRUSH_GLOBAL_DB` 配置值路径比对，覆盖 `crush_global_db` 后不再误判为项目库。
- **extract 发布写盘绕过原子写原语**（`extract/base.py` / `safeio.py`）：KB 内输出路径改走 `atomic_write`（tmp→rename），显式 `--output-dir` 的 KB 外路径保留直接写；批级回滚语义不变。

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

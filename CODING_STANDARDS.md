# CODING_STANDARDS.md — agenote 各分支改码细则

AGENTS.md 是入口，本文件是它每条指针的目标。**按分支分节，只读你正在做的那条。**
分支清单与触发条件见 [AGENTS.md §5](AGENTS.md#5-分支路由)。

## agenote

Python CLI，唯一实现层。一切落盘的逻辑只在这里，宿主插件不重复实现。

- **新增写命令**必须同时登记到 `cli.py` 的 `commands` dict **和** `MUTATING_COMMANDS`
  set；漏掉后者则该命令不持锁。只读命令（如 `context`）不进 `MUTATING_COMMANDS`。
  `MUTATING_COMMANDS` 当前成员见 `cli.py:1315`。**这条没有测试守护**——`packages/agenote/tests/`
  里没有任何用例断言它，漏登记不会红。
- **卡片状态机**共 4 态：`done / stable / stale / archived`。Org 标题 keyword 恒为
  `* DONE`，真实状态存于 `:STATUS:` 属性——标题与属性不是一回事。
- **双域模型**：`--domain human|agenote`；搜索默认跨域。agent 域卡片权重更低，
  见 `CONTEXT.md` 的 KBContext。
- **`extract/` 只读**：适配器直接读其他 agent 的 SQLite/JSONL，不产生写入。
  `reconcile` 写的是只读索引 `.reconcile/index.json`，不进权威 `experiences/`。
- **测试按主题分文件**：`test_dream_reconcile.py` / `test_distill_curator.py` /
  `test_health_*.py` / `test_viz_serve.py` / `test_memory_*.py` 等，新增命令先把测试放到
  对应主题文件。主门禁是 `uv run pytest -q`。

## agenote-el

Emacs 插件（ELPA）。所有文件系统操作委托给 `agenote` CLI。改 Emacs 端前先确认对应
CLI 子命令的行为；反过来改 CLI 时先读下面的脆弱耦合。

- **内部铁律**：CLI 调用必须走 `agenote-call` / `agenote-call-async`；每次调用重新
  `executable-find "agenote"`，不缓存路径；始终显式传 `--domain`；返回值是 plist
  `(:status :stdout :stderr :command :domain)`，`:status` 按退出码判断成败。

### 脆弱耦合：改 CLI 输出前必读

CLI 的输出形状被两条测试钉死，**改这些命令的措辞或字段会静默破坏 Emacs 端显示**：

| 命令 | 耦合方式 | 守护测试 |
|---|---|---|
| `list --json` | 必须含 `:id :title :category :created` | `packages/agenote/tests/test_el_contract.py` |
| `stats` / `health` / `review` / `get` / `memory` | 自由文本**原样渲染**，措辞即契约 | `packages/agenote/tests/test_contracts_text.py` 的 golden 快照（`tests/contracts/*.txt`） |
| `inbox-archive --stdin` | 吃 `[{"heading":..,"body":..}]` | `test_el_contract.py` |

确认是有意的展示层调整后，**先同步 elisp 侧**，再重生快照：

```bash
cd packages/agenote && AGENOTE_CONTRACT_UPDATE=1 uv run pytest tests/test_contracts_text.py
```

## 宿主适配

四个插件（zcode / hermes / pi / dsh）职责**仅限**「事件触发 + 命令快捷入口」。
行为规范由 `agenote-skills` 定义，知识库逻辑由 CLI 实现——两者都不在插件里重复。
两处需联动修改时放进同一次 monorepo 提交，这是合仓的主要收益。

- **`agenote-pi`**：单文件 TypeScript。omp 运行时自动扫描加载，无需构建、无需在
  `config.yml` 注册；`ExtensionAPI` 是全局类型无需 import。只读 CLI 查询走
  `agenote-cli` shim。
- **`dsh-agenote`**：本包自带 [`packages/dsh-agenote/AGENTS.md`](packages/dsh-agenote/AGENTS.md)，
  **以那份为准**——它写了 `runKb` 收敛、投递二分、状态二分等更细的规则，以及本包
  独有的冷启动验证与 peer cohort 约束。本节只留一条跨包禁令：**不 import
  `@deepseek-ai/*`**（`link:` 部署下解析不到 `$DSH_HOME/profiles/node_modules`）；需要
  上游纯函数就在 `lib.js` 复刻并注明出处。配置默认值单一真相源是导出的 `Config`
  schema，`apply()` 不做二次默认值或静默降级。
- **追加型注入器**（zcode / claude / codex / hermes）共用三件套防上下文膨胀：
  指纹未变不重注、recall 门槛、单会话累计预算。
- **重写型**（opencode / pi）每请求幂等重写，只要简报预算。
- 预算与阈值都在 `spec/injection.toml`，见 [§spec](#spec)。

## spec

`spec/injection.toml` 是跨组件语义的唯一真相源（完成信号、时序阈值、每宿主预算）。
刻意不放 CLI 自身配置项（真相源是 `SCHEMA`），也不放宿主适配胶水逻辑。

三条硬约束：

1. **生成物提交进仓。** 宿主插件是独立安装的（`~/.zcode/plugins/`、omp 扩展目录、
   hermes 插件目录），运行时不能依赖 monorepo 存在。漂移由 CI 拦截，不靠开发纪律。
2. **生成块必须带 BEGIN/END 标记**，工具只改写两标记之间的内容。所以生成块可以嵌在
   文件中间，不要求整文件模板化。
3. **不允许未登记的副本。** `check.py` 扫出任何自行定义 `COMPLETION_SIGNALS` 却没有
   生成块标记的文件并报错。

第 3 条是纪律失效过的实证：信号清单曾逐字复制在 4 处，靠人工同步已经漏过一次——
`agenote-zcode/hooks/hooks.json` 的预筛正则漏了 `完成`，该信号在 zcode 侧从未生效。
`check.py` 现在按覆盖度校验，但**为什么 spec 存在**没有任何机器会自述。**新开一份
副本前先想清楚它为什么不该进 spec。**

```bash
python3 tools/codegen/generate.py           # 把常量块写进各包（幂等）
python3 tools/codegen/generate.py --check   # 只校验漂移
python3 tools/codegen/check.py              # 校验 JSON 正则 / Markdown 散文 / 副本清点
```

## agenote-skills

`agenote-base` / `agenote-curator` / `agenote-review` 是**行为规范的载体**：写入流程、
卡片格式、策展策略写在这里，改策略**不该发版**。

- 本仓 `.agents/skills/` 是**项目级**副本（随 git 版本化，`skills-lock.json` 记录
  hash），cwd 在本仓内时优先加载它；`~/.agents/skills/` 是全局副本。上游有更新时同步。
- 来自 `mattpocock/skills` 的 skill **保持上游原样**。定制只写进
  `packages/agenote-skills/` 或本仓文档；直接改项目级副本会在同步时被覆盖。
- 改本文件或任何 skill 时用 **writing-for-agents** skill。

## commit

一律遵守 `~/.config/git/gitmessage`：`type(scope): 简短描述`。单文件改动 scope 用
文件名，多文件用组件名；描述动词开头、小写、无句号；body 只解释**为什么**；不兼容
变更 type 后加 `!`。提交前 `git status --short` 确认只含本次相关文件。

**LLM 参与的提交按实际宿主与模型附 `Co-authored-by`**——宿主与模型的对应写法见
[CONTRIBUTING.md §LLM 署名](CONTRIBUTING.md#llm-署名)，那里是这张表的唯一真相源。
`gitmessage` 里只有 type 清单，没有署名约定，别指望它会自述。

开发流程本身由另一套 skill 规定（来源 `mattpocock/skills`）：**tdd** 先行、提交前
**code-review** 自查（Standards 轴即 AGENTS.md + CHANGELOG 口径）；疑难 bug 走
**diagnosing-bugs**；架构评审走 **improve-codebase-architecture**（领域术语进
`CONTEXT.md`，决策进 `docs/adr/`）；写改任何 skill 或 AGENTS.md 用
**writing-for-agents**。

## 发版

**tag 前缀即包名**：一个提交可携带多个 tag，每个 tag 只触发自己那条链路，组件版本
互不牵制。新发版一律 `<组件名>-v<version>`。

| 组件 | 渠道 | 版本声明位置 |
|---|---|---|
| `agenote` | PyPI + GitHub Release | `packages/agenote/pyproject.toml` |
| `agenote-el` | ELPA tar + Release | `packages/agenote-el/agenote-pkg.el` 的 `define-package` |
| `dsh-agenote` | npm + Release | `packages/dsh-agenote/package.json` |
| `agenote-zcode` | tar.gz + Release | `packages/agenote-zcode/.zcode-plugin/plugin.json`（无 VERSION 文件） |
| `agenote-pi` / `agenote-hermes` / `agenote-skills` | tar.gz + Release | 各包 `VERSION` 文件 |

流程：

1. 改版本（包内版本声明处）
2. **更新该包的 `CHANGELOG.md`**（Keep a Changelog 格式 + 下方「release 文案结构」，
   新增 `## [X.Y.Z] - 日期` 段；漏记需事后补）
3. 提交
4. annotated tag 打在 release commit 上 → `git log --oneline -1 <tag>` 核对指向
5. `git push origin main <pkg>-vX.Y.Z`（main 与 tag **一并**推送）
6. push 后 `git ls-remote --tags origin | grep <tag>` 自查远端 tag 确实存在

### release 文案结构

GitHub Release 的正文就是该版本 CHANGELOG 段落——`build.py` 的 `release_notes()`
原样抽取，workflow 用 `--notes-file` 发布。所以段落结构按访客第一屏设计：

```markdown
## [X.Y.Z] - 日期

<一两句营销短段：这次发布给用户带来了什么。面向使用者，不展开实现>

---

### Added / Fixed / Changed

- 大概做了什么（abc1234）
- 大概做了什么（def5678）
```

- **首段只做营销**：一两句话概括本次全部改动的价值，读完知道"值不值得升级"。
- **分割线之后是正文**：每条一句话讲做了什么；细节**不描述**，直接引用对应
  commit 短 hash（GitHub 自动渲染成链接，点进去看根因与修法）。
- Keep a Changelog 分类小节保留。2026-10 起的新段落按此结构，历史段落不回改。

### tag 陷阱处置

两个 tag 陷阱的**症状**写在 [AGENTS.md §4 静默失败](AGENTS.md#4-静默失败)（它们必须在
动手发版前就看见）。这里是处置细节：

- **tag 漏推**：`release.yml` 认 `on: push: tags`，漏推时远端无 tag 事件、workflow 根本
  不跑，而本地 tag / commit / CHANGELOG 一切正常。该坑已连续复发多次。
- **一次 push 超过 3 个 tag**：GitHub 官方限制，超过时不产生任何 push 事件、workflow
  全部不触发（tag 本身推成功了）。多组件发版分批推（每批 ≤3），或逐个推。已中招时
  **无需动 tag**，用 workflow_dispatch 回填：
  `gh workflow run release.yml -f tag=<pkg>-v<ver>`。
- **悬空 tag**：本地 tag 可能因 amend/rebase 残留指向悬空提交，
  `git branch -a --contains <tag>` 无输出即为悬空，删了重打。远端 tag 以
  `git ls-remote --tags origin` 为准，不要只看本地 `git tag -l`。

预构建校验（不推 tag）：

```bash
python3 tools/release/build.py --list                              # 各组件版本
python3 tools/release/build.py --component agenote --tag agenote-v0.3.0
```

## dotfiles

`Guix-configs` **不用 submodule 消费本仓**，改用钉版本的拉取脚本
（`dotfiles/mutable/agenote/{sync-agenote.sh,agenote.lock}`）：

- `agenote.lock` 每行 `包:版本:sha256`。**版本变更就是改这一行**——纯文本 diff 友好、
  天然原子、不可能漏推。
- `sync-agenote.sh` 拉取钉版本 tag 的产物、逐个校验 sha256、解包到内容寻址缓存
  （`~/.cache/agenote/packages/<包>/<版本>/`，多版本共存），再逐文件铺相对 symlink
  到四个宿主目录：`~/.config/agents/skills`、
  `~/.config/omp/extensions/agenote-hooks`、`~/.zcode/plugins/agenote-zcode`、
  `~/.local/share/hermes/plugins/agenote`。
- CLI / elisp / dsh 不由该脚本部署，分别走 `uv tool install` / `package.el` /
  `npm install`。

**改了本仓的插件代码 ≠ dotfiles 侧生效。** 生效路径：合仓提交 → 打 `<pkg>-v*` tag →
推上去（tag 一并）→ 拿 `dist/<pkg>/SHA256SUMS` 里的值 → 改 `agenote.lock` 对应行 →
跑 `sync-agenote.sh --dry-run` 确认 → 实跑。

脚本的三个刻意设计：两段式（全部下载校验通过才动任何链接）、拒写非 symlink（目标有
真实文件即失败，不替用户决定丢弃）、manifest 精确回滚（`--uninstall` 只删仍指向本
脚本记录的源的链接）。

## 验证

按改动分支跑；下面是全量（与 `.github/workflows/ci.yml` 的 5 个 job 对应）。

```bash
# CLI：单元测试 + 契约测试 + 注入器离线自测（实测 30 项断言）
cd packages/agenote && uv sync --extra test && uv run pytest -q
bash packages/agenote/injectors/selftest.sh

# 跨组件契约与漂移
python3 tools/codegen/generate.py --check && python3 tools/codegen/check.py

# 各宿主适配器语法
node --check packages/agenote-zcode/hooks/*.mjs
cp packages/dsh-agenote/hooks.js /tmp/dsh-hooks.mjs && node --check /tmp/dsh-hooks.mjs
python3 -m py_compile packages/agenote-hermes/__init__.py
cd packages/agenote-el && emacs -Q --batch -L . -l package -f batch-byte-compile *.el

# 7 个组件产物可构建（发版前必跑）
for c in $(python3 tools/release/build.py --list | awk '{print $1}'); do
  python3 tools/release/build.py --component "$c"; done
```

`selftest.sh` 依赖 `.venv` 里的 `agenote`（spawn 真 CLI）；zcode / hermes 的实装直接
取本仓 `packages/` 下的包源码。dsh 是 `"type": "module"`，`node --check` 按 CJS 解析会
误报 `export`，故复制成 `.mjs` 再检。`agenote-el` 无自动化测试，字节编译兜底。
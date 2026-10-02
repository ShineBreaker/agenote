# AGENTS.md — agenote monorepo

跨 Agent 经验平台。**单仓 7 个组件**，布局与发布约定见
[`docs/adr/0005`](docs/adr/0005-monorepo-unify-seven-components.md)。

本文件是本仓规范的第一入口。改代码前先读对应小节，别靠猜。

---

## 1. 布局

```
ShineBreaker/agenote/            ← monorepo 根（沿用原 CLI 仓，14 个历史 tag 保留）
├── packages/
│   ├── agenote/                  Python CLI（PyPI: agenote），唯一实现层
│   ├── agenote-el/               Emacs 插件（ELPA），纯 CLI 适配层
│   ├── dsh-agenote/              DSH cordis bundle（npm: dsh-agenote）
│   ├── agenote-pi/               omp/pi 单文件 TS 扩展
│   ├── agenote-zcode/            ZCode 插件
│   ├── agenote-hermes/           hermes Python 插件
│   └── agenote-skills/           3 个 agent skill（base / curator / review）
├── spec/                         跨组件语义唯一真相源（见 §3）
├── tools/
│   ├── codegen/                  生成器 + 契约校验器
│   └── release/build.py          7 个组件的产物构建
├── docs/adr/                     跨组件 ADR（0005 起）
└── CONTEXT.md                    领域术语
```

**包目录不重命名。** 名字已出现在 URL、插件名、import 路径与用户文档中。

**不在本仓**：`~/Documents/Org`（知识库本体，CLI 的 `KB_ROOT`）是独立 git 仓；
`Guix-configs`（dotfiles）通过锁文件消费本仓的发布产物（见 §6）。

---

## 2. 单一真相源分层

改动前先判断「我改的是哪一层」。跨层放错位置是本仓最常见的走偏方式。

| 语义 | 真相源 | 判据 |
|---|---|---|
| 跨宿主复制的信号 / 预算 / 时序 | `spec/injection.toml` | 在 2 个以上包里出现 |
| CLI 的 84 个配置键 | `packages/agenote/src/agenote/config.py` 的 `SCHEMA` | 只有 CLI 读 |
| 各插件的 Config schema 默认值 | 各包 `Config` 声明 | 随宿主加载期校验而异 |
| 行为规范（写入流程 / 卡片格式 / 策展策略） | `packages/agenote-skills/*/SKILL.md` | 文档即载体，改策略不该发版 |
| 卡片状态机 / 领域术语 | `CONTEXT.md` + 各包 `docs/adr/` | |

判据一句话：**只有一处用 → 留在代码里写注释；两处以上用 → 进 `spec/`。**

`spec` 里有且只有一处刻意的**镜像值**：`[append].min_query = 6` 对应 CLI 的
`recall_min_query`。两边仅通过 env（`AGENOTE_INJECTION_MIN_QUERY`）联动，改
`config.toml` 不同步注入器；**CLI 侧始终是最终裁决**，注入器那道门槛只为省一次
spawn。不要试图「顺手统一」这两处。

---

## 3. spec 与代码生成

```bash
python3 tools/codegen/generate.py           # 把常量块写进各包（幂等）
python3 tools/codegen/generate.py --check   # 只校验漂移（CI 用）
python3 tools/codegen/check.py              # 校验 JSON 正则 / Markdown 散文 / 副本清点
```

三条硬约束：

1. **生成物提交进仓。** 宿主插件是独立安装的（`~/.zcode/plugins/`、omp 扩展
   目录、hermes 插件目录），运行时不能依赖 monorepo 存在。漂移由 CI 拦截，
   不靠开发纪律。
2. **生成块必须带 BEGIN/END 标记**，工具只改写两标记之间的内容。所以生成块
   可以嵌在文件中间，不要求整文件模板化。
3. **不允许出现未登记的副本。** `check.py` 会扫出任何自行定义
   `COMPLETION_SIGNALS` 却没有生成块标记的文件并报错。

前两任插件的 `COMPLETION_SIGNALS` 曾逐字复制在 4 处，纪律失效过一次实证：
`agenote-zcode/hooks/hooks.json` 的预筛正则漏了 `完成`，该信号在 zcode 侧实际
从未生效。**新开第五份副本前先想清楚为什么不进 spec。**

---

## 4. 改动各包时的硬边界

### 通用

- 一切落盘走 `safeio.atomic_write` + `kb_lock`（fcntl.flock，10s 超时）。
- 配置只能改 `config.py` 的 `SCHEMA`，不要在其他模块散落读取配置。
- 跑本仓 CLI 时环境变量必须带前缀：`AGENOTE_AGENT=zcode agenote ...`，
  否则卡片归因会错误落到默认 agent（`omp`）。该变量只打标签，不做写入隔离。

### `agenote`（CLI，唯一实现层）

- 新增写命令必须在 `cli.py` **同时**登记到 `commands` dict **和**
  `MUTATING_COMMANDS` set，漏掉后者则该命令不持锁。
- 卡片状态机共 4 态：`done / stable / stale / archived`。Org 标题 keyword 恒为
  `* DONE`，真实状态存于 `:STATUS:` 属性，二者不要混淆。
- 双域模型：`--domain human|agenote`；搜索默认跨域。
- `extract/` 下的适配器直接读其他 agent 的 SQLite/JSONL，**只读**，不要在里面
  产生写入。
- 测试盲区：`dream` / `distill` / `reconcile` / `health` / `memory` / `viz` /
  `inbox_archive` 无直接单测，改动需手动验证或补测。

```bash
cd packages/agenote && uv sync --extra test && uv run pytest -q
```

### `agenote-el`（Emacs）

- 所有文件系统操作委托给 `agenote` CLI，改 Emacs 端前先确认对应 CLI 子命令的
  行为；反过来改 CLI 时注意下面两条契约。
- 内部铁律：CLI 调用必须走 `agenote-call` / `agenote-call-async`；每次调用重新
  `executable-find "agenote"` 不缓存路径；始终显式传 `--domain`，绝不依赖 CLI
  默认值；返回值是 plist `(:status :stdout :stderr :command :domain)`，`:status`
  按退出码判断成败。
- **脆弱耦合**：`list` 必须 `--json` 且含 `:id :title :category :created`；
  `stats` / `health` / `review` / `get` / `memory` 按**自由文本原样渲染**，CLI 改
  措辞会静默破坏显示；`inbox-archive --stdin` 吃 `[{"heading":..,"body":..}]`。
  这两类耦合都有测试守护：字段形状在
  `packages/agenote/tests/test_el_contract.py`，逐字文案在
  `tests/test_contracts_text.py` 的 golden 快照。**改这些命令的输出前先看那两处**
  ——确认是有意的展示层调整后，先同步 elisp 侧，再用
  `AGENOTE_CONTRACT_UPDATE=1 uv run pytest tests/test_contracts_text.py` 重生快照。

### `agenote-pi` / `dsh-agenote` / `agenote-zcode` / `agenote-hermes`（宿主适配）

职责**仅限**「事件触发 + 命令快捷入口」。行为规范由 `agenote-skills` 定义，这
四个插件不重复实现知识库逻辑。两处需联动修改时注意分别在各自目录提交（同一次
monorepo 提交即可，这是合仓的主要收益）。

- `agenote-pi`：单文件 TypeScript，omp 运行时自动扫描加载、无需构建、无需在
  config.yml 注册；`ExtensionAPI` 是全局类型无需 import。只读 CLI 查询走
  `agenote-cli` shim。
- `dsh-agenote`：**不 import `@deepseek-ai/*`**（`link:` 部署下解析不到
  `$DSH_HOME/profiles/node_modules`）。需要上游纯函数就在 `lib.js` 复刻并注明
  出处。配置默认值单一真相源是导出的 `Config` schema（Standard Schema v1 手搓子
  集），`apply()` **不做**二次默认值或静默降级。
- 追加型注入器（zcode / claude / codex / hermes）共用三件套防上下文膨胀：指纹未
  变不重注、recall 门槛、单会话累计预算。重写型（opencode / pi）每请求幂等重写。
  预算与阈值都在 `spec/injection.toml`。

---

## 5. Commit 规范

一律遵守 `~/.config/git/gitmessage`：`type(scope): 简短描述`，单文件改动 scope
用文件名，多文件用组件名；描述动词开头、小写、无句号；body 只解释**为什么**；
不兼容变更 type 后加 `!`。

LLM 参与的提交按**实际宿主与模型**附加 `Co-authored-by`，多个作者各占一行：

| 宿主 | 写法 |
|---|---|
| minimax Code | `minimax Code (MiniMax-M3.1-Flash-Preview) <noreply@minimax.io>` |
| ZCode | `ZCode (GLM-5.3) <noreply@z.ai>` |
| DSH | `DeepSeek (step-5-preview) <noreply@deepseek.com>` |
| Hermes | `Hermes (<model>) <noreply@nousresearch.com>` |

提交前 `git status --short` 确认只含本次相关文件。

开发流程本身由另一套 skill 规定（来源 `mattpocock/skills`）：**tdd** 先行、
提交前 **code-review** 自查（Standards 轴即本文件 + CHANGELOG 口径）；疑难 bug
走 **diagnosing-bugs**；架构评审走 **improve-codebase-architecture**（领域术语进
`CONTEXT.md`，决策进 `docs/adr/`）；写改任何 skill 或本文件用
**writing-for-agents**。

本仓 `.agents/skills/` 是**项目级**副本（随 git 版本化），cwd 在本仓内时优先加载
它；`~/.agents/skills/` 是全局副本。**matt skill 保持上游原样，定制只写进
`packages/agenote-skills/` 或本文件**，更新才不冲突。每月至少同步一次。

---

## 6. 发布

**tag 前缀即包名**：一个提交可携带多个 tag，每个 tag 只触发自己那条链路，组件
版本互不牵制。

| 组件 | tag 示例 | 渠道 | 版本声明位置 |
|---|---|---|---|
| `agenote` | `agenote-v0.3.0` | PyPI + GitHub Release | `pyproject.toml` |
| `agenote-el` | `agenote-el-v0.1.0` | ELPA tar + Release | `agenote-pkg.el` 的 `define-package` |
| `dsh-agenote` | `dsh-agenote-v0.2.0` | npm + Release | `package.json` |
| `agenote-pi` / `agenote-zcode` / `agenote-hermes` / `agenote-skills` | `<pkg>-v0.1.0` | tar.gz + Release | `VERSION` 文件 |

流程：

1. 改版本（包内版本声明处）
2. **更新该包的 `CHANGELOG.md`**（Keep a Changelog 格式，新增 `## [X.Y.Z] - 日期`
   段；漏记需事后补）
3. 提交
4. annotated tag 打在 release commit 上 → `git log --oneline -1 <tag>` 核对指向
5. `git push origin main <pkg>-vX.Y.Z`（main 与 tag **一并**推送）
6. push 后 `git ls-remote --tags origin | grep <tag>` 自查远端 tag 确实存在

### ⚠️ tag 漏推 = GitHub 无 Release

`release.yml` 认 `on: push: tags`，漏推 tag 时远端无 tag 事件、workflow 根本不
跑，而**本地 tag / commit / CHANGELOG 一切正常、极具迷惑性**。该坑在 tag 漏推
上已连续复发多次。**每次发版必做第 6 步。**

本地 tag 可能因 amend/rebase 残留指向悬空提交（`git branch -a --contains <tag>`
无输出即为悬空）。远端 tag 以 `git ls-remote --tags origin` 为准，不要只看本地
`git tag -l`。

预构建校验（不推 tag）：

```bash
python3 tools/release/build.py --list                              # 各组件版本
python3 tools/release/build.py --component agenote --tag agenote-v0.3.0   # 校验 tag↔版本 + 产物
```

---

## 7. dotfiles 侧如何消费

`Guix-configs` **不用 submodule 消费本仓**，改用钉版本的拉取脚本
（`dotfiles/mutable/agenote/{sync-agenote.sh,agenote.lock}`）：

- `agenote.lock` 每行 `包:版本:sha256`。**版本变更就是改这一行**——纯文本 diff
  友好、天然原子、不可能漏推。
- `sync-agenote.sh` 拉取钉版本 tag 的产物、逐个校验 sha256、解包到内容寻址缓存
  （`~/.cache/agenote/packages/<包>/<版本>/`，多版本共存），再逐文件铺相对
  symlink 到四个宿主目录：`~/.config/agents/skills`、
  `~/.config/omp/extensions/agenote-hooks`、`~/.zcode/plugins/agenote-zcode`、
  `~/.local/share/hermes/plugins/agenote`。
- CLI / elisp / dsh 不由该脚本部署，分别走 `uv tool install` / `package.el` /
  `npm install`。

**所以：改了本仓的插件代码 ≠ dotfiles 侧生效。** 生效路径是：合仓提交 → 打
`<pkg>-v*` tag → 推上去（tag 一并）→ 拿 `dist/<pkg>/SHA256SUMS` 里的值 → 改
`agenote.lock` 对应行 → 跑 `sync-agenote.sh --dry-run` 确认 → 实跑。

脚本的三个刻意设计：两段式（全部下载校验通过才动任何链接）、拒写非 symlink
（目标有真实文件即失败，不替用户决定丢弃）、manifest 精确回滚
（`--uninstall` 只删仍指向本脚本记录的源的链接）。

---

## 8. 验证

```bash
# 全量本地验证（与 CI 的 5 个 job 对应）
cd packages/agenote && uv sync --extra test && uv run pytest -q   # CLI
bash packages/agenote/injectors/selftest.sh                        # 注入器 30 项离线断言
python3 tools/codegen/generate.py --check && python3 tools/codegen/check.py
node --check packages/agenote-zcode/hooks/*.mjs                    # 适配器语法
python3 -m py_compile packages/agenote-hermes/__init__.py
for c in $(python3 tools/release/build.py --list | awk '{print $1}'); do
  python3 tools/release/build.py --component "$c"; done            # 7 个组件产物可构建
```

无自动化测试的只有 `agenote-el`，靠 `emacs -Q --batch -L . -l package
-f batch-byte-compile *.el` 字节编译兜底（CI 的 `adapters` job 有跑）。

改适配器时**顺手跑一遍对应语言的语法检查**——本次 codegen 过程中真实踩到过两个
只有语法检查能抓的错误（Python 列表漏尾逗号、ESM 里丢 `const`）。

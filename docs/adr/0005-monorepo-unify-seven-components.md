# monorepo 化：7 组件合仓 + spec 单一真相源 + 钉版本拉取

**Status**: accepted

agenote 生态此前是 7 个彼此独立的交付单元，散在 5 个 GitHub 仓、1 份 Guix-configs
dotfiles 裸目录里，部署走 3 套互不相同的机制。决定合并为单仓
`ShineBreaker/agenote`（沿用 CLI 仓身份、14 个历史 tag 与 release CI），`packages/`
下平铺 7 个组件；跨组件的重复语义收敛到 `spec/` 单一真相源并代码生成；发布按组件
tag 前缀从单仓抽取四条渠道；dotfiles 侧改用「钉版本 tag 的拉取脚本」，彻底废除
submodule 指针与裸目录。

本 ADR 记的是 2026-10-03 的决策。0001–0004 是 CLI 内部的架构决策，留在
`packages/agenote/docs/adr/`；本文件及后续记的是跨组件决策，编号延续同一序列。

## Why

现状的代价不是「仓库多」，而是**一个逻辑变更要跨 4–5 次不可原子回滚的操作**。
三个硬证据：

- **注入器核心被强制复制成 4 份语言**。追加型三件套（指纹缓存 + recall 门槛 +
  单会话累计预算）在 `injectors/lib.sh`（bash，给 claude/codex）、
  `agenote-zcode/hooks/lib.mjs`（JS）、`agenote-pi/index.ts`（TS）、
  hermes 插件 `__init__.py`（Python）各实现一遍。`lib.mjs:12` 自己写着「与 agenote
  仓库 injectors/lib.sh 及 hermes 插件同构，**改语义需跨文件同步**」。完成信号清单
  同理存 4 份（`dsh-agenote/hooks.js` 数组、`agenote-zcode/hooks/hooks.json` 正则、
  skills 的 SKILL.md 散文、pi 的 TS）。这是 bash 库无法被 Python 插件 import 的
  结构性后果，靠纪律无法长期守住。
- **版本列车的耦合已经漂移**。CLI 在 `0.2.0.3`，`dsh-agenote` 的
  `peerDependencies` / `engines` 仍钉 `0.2.0-rc.2`。手工维护的跨仓版本约束不会
  自我维持——这正是需要机器保障的信号。
- **submodule 闭环本身是缺陷源**。`pi` 与 `skills` 是 Guix-configs 的 submodule，
  推进要三步：子模块 `push HEAD:main` → 父仓库 bump 两个指针 → push 父仓库。漏第三
  步时本地一切正常、远端指针仍是旧值，症状极隐蔽（`AGENTS.md` 专门记录了两次
  同类事故：v0.1.6 的悬空 tag、v0.2.0.2 的漏推 tag）。

还有两个**结构性事实**让「每个组件一个仓」无法自洽：

- `agenote-zcode` 与 hermes 插件**没有任何 git 边界**，只是 Guix-configs 里的裸目录。
  它们没有独立历史、别人无法安装、CI 零覆盖。`agenote/injectors/zcode/` 与
  `injectors/hermes/` 两个 README 槽位就是指向它们的文档约定——依赖被写进了文档，
  但没有任何机制强制它成立。
- 三个组件的发布成熟度差 3 个档：CLI 有 tag + CI + CHANGELOG；`dsh-agenote` 有 1 个
  tag 但 `package.json` 是 `private: true`（发不了 npm）；`el` / `pi` / `skills`
  既无 tag 也无 CHANGELOG，elisp 还缺 `Package-Version` 头（装不了 package.el）。

## Considered Options

- **核心 monorepo（只合并 CLI + 协议规范 + 生成器，5 个适配器保持独立 repo 消费生成物）**：
  被拒。适配器保住了独立 URL，但一个语义变更仍要跨多仓提交，**只解决复制问题、
  不解决原子性问题**——而原子性才是这次改造的首要目标。
- **不合并，改为「生成式同步」**：被拒。同上，且跨仓提交顺序问题依旧，还额外背上
  一套 codegen 发布约定。
- **新建 `agenote-monorepo` 仓，CLI 退为子目录**：被拒。平白丢掉 PyPI 包名
  `agenote` 的仓身份、14 个历史 tag 与已配置的 `release.yml`，收益为零。
- **dotfiles 侧继续用 submodule（指向整仓）**：被次优方案采纳前否决。三步闭环仍在，
  且漏推症状不变。改为钉版本拉取脚本。
- **`agenote-zcode` / hermes 插件各建独立仓转 submodule**：被拒。等于把缺陷（指针
  bump）制度化；且这两个组件与 pi / skills 的分发形态完全相同，无需区别对待。

## Consequences

### 仓库布局

```
ShineBreaker/agenote/            ← monorepo 根（沿用原 CLI 仓）
├── packages/
│   ├── agenote/                  Python CLI（PyPI: agenote）
│   ├── agenote-el/               Emacs 插件（emacs package）
│   ├── dsh-agenote/              DSH cordis bundle（npm: dsh-agenote）
│   ├── agenote-pi/               omp/pi TS 扩展
│   ├── agenote-zcode/            ZCode 插件
│   ├── agenote-hermes/           hermes Python 插件
│   └── agenote-skills/           3 个 agent skill
├── spec/                         跨组件语义单一真相源（信号 / 预算 / 状态目录）
├── tools/                        codegen、契约测试、发布抽取
├── docs/adr/                     跨组件 ADR（本文件起）
├── CONTEXT.md                    领域术语
└── .github/workflows/            多组件 CI + 4 条发布流水线
```

包目录**沿用既有名字**（不重命名）：名字已出现在 URL、插件名、import 路径与用户
文档中，迁移是纯 `git mv`，零文档改写成本。ADR 0001–0004 随 CLI 移入
`packages/agenote/docs/adr/`。

### 单一真相源与代码生成

`spec/injection.toml` 持有信号清单、每宿主预算表、状态目录约定、CLI 调用契约。
`tools/codegen` 生成各语言的常量块到各包内**并提交进仓**：

- 关键点：宿主插件是**独立安装**的（`~/.zcode/plugins/`、omp 扩展目录），
  运行时不能依赖 monorepo 存在。所以生成物**提交进仓**，插件零运行时依赖。
- CI 跑漂移检查：重新生成后 `git diff --exit-code`，不一致即红。这把
  「改语义需跨文件同步」的手工纪律换成机器保障。
- 契约耦合（`list --json` 字段形状、`stats`/`health`/`review`/`get`/`memory` 的
  自由文本渲染）不进 codegen，进 `tools/` 的契约测试 + golden 快照：elisp 端
  「CLI 改措辞会静默破坏显示」这一类问题改为 CI 失败。

### 发布

tag 格式 `<component>-v<version>`（`agenote-v0.3.0`、`dsh-agenote-v0.2.0`），
一个提交可携带多个 tag，每个 tag 只触发自己的流水线，组件版本互不牵制。渠道：

| 组件 | 渠道 | 前置补齐 |
|---|---|---|
| `agenote` | PyPI + GitHub Release | `pyproject.toml` 迁到 `packages/agenote/` |
| `agenote-el` | GNU ELPA / elpa.gnu.org | 补 `Package-Version` / `Package-Archive` 等头 |
| `dsh-agenote` | npm + GitHub Release | 去 `private: true` |
| `agenote-pi` / `agenote-zcode` / `agenote-hermes` / `agenote-skills` | GitHub Release tarball + 拉取脚本 | 各补 CHANGELOG 与 LICENSE 位置声明 |

**发布流程的 tag 漏推陷阱原样继承**：`release.yml` 认 `on: push: tags`，漏推 tag 时
远端无 tag 事件、workflow 不跑，而本地一切正常。每次发版仍须
`git ls-remote --tags origin | grep <version>` 自查。

### dotfiles 消费

`agenote-version` 文件只记一个版本号；`sync-agenote.sh` fetch 该 tag 的 tarball、
校验 sha256、解包到 `~/.local/share/agenote/packages/<version>/`（内容寻址，可
多版本共存）；GNU stow 继续做唯一的部署手段——从缓存目录逐文件 symlink 到宿主
路径（现状已是此机制，无需改变 stow 用法）。

由此得到三个直接收益：

- 版本变更 = 改一行版本号。天然原子、可 code review、不可能漏推。
- dotfiles 侧不再有任何 agenote 的 git checkout → `ShineBreaker/agenote-skills`
  与 `ShineBreaker/pi-agenote` 两个 submodule 从 `.gitmodules` 移除。
- 卸载 = 删缓存目录 + `stow -D`。没有 gitlink 悬空、没有裸目录残留。

### 迁移期间的硬约束

`agenote-zcode` 是 ZCode 的运行时插件，改造期间**不得先动 dotfiles 侧**。落地顺序
固定为：合仓 → spec/codegen → 契约测试 → CI → 发布流水线 → 拉取脚本验证通过 →
最后才移除 submodule 与 dotfiles 裸目录。任何阶段失败都可停在原状态。

`~/Documents/Org`（知识库本体，CLI 的 `KB_ROOT`）不参与本次改造，是独立 git 仓。

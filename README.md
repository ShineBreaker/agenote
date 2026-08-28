# agenote — 跨 Agent 经验平台 CLI

[![CI](https://github.com/ShineBreaker/agenote/actions/workflows/ci.yml/badge.svg)](https://github.com/ShineBreaker/agenote/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 跨 Agent 知识管理与经验共享系统。通过 CLI 命令（终端调试与 cron 入口）暴露统一
> API；支持多个 AI agent 共享经验卡片、记忆、策展与工作流蒸馏。

本仓库是 agenote 系统的**程序本体**——Python CLI 工具与共享库。配套的 agent skills
和 omp 扩展在独立仓库：

- [agenote-skills](https://github.com/ShineBreaker/agenote-skills) — 3 个 agent skill
- [pi-agenote](https://github.com/ShineBreaker/pi-agenote) — oh-my-pi 扩展

## 安装

### uv tool（推荐）

```bash
# 从 git 安装（产出 ~/.local/bin/{agenote,agenote-cli,orgfmt}）
uv tool install git+https://github.com/ShineBreaker/agenote.git

# 本地开发（editable，改源即生效）
uv tool install --editable /path/to/agenote

# 带 jieba 中文分词（dream 子命令用）
uv tool install --with jieba git+https://github.com/ShineBreaker/agenote.git
```

### pip

```bash
pip install --user git+https://github.com/ShineBreaker/agenote.git
```

## 命令

安装后获得三个命令：

| 命令          | 用途                                                                      |
| ------------- | ------------------------------------------------------------------------- |
| `agenote`     | 主 CLI（30+ 子命令）：卡片 CRUD、检索、记忆、策展、健康度、跨 agent 协同 |
| `agenote-cli` | 轻量 shim，供 pi 扩展 execSync 调用（health）                             |
| `orgfmt`      | 通用 org-mode 格式化 CLI（共享 agenote 库）                               |

运行 `agenote --help` 查看完整子命令清单。主要命令分组：

- **卡片 CRUD**：`add` / `get` / `list` / `update` / `merge` / `connect` / `archive` / `restore`
- **检索**：`search`（BM25 排序，CJK n-gram 中英混检）/ `tags` / `fields` / `inbox`
- **记忆系统**：`memory`（`--add` / `--stale` / `--touch` / `--archive` 等子选项）
- **策展**（原子工具，流程由 agent 依据 [agenote-skills](https://github.com/ShineBreaker/agenote-skills) 编排）：
  `health` / `gaps` / `deduplicate` / `review` / `archive --stale`（归档候选，只读）/
  `list --unused-days`（降级候选，只读）/ `lint`（`--json` 分类报告）/ `reindex`（含 WEIGHT 重算）/ `stats`
- **跨 agent**：`reconcile` / `dream` / `distill` / `extract`
- **维护**：`init` / `commit` / `config` / `doctor`（环境自诊断）/ `touch` / `viz`

多 agent 并发写入安全：变更类命令持进程级 KB 锁（flock），文件落盘走
原子写（tmp + rename），同秒 `add` 自动追加 ID 序号防覆盖。

## 配置

### 快速上手

```bash
agenote config init    # 生成带注释的配置模板到 ~/.config/agenote/config.toml
agenote config show    # 打印当前生效配置及每个键的来源（env / file / default）
```

配置文件遵循 XDG（`$XDG_CONFIG_HOME/agenote/config.toml`，默认
`~/.config/agenote/config.toml`），TOML 格式。**三层优先级：环境变量 > 配置文件 > 内置默认值**——CLI 显式传参时参数最优先。

### 可配置项（节选）

| 节 | 内容 |
| --- | --- |
| `[paths]` | `kb_root` 知识库根、`agenote_dir` agent 域子目录名、conversations/reconcile/viz 产物目录 |
| `[agent]` | 卡片 SOURCE_AGENT 默认写入者标签 |
| `[weights]` | 检索权重（人类 1.5 / agent 1.0）、touch 加成、stale 惩罚、去重加成、评分系数 |
| `[curation]` | stale/archive 天数阈值、去重相似度阈值 |
| `[health]` | 孤立率/过时率/类型偏斜的 warn/bad 分级阈值 |
| `[dream]` / `[distill]` | 启发式阈值（词频、窗口天数、聚类下限等） |
| `[extract]` / `[extract.sources]` | 抽取截断链、每源条数上限、7 个 agent 源数据库路径 |
| `[search]` / `[add]` / `[commit]` / `[viz]` | 检索参数、新卡片默认字段、commit 精准 add 清单、可视化参数 |

完整键清单与默认值见 `agenote config init` 生成的模板注释，或
[config.py SCHEMA](src/agenote/config.py)（全部键的单一真相源）。

### 知识库根（`KB_ROOT`）

默认 `~/Documents/Org`，配置方式（按优先级）：

```bash
KB_ROOT=/path/to/kb agenote stats      # ① 环境变量临时覆盖
# ② 配置文件：[paths] kb_root = "/path/to/kb"
# ③ 默认值：~/Documents/Org
```

卡片数据、`MEMORY.org`、`index.json`、`conversations/` 等运行时产物写入 `KB_ROOT`，
**不在本仓库**——本仓库只管 CLI 源码。

### 知识库 commit 约定

`agenote init` / `agenote commit` 生成与建议的 message 采用 Conventional Commits
（内容仓库简化版）：

```
chore(init): 初始化知识库
chore(curate): 新增 K 张 / 更新 M 张
feat(card): <新卡片主题>
```

## 架构

```
agenote CLI ─┐
             ├── agenote 包（src/agenote/）
orgfmt CLI ──┤     ├── config.py    配置层（SCHEMA 单源 + env > toml > 默认）
             │     ├── core.py     常量 + KBContext + 工具函数
agenote-cli ─┘     ├── cards.py    卡片 CRUD
                   ├── memory.py   记忆系统
                   ├── health.py   健康度分析
                   ├── reconcile.py 跨 agent 只读索引
                   ├── dream.py    启发式候选发现
                   ├── distill.py  工作流蒸馏
                   ├── extract/    对话抽取（7 个 agent extractor）
                   └── viz/        HTML 可视化生成
```

三个 CLI 共享同一 `agenote` 包内核，行为一致。`agenote-cli` 是给 pi 扩展的
轻量入口（纯 stdlib，仅 health 一个命令——策展由 agent 走主 CLI 编排）。

## 开发

```bash
# 克隆 + 本地安装（editable）
git clone https://github.com/ShineBreaker/agenote.git
cd agenote
uv sync --extra test
uv tool install --editable .

# 验证
agenote --help
python -c "from agenote import core; print(core.KB_ROOT)"

# 测试（CI 同款）
uv run pytest -q

# 构建 wheel/sdist
uv build
```

## Shell 补全

fish / zsh / bash 均支持 Tab 补全子命令与常用选项。

```bash
# 动态生成（推荐：变更后自动同步）
agenote completions fish > ~/.config/fish/completions/agenote.fish
agenote completions zsh  > ~/.zsh/completions/_agenote   # 确保该目录在 $fpath
agenote completions bash > /etc/bash_completion.d/agenote  # 或 source

# 静态脚本（随仓库分发，无需已安装 agenote）
# completions/agenote.fish  completions/_agenote  completions/agenote.bash
```

覆盖 33 个子命令（含 `config init/show` 二级）、`--domain` / `--type` /
`--source` 等常用枚举。`completions/` 下的静态脚本与 `agenote completions`
输出逐字节一致，CI 校验。

贡献指南（含 Conventional Commits 规范）见 [CONTRIBUTING.md](CONTRIBUTING.md)，
版本历史见 [CHANGELOG.md](CHANGELOG.md)，架构决策见 [docs/adr/](docs/adr/)。

## 致谢

本项目的若干核心设计移植自 [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian)
（MIT License © AgriciDaniel 及贡献者），在此致谢：

- **写入安全**（flock 进程锁 + tmp/rename 原子写）源自其「崩溃后可确定性恢复」
  的事务哲学（裁剪版——agenote 单命令即时写入，不引入 journal/审批两阶段）
- **BM25 检索**（纯 stdlib Okapi + CJK 1/2/3-gram 分词）源自其 wiki-retrieve
  检索扩展（裁剪版——百级卡片进程内即时计算，不引入持久倒排索引）
- **doctor 能力检测**的三态声明与 `verification_reason` 惯例
  （「没有验证器」是一等信息公开声明）源自其 capabilities 合同
- **lint 分类报告**（summary.category_counts + 条目 {file, reason}，
  agent 可消费、可差分）源自其 wiki-lint 报告结构

## 许可证

MIT，见 [LICENSE](LICENSE)。

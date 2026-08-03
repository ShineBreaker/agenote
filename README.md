# agenote — 跨 Agent 经验平台 CLI

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

| 命令 | 用途 |
| ---- | ---- |
| `agenote` | 主 CLI（29 个子命令）：卡片 CRUD、检索、记忆、策展、健康度、跨 agent 协同 |
| `agenote-cli` | 轻量 shim，供 omp-hooks 扩展 execSync 调用（health/curate/review） |
| `orgfmt` | 通用 org-mode 格式化 CLI（共享 agenote 库） |

运行 `agenote --help` 查看完整子命令清单。主要命令分组：

- **卡片 CRUD**：`add` / `get` / `list` / `update` / `merge` / `connect` / `archive` / `restore`
- **检索**：`search` / `tags` / `fields` / `inbox`
- **记忆系统**：`memory`（`--add` / `--stale` / `--touch` / `--archive` 等子选项）
- **策展**：`curate`（一键）/ `lint` / `deduplicate` / `health` / `gaps` / `reindex` / `stats`
- **跨 agent**：`reconcile` / `dream` / `distill` / `extract`
- **维护**：`init` / `commit` / `touch` / `viz`

## 知识库根（`KB_ROOT`）

默认 `~/Documents/Org`，通过环境变量 `KB_ROOT` 覆盖：

```bash
KB_ROOT=/path/to/kb agenote stats
```

卡片数据、`MEMORY.org`、`index.json`、`conversations/` 等运行时产物写入 `KB_ROOT`，
**不在本仓库**——本仓库只管 CLI 源码。

## 架构

```
agenote CLI ─┐
             ├── agenote 包（src/agenote/）
orgfmt CLI ──┤     ├── core.py     常量 + KBContext + 工具函数
             │     ├── cards.py    卡片 CRUD
agenote-cli ─┘     ├── memory.py   记忆系统
                   ├── health.py   健康度分析
                   ├── reconcile.py 跨 agent 只读索引
                   ├── dream.py    启发式候选发现
                   ├── distill.py  工作流蒸馏
                   ├── extract/    对话抽取（7 个 agent extractor）
                   └── viz/        HTML 可视化生成
```

三个 CLI 共享同一 `agenote` 包内核，行为一致。`agenote-cli` 是给 omp-hooks 扩展的
轻量入口（纯 stdlib，health/curate/review 三个命令）。

## 开发

```bash
# 克隆 + 本地安装（editable）
git clone https://github.com/ShineBreaker/agenote.git
cd agenote
uv tool install --editable .

# 验证
agenote --help
python -c "from agenote import core; print(core.KB_ROOT)"

# 构建 wheel/sdist
uv build
```

## 许可证

MIT，见 [LICENSE](LICENSE)。

# Changelog

本项目的所有显著变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本管理遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.5] - 2026-08-20

### Added

- 配置文件支持：`~/.config/agenote/config.toml`（TOML，遵循 XDG），84 个配置键
  覆盖知识库路径（`kb_root`、agent 域目录、各产物目录）、7 个 extract 源数据库
  路径、检索权重、策展阈值、dream/distill 启发式等；优先级为
  环境变量 > 配置文件 > 内置默认值，CLI 显式传参最优先。
- 新增 `agenote config` 子命令：`config init` 生成带注释的配置模板，
  `config show` 打印当前生效配置及来源（env / file / default）。
- GitHub Actions CI：push / PR 自动运行 pytest（Python 3.10–3.12 矩阵）。
- CHANGELOG.md 与 CONTRIBUTING.md（开发环境、Conventional Commits 规范）。

### Changed

- CLI 生成与建议的知识库 commit message 对齐 Conventional Commits：
  `agenote init` 初始提交改为 `chore(init): 初始化知识库`，策展提交建议改为
  `chore(curate): …` / `feat(card): …` 格式。
- 全部模块常量接入配置层并收敛双源常量：去重阈值（4 处）、去重加权（2 份）、
  默认权重（viz 副本）、add 默认值（cards/index 两份）单源化；修复 KB_ROOT
  三处独立硬编码不随配置变化的问题；hermes 源获得与其他 6 源一致的
  env/config 覆盖链。

### Fixed

- `AGENOTE_AGENT` 设为纯空白时不再把空白串写入 SOURCE_AGENT（回落默认值）。
- 配置值类型错误时报键名退出，不再裸 traceback。
- lint `--fix` 写回前路径归一化并限定 `.org` 文件；viz serve 探测限定
  localhost；HTML 骨架模板占位符白名单（静态审计加固）。

## [0.1.5.1] - 2026-08-20

### Added

- fish / zsh / bash 自动补全：`agenote completions <shell>` 动态生成，
  `completions/` 静态脚本随仓库分发（`agenote.fish` / `_agenote` / `agenote.bash`），
  覆盖全部 34 个子命令与常用枚举（`--domain` / `--type` / `--source` 等）；
  `completions.py` 为单一真相源，`completions/` 与动态输出逐字节一致。

## [0.1.2] - 2026-08-03

### Changed

- jieba 改为硬依赖，确保 `dream` 子命令开箱即用。
- 重构为 PEP 621 src layout + uv 打包。

### Removed

- 移除 `ag-ent`、`agenote-mcp`（MCP 层由 agent skills 仓库承担）。

## [0.1.0] - 2026-07-05

### Added

- 首个发布版本：卡片 CRUD、检索、记忆系统、策展、跨 agent
  reconcile / dream / distill、7 源对话抽取、HTML 可视化。
  更早的逐条变更见 git log。

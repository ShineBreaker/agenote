# Changelog

本项目的所有显著变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本管理遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- 配置文件支持：`~/.config/agenote/config.toml`（TOML，遵循 XDG），可覆盖知识库
  路径（`kb_root`）、agent 名、检索权重、策展阈值等行为参数；优先级为
  环境变量 > 配置文件 > 内置默认值。
- 新增 `agenote config` 子命令：`config init` 生成带注释的配置模板，
  `config show` 打印当前生效配置及来源（env / file / default）。
- GitHub Actions CI：push / PR 自动运行 pytest（Python 3.10–3.12 矩阵）。

### Changed

- CLI 生成与建议的知识库 commit message 对齐 Conventional Commits：
  `agenote init` 初始提交改为 `chore(init): 初始化知识库`，策展提交建议改为
  `chore(curate): …` / `feat(card): …` 格式。
- 本仓库开发 commit 规范对齐标准 Conventional Commits（详见 CONTRIBUTING.md），
  历史提交中的 `FEATURE:` / `REFACTOR:` 等大写前缀不再使用。

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

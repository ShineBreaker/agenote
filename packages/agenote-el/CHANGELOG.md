# Changelog

本文件记录本组件的显著变更。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

本组件是 [agenote](https://github.com/ShineBreaker/agenote) monorepo 的组成部分
（布局与发布约定见仓库根 `docs/adr/0005-monorepo-unify-seven-components.md`），
由 `<component>-v<version>` 形式的 tag 触发发布。

## [0.1.0] - 2026-10-03

### Added

- **迁入 monorepo**：本组件并入 `ShineBreaker/agenote` 单仓，目录 `packages/agenote-el/`。
  原独立仓保留完整历史，迁移决策见 ADR 0005。
- **补 ELPA 包声明**：新增 `agenote-pkg.el`（`define-package`），并为每个 `.el`
  补 `;; Version:` 与 `;; Package-Requires:` 头。此前本组件无任何包元数据，
  只能从 git 源码目录手工 `load`，无法走 `package-install` / ELPA。
- **发布渠道**：本组件自 0.1.0 起可打包为 ELPA 格式 tar 并随
  `agenote-el-v*` tag 发布到 GitHub Release。

迁移前基线说明：本组件的功能面（`agenote-list` / `agenote-search` /
`agenote-health` 等交互命令与 browse mode）自建仓以来未变，本次仅补元数据。


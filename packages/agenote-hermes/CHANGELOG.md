# Changelog

本文件记录本组件的显著变更。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

本组件是 [agenote](https://github.com/ShineBreaker/agenote) monorepo 的组成部分
（布局与发布约定见仓库根 `docs/adr/0005-monorepo-unify-seven-components.md`），
由 `<component>-v<version>` 形式的 tag 触发发布。

## [Unreleased]

### Changed

- 注入状态机移到 CLI（`agenote inject`）：插件不再自己算指纹、拼 query、读写状态文件；简报（system prompt section）与召回（`pre_llm_call`）都只传事件与预算。此前累计预算跨会话延续的问题随状态机下沉一并消除——`inject --event session` 按 session_id 重置，render 时即完成。

## [0.1.0] - 2026-10-03

### Added

- **迁入 monorepo**：本组件并入 `ShineBreaker/agenote` 单仓，目录 `packages/agenote-hermes/`。
  原独立仓保留完整历史，迁移决策见 ADR 0005。
- **本插件首次获得 git 边界**：此前只作为 Guix-configs dotfiles 里的裸目录
  存在，没有独立历史、CI 零覆盖、他人无法安装。迁移时按路径提取了 dotfiles
  的 2 个历史提交并保留原作者与日期。
- **完成信号与预算常量改为生成块**：真相源是仓库根 `spec/injection.toml`。
- **发布渠道**：自 0.1.0 起随 `agenote-hermes-v*` tag 以 tar.gz 分发。


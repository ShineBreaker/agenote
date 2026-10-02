# Changelog

本文件记录本组件的显著变更。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

本组件是 [agenote](https://github.com/ShineBreaker/agenote) monorepo 的组成部分
（布局与发布约定见仓库根 `docs/adr/0005-monorepo-unify-seven-components.md`），
由 `<component>-v<version>` 形式的 tag 触发发布。

## [0.1.0] - 2026-10-03

### Added

- **迁入 monorepo**：本组件并入 `ShineBreaker/agenote` 单仓，目录 `packages/agenote-skills/`。
  原独立仓保留完整历史，迁移决策见 ADR 0005。
- **完成信号的真相源迁移到 monorepo 根 `spec/injection.toml`**：本包的
  `agenote-review/references/triggers.md` 降为散文版（仍承载机制说明与隐式信号），
  其两张显式完成信号清单由 `tools/codegen/check.py` 校验与 spec 一致。
  此前该文件被文档称为「插件单一真相源」，但它只是散文，改信号实际要手工同步
  pi / dsh / zcode / hermes 四处代码加 `hooks.json` 正则，共 5 处。
- **发布渠道**：自 0.1.0 起随 `agenote-skills-v*` tag 以 tar.gz 分发。

迁移前基线说明：三个 skill（`agenote-base` / `agenote-curator` /
`agenote-review`）的行为规范自建仓以来未变，本次仅调整信号维护机制与发布。


# Changelog

本文件记录本组件的显著变更。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

本组件是 [agenote](https://github.com/ShineBreaker/agenote) monorepo 的组成部分
（布局与发布约定见仓库根 `docs/adr/0005-monorepo-unify-seven-components.md`），
由 `<component>-v<version>` 形式的 tag 触发发布。

## [0.1.0] - 2026-10-03

### Added

- **迁入 monorepo**：本组件并入 `ShineBreaker/agenote` 单仓，目录 `packages/agenote-zcode/`。
  原独立仓保留完整历史，迁移决策见 ADR 0005。
- **`hooks/hooks.json` 的 `UserPromptSubmit` 预筛正则补上 `完成`**：此前该信号
  虽在 `COMPLETION_SIGNALS` 清单中，但预筛正则漏了它，只含「完成」的整句根本
  进不了精确匹配，信号在 zcode 侧实际从未生效。monorepo 的
  `tools/codegen/check.py` 首次运行即报出此问题。
- **完成信号与预算常量改为生成块**：真相源是仓库根 `spec/injection.toml`，
  由 `tools/codegen/generate.py` 产出并提交进仓。迁移前信号在 pi / dsh / zcode /
  hermes 四处逐字复制，靠纪律同步。


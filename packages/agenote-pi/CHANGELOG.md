# Changelog

本文件记录本组件的显著变更。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

本组件是 [agenote](https://github.com/ShineBreaker/agenote) monorepo 的组成部分
（布局与发布约定见仓库根 `docs/adr/0005-monorepo-unify-seven-components.md`），
由 `<component>-v<version>` 形式的 tag 触发发布。

## [0.1.0] - 2026-10-03

### Added

- **迁入 monorepo**：本组件并入 `ShineBreaker/agenote` 单仓，目录 `packages/agenote-pi/`。
  原独立仓保留完整历史，迁移决策见 ADR 0005。
- **完成信号与预算常量改为生成块**：真相源是仓库根 `spec/injection.toml`，
  由 `tools/codegen/generate.py` 产出并提交进仓。生成物进版本库是为了让本扩展
  继续支持独立安装——omp 直接加载 `.ts` 源码，运行时不依赖 monorepo 存在。
- **发布渠道**：自 0.1.0 起随 `agenote-pi-v*` tag 以 tar.gz 分发。


# Changelog

本文件记录本组件的显著变更。格式基于
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循
[语义化版本](https://semver.org/lang/zh-CN/)。

本组件是 [agenote](https://github.com/ShineBreaker/agenote) monorepo 的组成部分
（布局与发布约定见仓库根 `docs/adr/0005-monorepo-unify-seven-components.md`），
由 `<component>-v<version>` 形式的 tag 触发发布。

## [0.2.0] - 2026-10-12

策展模型从「保存者」翻转为「蒸馏器」：举证责任在卡片——证明不了复用价值的卡默认出局，KB 活跃卡数随时间下降、密度上升。配套写入侧门禁与高频卡机制化退役路径。

---

### Changed

- **策展 KPI 翻转**：每轮策展以「活跃卡净变化 ≤0、零复用率下降、检索抽查不回退」三条硬指标验收，报告必填期初/期末对比；连续两轮净增为正即流程回归（1f550df）
- **孤立卡默认出局**：>45 天未使用默认归档，废除「stale + >90 天未验证」两级死锁条件；救活是例外，须补写「预期复用场景」（1f550df）
- **维护流程改漏斗制**：批量诊断 → 批量分流 → 例外读卡，取代逐卡精读（cc7bef4）

### Added

- **蒸馏步**：去重后新增横向合并（同主题 ≥3 → 高密度卡，`MERGED_FROM` 溯源）与纵向升华（`usage≥5` 卡沉淀为 skill/hook/校验器后退役）（1f550df）
- **写入侧门槛**：`add` 前须答出「谁在什么场景会命中这张卡」，答不出记入 `~/Documents/Org/agent-inbox.org` 攒批（1f550df）
- **记忆门禁文档**：U3 出处溯源（`--source`/`--added`）写入流程；U1 `memory-entry-budget` 超限条目的人工拆分流程（a308a46）
- **内联正文陷阱**：`--stdin <<EOF` 正文含「git commit」触发命令 gate 的规避写法（f0a72b0）

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


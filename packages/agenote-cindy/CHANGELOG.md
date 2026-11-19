# Changelog

本项目的所有显著变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.1.0] - 2026-11-21

首个版本：把 agenote 接进 Cindy。宿主用 `--no-extensions` 启动内置 Pi，omp 扩展
（`agenote-pi`）在这条路上不可用，故改用 ghost 插件。

---

### Added

- **`will-user-message` 注入**：会话简报 + 按轮召回封进 `<!-- agenote-inject:… -->`，
  以 `rewrite` 挂到本轮用户消息开头。注入状态机（指纹缓存、query 去重、单会话累计
  预算）全部由 `agenote inject` 承担，插件只搬运。
- **完成信号与空闲兜底**：`did-turn-end` 命中 `spec/injection.toml` 的显式完成信号后
  投递 agenote-review 提示；长轮次结束后武装空闲兜底，两者都带 `<agenote-hook>`
  标记断开自触发反馈环。投递走宿主 `send_to_session`（`control` 组）。
- **只读工具**：`agenote_search`、`agenote_health`，经随包 Node worker 调 CLI，
  子命令白名单，不带 shell。
- **旧 CLI 降级**：`--host cindy` 被拒时自动退回 `generic`；没有 `inject` 子命令时
  只注会话简报，工具照常可用。

### Notes

- 追加型注入：入口钩子改不到历史，简报正文会出现在用户消息气泡里并随轮次累积，
  去重与累计预算由 CLI 兜底。改 `will-compose-context` 可避免污染气泡，但需要
  逐任务勾选插件。

# agenote-skills：agent 行为规范

一套行为规范，所有 agent 共用。

## 解决的问题

同一个 agent 换宿主就变样。在 pi 里养成的习惯，搬到 crush、opencode、hermes 上
往往要重新教一遍：开工前先查经验、中途复用已有结论、收尾时把经验写回去。
原因不复杂，每个宿主的提示词、模型和工具链都不一样。行为规范只写在某个宿主的
配置里，就只对那个宿主生效。

agenote-skills 把这套协议从宿主配置里抽出来，放进 agenote monorepo 的一个共享包。所有接进来的
agent 读同一份，读到的是同一套行为。

## 三个 skill

- `agenote-base` 负责日常读写。开工前跑 `list`→`search`→`get` 查有没有踩过的坑，
  过程中复用已有卡片，结束时用 `add`→`touch` 记录，再 `commit`。
- `agenote-curator` 负责知识库健康度。诊断、去重、归档、重算检索权重，
  以及多个 agent 之间的 memory reconcile。
- `agenote-review` 负责会话后的经验采集。识别哪些经历值得留痕，判定
  ENTRY_TYPE，再决定写新卡片还是 touch 已有卡片。

三者互相引用对方的 skill 名，框架据此切换加载哪份规范。完整的职责对照表见
[使用文档](docs/usage.md)。

## 核心设计

- 纯 Markdown。agent 框架每个会话扫描目录加载规范，改完源文件即生效，
  没有编译和部署步骤。
- 按需加载。每个 skill 的 `description` 里写明触发信号，框架匹配到才载入正文，
  平时只有三行 description 的开销。
- 单一外部依赖。规范本身不实现读写，所有卡片操作都走 PATH 里的 `agenote` CLI。

## 不适合谁

- 只跑一个 agent，且行为已经稳定。自己写一段提示词就够，维护一份共享规范
  是额外成本。
- 不打算用 agenote 做记忆后端。这些 skill 里的每条操作都是 `agenote` CLI 调用，
  换后端就得重写。

## 深入阅读

- [使用文档](docs/usage.md)：skill 规范、职责对照、部署、依赖
- [English README](README.md)
- [agenote 主仓库](https://github.com/ShineBreaker/agenote)

## 许可证

MIT，见 [LICENSE](LICENSE)。

# agenote

> One memory, all agents sharing it. / 一个知识库，所有 agent 共享。

Agent 不共享记忆：你在 Claude Code 踩过的坑，到 Codex 还得再踩一次——每个宿主
都把记忆存成只有自己能读的私有格式。agenote 把这些经验收进一个 Org 目录，
人与 agent 读写同一批文件。领域术语见 [CONTEXT.md](CONTEXT.md)。

本仓库是 monorepo（[ADR 0005](docs/adr/0005-monorepo-unify-seven-components.md)），
7 个组件共用一套语义真相源，各自独立发版：

| 组件 | 是什么 | 发布渠道 |
| --- | --- | --- |
| [`packages/agenote`](packages/agenote/) | Python CLI，**唯一实现层**：卡片状态机、双域搜索、注入简报 | PyPI `agenote` |
| [`packages/agenote-el`](packages/agenote-el/) | Emacs 插件，纯 CLI 适配层 | ELPA tar |
| [`packages/agenote-zcode`](packages/agenote-zcode/) | ZCode 插件（hooks 注入 + 完成信号） | Release tar.gz |
| [`packages/agenote-pi`](packages/agenote-pi/) | omp 单文件 TypeScript 扩展 | Release tar.gz |
| [`packages/agenote-hermes`](packages/agenote-hermes/) | hermes Python 插件 | Release tar.gz |
| [`packages/dsh-agenote`](packages/dsh-agenote/) | DSH cordis bundle（npm） | npm `dsh-agenote` |
| [`packages/agenote-skills`](packages/agenote-skills/) | 3 个 agent skill（base / curator / review），行为规范的载体 | Release tar.gz |

## 快速开始（CLI）

```bash
uv tool install agenote
agenote add "标题" --body "一条经验" --category debug   # 记一张卡
agenote search "关键词"                                 # 跨域搜索
```

CLI 的安装与用法详见 [packages/agenote](packages/agenote/)；Emacs 端装
`agenote-el`，其余宿主按上表对应包的 README 安装。

## 架构约定

- **单一实现层**：一切落盘逻辑只在 CLI；各宿主插件只做「事件触发 + 命令入口」，
  不重复实现知识库逻辑。
- **单一真相源**：跨组件复用的信号 / 预算 / 时序在 [`spec/`](spec/)，由
  `tools/codegen` 生成进各包（生成物随仓提交，CI 拦截漂移）；行为规范写在
  `agenote-skills` 的 SKILL.md，改策略不发版。
- **tag 前缀即包名**：`agenote-v0.3.0`、`agenote-el-v0.1.0`……一个提交可携带
  多个 tag，组件版本互不牵制。

## 文档

- [CONTRIBUTING.md](CONTRIBUTING.md) — 开发环境、commit 规范、各包验证入口
- [AGENTS.md](AGENTS.md) — monorepo 规范本体（改代码前必读）
- [docs/adr/](docs/adr/) — 跨组件架构决策（0005 起；各包内 ADR 见各自目录）

## License

[MIT](LICENSE)

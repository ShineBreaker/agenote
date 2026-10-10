# agenote

> One memory, all agents sharing it. / 一个知识库，所有 agent 共享。

[![CI](https://github.com/ShineBreaker/agenote/actions/workflows/ci.yml/badge.svg)](https://github.com/ShineBreaker/agenote/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

你在 Claude Code 踩过的坑，到 Codex 还得再踩一次——每个宿主都把记忆锁进只有
自己能读的私有格式：Markdown 碎片、SQLite、JSONL。换个宿主就失忆，你辛苦喂
出来的经验永远带不走。

agenote 把所有 agent 的经验收进**一个 Org 目录**：纯文本、人可读、git 友好，
人与 agent 读写同一批文件——没有数据库，没有服务端。开工时相关经验自动注入
会话，任务结束时教训自动沉淀成卡片。你的 agent 越用越顺手，而这份积累属于
你，不属于任何一家工具。

## 为什么不是又一个记忆插件

| | 宿主内置记忆 | agenote |
| --- | --- | --- |
| 范围 | 单宿主私有 | zcode / Claude Code / Codex / oh-my-pi / opencode / Hermes / DSH 共享一个库 |
| 存储 | 私有格式，你读不了也改不了 | 统一 Org 纯文本：Emacs 直接打开，diff 一目了然 |
| 写入 | 各宿主各写各的 | 宿主只读，CLI 独占写入——flock + 原子写，多 agent 并发不冲突 |
| 检索 | 宿主内逐条匹配 | 全局 BM25，中文句子里嵌英文命令名照样命中 |
| 策展 | 无 | 健康报告 / 去重 / 降级 / 归档：agent 提建议，CLI 给证据 |
| 泄露 | — | secret 扫描默认开启；敏感条目只留本地，永不注入会话 |

还有两件事内置记忆做不到：**每条经验都能溯源**到原始对话（完整工具调用与
推理，不是一句转述摘要）；`agenote viz` 把整个知识库渲染成单个可搜索的 HTML。

## 30 秒上手

```bash
uv tool install agenote
agenote init                  # 建知识库，默认 ~/Documents/Org
agenote add "标题" --body "一条经验" --category debug
agenote search "关键词"       # 跨域搜索
```

装上对应宿主的插件（见下表）之后就是全自动：会话开始注入简报，任务完成
触发记卡。你随时可以用 Emacs 打开知识库直接改——`org agenda` 按状态列卡片，
`org-refile` 移动它们。它是你的文件，不是 agent 的黑盒。

## 适合谁 / 不适合谁

**适合**：同时用多个 agent 宿主；已经在用 org-mode 记笔记，想让 AI 加入同一套
系统；想要一份自己拥有、可 git 版本化的 agent 记忆。

**不适合**：只用一个宿主（内置记忆够用）；已经有一套不想让 AI 读写的笔记系统；
需要团队共享或实时协作（agenote 是单机 git 仓库）。

## 组件

monorepo，7 个组件共用一套语义真相源，各自独立发版
（[ADR 0005](docs/adr/0005-monorepo-unify-seven-components.md)）：

| 组件 | 是什么 | 发布渠道 |
| --- | --- | --- |
| [`packages/agenote`](packages/agenote/) | Python CLI，**唯一实现层**：卡片状态机、双域搜索、注入简报 | PyPI `agenote` |
| [`packages/agenote-el`](packages/agenote-el/) | Emacs 插件，纯 CLI 适配层 | ELPA tar |
| [`packages/agenote-zcode`](packages/agenote-zcode/) | ZCode 插件（hooks 注入 + 完成信号） | Release tar.gz |
| [`packages/agenote-pi`](packages/agenote-pi/) | omp 单文件 TypeScript 扩展 | Release tar.gz |
| [`packages/agenote-hermes`](packages/agenote-hermes/) | hermes Python 插件 | Release tar.gz |
| [`packages/dsh-agenote`](packages/dsh-agenote/) | DSH cordis bundle（npm） | npm `dsh-agenote` |
| [`packages/agenote-skills`](packages/agenote-skills/) | 3 个 agent skill（base / curator / review），行为规范的载体 | Release tar.gz |

## 文档

- [使用指南](packages/agenote/docs/usage.md) — 安装、命令全集、配置、架构
- [CONTRIBUTING.md](CONTRIBUTING.md) — 开发环境与 commit 规范
- [AGENTS.md](AGENTS.md) — monorepo 规范本体（改代码前必读）
- [docs/adr/](docs/adr/) — 跨组件架构决策
- [CONTEXT.md](CONTEXT.md) — 领域术语：卡片、记忆条目、reconcile 是什么

## License

[MIT](LICENSE)

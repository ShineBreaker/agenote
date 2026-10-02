# agenote：跨 Agent 经验库

[![CI](https://github.com/ShineBreaker/agenote/actions/workflows/ci.yml/badge.svg)](https://github.com/ShineBreaker/agenote/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> 一份记忆，六个 agent 共用。

agent 之间不共享记忆。你在 Claude Code 里踩过的坑，换到 Codex 要重踩一遍；每个宿主
都把记忆存成自己的私有格式，人既读不到也管不了。agenote 把这些记忆收进同一个 Org
目录，AI 和人读写的是同一份文件。

## 为什么是 Org，不是 Markdown

多数记忆系统按 Markdown 生态设计：文件是 `.md`，正文是段落，结构靠标题层级，状态靠
front matter。agenote 走的是另一条路，每张卡片都是标准 org 文件。

这不是包装层。卡片结构直接用 org 的原生机制：

- **属性抽屉** 存结构化元数据。`CATEGORY`、`TECH`、`STATUS`、`WEIGHT`、`USAGE_COUNT`
  都是 drawer 里的键值，不是正文里的行。
- **TODO 关键字** 承担状态机。标题恒为 `* DONE`，真实状态在 `:STATUS:` 属性，四态
  流转（done / stable / stale / archived）由 CLI 读写。
- **章节与行内标记** 保留 org 写法。`** 场景`、`** 关键发现` 是 org 小标题，
  `~orgfmt --check~` 是 org 的行内代码标记，不是反引号。

换来三件 Markdown 方案给不了的东西：

**人可以直接编辑。** 打开 Emacs，`M-x org agenda` 就能把待办卡片排出来，
`org-refile` 挪卡片，双链 `[[卡片][描述]]` 在 org 里可点可跳。维护知识库不需要
agent 参与，也不需要另学一套界面。

**Emacs 用户零学习成本。** 用 org mode 记笔记的人本来就在用这套语法，记 agent 的经验
只是往同一个抽屉里放东西。

**Git diff 可读。** 一张卡片改了哪句话，在 org 里一眼看得出来。纯正文的大 Markdown
文件在 diff 里全是重排。

代价是明确的：它假定你的环境里有 Emacs 或至少一个认 org 语法的编辑器。没有的话，
文件本身仍然是纯文本，用别的工具读也不会丢数据，只是少了 agenda 和 refile。

## 记的是什么

两类东西，边界清楚：

- **经验卡片**，存 `experiences/`。一条可复用的经验，带标题、类别、技术栈、正文和状态。
  这是知识库的原子单元。
- **记忆条目**，存 `MEMORY.org`。跨卡片的长期约束，比如你的偏好、某个项目的构建
  命令、这台机器上的坑。这一层比卡片轻，检索时优先注入会话。

人类手写的卡片和 agent 写的卡片存在不同子域，互不污染。检索默认跨域加权，人写的经验
权重更高。

## 核心能力

**写入安全。** 变更类命令持 flock 进程锁，落盘走 tmp + rename 原子写，同一秒的写入自动
追加 ID 序号。几个 agent 同时写同一份知识库不会撞车。

**中英混检。** BM25 排序配 CJK 1、2、3-gram 分词，一句中文里夹的英文命令名照样命中。

**写入门禁。** secret 扫描默认开着，命中密钥形态直接拒写。敏感记忆条目带标记，只留在
本地，不注入会话也不投影给宿主。

**溯源到原文。** 每条经验能回查原始完整对话，含工具调用和推理过程，不是摘要了事。

**策展闭环。** 健康度报告、去重、状态降级、归档、权重重算都是原子命令。取舍由 agent 依据
规范判断，CLI 只给候选和依据，不代为决定。

**能看。** `agenote viz` 把整个知识库渲染成一份可搜索的单文件 HTML。

## 六个宿主

zcode、Claude Code、Codex、oh-my-pi、opencode、Hermes 各有一个插件或注入器，接到
`agenote context` 上。会话启动注入简报，任务结束触发经验采集，具体行为由共享 skill 定义。
宿主自带的记忆写侧要手动关掉，`agenote doctor` 会逐个检查。

知识库本体是一组 org 文件，Python CLI 管卡片、检索、策展和健康度。37 个子命令，
460 个测试，Python ≥ 3.10，不需要数据库。

## 对比宿主自带记忆

| 维度 | 宿主自带记忆 | agenote |
| --- | --- | --- |
| 覆盖范围 | 单个宿主私有 | 六个宿主共享同一知识库 |
| 存储格式 | 各家私有（Markdown 片段、SQLite、JSONL） | 统一 org 文件，人能直接读 |
| 写侧 | 各宿主独立写，互不感知 | 宿主写侧关闭，CLI 是唯一写入方 |
| 检索 | 宿主内局部匹配 | 全局 BM25，CJK n-gram 中英混检 |
| 策展 | 无 | 健康度、去重、降级、归档、权重重配 |
| 可视化 | 无 | HTML 可视化与 Emacs dashboard 面板 |

## 快速上手

```bash
uv tool install git+https://github.com/ShineBreaker/agenote.git

agenote init                    # 初始化知识库，默认 ~/Documents/Org

agenote add --title "并发写入测试" --category testing --tech Python
```

完整命令、配置项和架构说明见 [使用文档](docs/usage.md)。

## 不适合谁

- 知识库只有你一个人用，只有一个 agent。那种情况下宿主自带记忆够用。
- 你已经有一套跑顺了的笔记系统，并且不打算让 AI 读写它。
- 想要团队共享、多用户权限或实时协同。agenote 是单人单机的文件存储，靠 Git 做版本化。

## 生态组成

- [agenote-skills](https://github.com/ShineBreaker/agenote-skills)：三个 agent skill（base、curator、review）
- [pi-agenote](https://github.com/ShineBreaker/pi-agenote)：oh-my-pi 集成扩展
- [injectors/](injectors/README.md)：六宿主记忆注入器
- [agenote-el](https://github.com/ShineBreaker/agenote-el)：Emacs 集成插件

## 深入阅读

- [使用文档](docs/usage.md)：安装、命令参考、配置、架构、开发
- [English README](README.md)
- [贡献指南](CONTRIBUTING.md) · [版本历史](CHANGELOG.md) · [架构决策](docs/adr/)
- [术语表](CONTEXT.md)：卡片、记忆条目、reconcile 这些词的确切含义

## 许可证

MIT，见 [LICENSE](LICENSE)。

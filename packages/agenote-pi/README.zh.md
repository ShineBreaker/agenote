# pi-agenote：oh-my-pi (omp) 的 agenote 集成扩展

pi-agenote 把 agenote 知识库接进 oh-my-pi (omp)。它是单文件 TypeScript
扩展，omp 直接加载源码，改完下次会话启动就生效，没有构建步骤。

## 它解决什么问题

omp 里每个会话都从空白开始。agent 不知道你上周在哪个依赖上踩过坑，也不记得
你定过什么技术决策，于是同一个坑踩第二遍。agenote 存着这些经验，
pi-agenote 负责把它们送进每轮对话。

第二个问题是收尾。一次改动做完，agent 不会主动把踩坑过程写下来。下次换个人
接手，坑还在原地。pi-agenote 在任务完成的时机插一句提示，让当前 agent 自己
走完经验总结流程。

用的人不需要记任何命令，也不需要在两次会话之间手动搬运上下文。

## 核心机制

记忆简报在每轮 LLM 调用前挂进上下文：

- 插件执行 `agenote-cli context --mode session --host pi --budget 8000`（默认
  预算 8000 字符），把输出用 `<!-- agenote-inject:start -->` 与
  `<!-- agenote-inject:end -->` 标签包住，写到本轮第一条 user 消息开头。
- 每次写之前先扫掉全部旧标签块。宿主即使把注入过的消息持久化进会话历史，
  块也不会一轮轮叠起来。
- 简报按 KB 侧 `MEMORY.org` 和 `memories/` 目录树的 mtime 加 size 做指纹缓存。
  指纹没变就重放缓存，不起 CLI 进程；KB 有改动才真跑一次。
- CLI 返回空（还没有记忆，或 agenote 的语义开关关了）时只剥不写，
  上一轮的简报自动消失。
- CLI 没装、执行失败或超过 5 秒，只往 `.load-errors.log` 追加一行，
  本轮跳过注入，不打断宿主会话。

经验总结由两个时机触发：

- 完成信号。`agent_end` 时读用户最后一条消息，命中完成词表（搞定、做完了、
  测试通过、done.、ship it 等）且距上次触发超过 5 分钟，就给下一轮注入一条
  review 提示。插件会排除自己注入的提示，断开自触发。
- 空闲兜底。会话连续空闲 5 分钟且本会话从没触发过，注入同一条提示。
  用来兜住夜间无人值守那种没人说「完成」的场景。新的 turn 一开始就取消倒计时。

判断和写盘都交给 agent。完成信号清单、写入流程、卡片格式在
agenote-skills 里定义，插件只做触发和命令入口，两边不重复维护。
`/agenote-curate` 同理：它注入的是策展任务提示，策展流程由 agent 按
agenote-curator skill 主导执行，插件不直接跑策展命令。

子代理进程不参与任何一条路径。进程带 `PI_BLOCKED_AGENT`、`--no-session`，
或同时带 `--mode json` 和 `-p` 时判定为子代理，所有钩子直接返回。

## 不适合谁

- 不用 oh-my-pi 的人。这是 omp 扩展，离开 omp 跑不起来。
- 只用一个 agent、不开子代理、不需要跨会话记忆的人。每轮多一次 CLI 查询，
  指纹没变时只是本地 stat 一次目录，换不来东西。
- 已经有别的记忆注入扩展的人。两个扩展都往第一条 user 消息头部塞内容，
  会叠在一起。

## 部署

仓库按原生 stow 布局组织，把内容铺到 `$HOME` 即可：

```bash
git clone https://github.com/ShineBreaker/pi-agenote.git ~/pi-agenote
stow --dir=~/pi-agenote --target=$HOME
```

作为 Guix-configs 子模块时，扩展落在
`dotfiles/mutable/agenote/.config/omp/extensions/agenote-hooks`，由该仓库的 stow 流程
统一纳管，不单独部署。

## 深入阅读

- [使用文档](docs/usage.md)：钩子与命令清单、注入机制、开关、部署、依赖
- [English README](README.md)
- [agenote 主仓库](https://github.com/ShineBreaker/agenote)
- [agenote-skills](https://github.com/ShineBreaker/agenote-skills)：信号清单与
  写入流程的真相源

## 许可证

MIT，见 [LICENSE](LICENSE)。

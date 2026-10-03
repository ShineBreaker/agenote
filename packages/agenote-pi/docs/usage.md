# pi-agenote 使用文档

本文档覆盖钩子与命令清单、注入机制、开关与部署。产品定位见
[README](README.zh.md)。

## 目录

- [钩子与命令](#钩子与命令)
- [记忆简报注入](#记忆简报注入)
- [经验总结的触发](#经验总结的触发)
- [双层开关](#双层开关)
- [子代理进程排除](#子代理进程排除)
- [错误处理与日志](#错误处理与日志)
- [依赖版本](#依赖版本)
- [OMP 扩展规范](#omp-扩展规范)
- [部署](#部署)
- [依赖](#依赖)
- [改源生效路径](#改源生效路径)

## 钩子与命令

四个事件钩子：

| 钩子            | 触发时机         | 作用                                                       |
|-----------------|------------------|------------------------------------------------------------|
| `context`       | 每轮 LLM 调用前  | 注入 agenote 记忆简报（指纹缓存，先剥旧块再写新块）        |
| `session_start` | 会话启动         | 清定时器并归零触发状态；首次启动把 argv 记进加载日志供排查 |
| `agent_start`   | 新一轮 turn 开始 | 取消 `agent_end` 武装的空闲兜底定时器                      |
| `agent_end`     | agent 响应结束   | 检测完成信号与空闲兜底，命中时注入 agenote-review 评估提示 |

健康度摘要由 pi-ui 扩展在欢迎框里显示，不经本插件。`session_start` 不再
打印状态摘要。

三个斜杠命令：

| 命令                 | 行为                                                              |
|----------------------|-------------------------------------------------------------------|
| `/agenote-summarize` | 注入一条 review 提示，让当前 agent 在本会话做经验总结与资料留痕   |
| `/agenote-curate`    | 注入一条策展任务提示，agent 按 agenote-curator skill 主导执行策展 |
| `/agenote-health`    | 直接跑 `agenote-cli health` 并打印输出                            |

`/agenote-curate` 不直接执行策展。插件只投递任务提示，策展的流程编排、
逐项审查与去留决策由 agent 完成。这样命令和 `agent_end` 的总结路径行为
一致，agent 手上有完整上下文。

前两个命令用 `deliverAs: "followUp"` 投递：agent 还在流式输出时排队，
空闲时立即送达。

## 记忆简报注入

每轮 LLM 调用前，插件执行：

```bash
agenote-cli context --mode session --host pi --budget 8000
```

输出用固定标签包裹后写到本轮第一条 user 消息开头：

```html
<!-- agenote-inject:start -->
简报正文
<!-- agenote-inject:end -->
```

两条实现细节决定了它不会污染会话：

- 写入前扫描所有消息（string 与 blocks 两种内容形态）剥掉旧标签块。宿主
  即使把注入过的消息持久化进会话历史，块也不会一轮轮叠加。未闭合的块从
  START 标签起整段切除。
- 简报为空时只剥不写。记忆被清空或语义开关关闭时，上一轮的简报自动撤下。

没有可挂载的 user 消息时放弃本轮注入，不报错。

简报缓存是单槽的，按 KB 侧指纹缓存：

- 指纹来源是 `MEMORY.org` 的 mtime 与 size，加上 `memories/` 目录树下所有
  文件的 mtime 与 size（`KB_ROOT` 未设时回落到 `~/Documents/Org/agenote`）。
- 指纹未变就重放缓存，不 spawn CLI。KB 变了才真跑一次。
- 文件列表超过 256 个时截断，尾部变更不触发刷新。
- CLI 失败按空正文处理并一并进缓存，避免每轮重试打出一串 spawn。
  KB 变更后指纹失效，自然会重试。

标签常量在代码里是 `INJECT_START` / `INJECT_END`，改动会让历史块无法
剥离。

## 经验总结的触发

`agent_end` 有两条触发路径。

**完成信号。** 只读用户最后一条消息，不扫全会话历史。命中完成词表
（搞定、做完了、测试通过、done.、ship it 等）且距上次触发超过 5 分钟
（`DEBOUNCE_MS`）时，注入一条 review 提示。词表来自
agenote-review skill 的 `references/triggers.md`，代码里改要同步那份文件。

注入的提示带 `<agenote-hook>` 标记。下一轮 `agent_end` 会跳过含这个标记
的消息，断开自触发反馈环。

**空闲兜底。** `agent_end` 时武装一个 5 分钟（`IDLE_FALLBACK_MS`）的定时器。
到点后会话仍无新 turn、本会话从未被完成信号触发过、且至少处理过一轮真实
工作，就注入同一条 review 提示。覆盖夜间无人值守那种没人说「完成」的场景。
两个条件任一命中就不触发：完成信号触发过一次（`signalTriggered`），
或兜底已触发过。

`agent_start` 一到就取消定时器。空闲计时只能从会话真正安静下来那刻起算，
否则长输出 turn 期间定时器会到点误触发。

## 双层开关

| 层       | 开关位置                                                            | 生效方式                                                                      |
|----------|---------------------------------------------------------------------|-------------------------------------------------------------------------------|
| 语义开关 | agenote 配置 `[injection].enabled` / `[injection.hosts].pi_enabled` | CLI 自身遵守，关闭时输出零字节；每轮生效，不需重启                            |
| 物理开关 | `<omp 配置根>/agenote-pi.json` 的 `"injectEnabled": false`          | 本扩展对 `context` 事件完全透明，不 spawn CLI；init 时读一次，需重启 omp 会话 |

改注入行为优先改 agenote SCHEMA。物理开关只在需要快速临时断开时用。

`agenote-pi.json` 还支持 `"budget": N`，即单次注入的字符预算，默认 8000，
透传给 CLI 的 `--budget`。必须是正整数。文件缺失等于默认开；解析失败
容错回退默认值并记加载日志。

配置根由 `PI_CONFIG_DIR` 决定，未设时是 `~/.config/omp`。

## 子代理进程排除

`isSubagentProcess()` 命中任一条件即判定为子代理进程，所有钩子直接返回：

- 环境变量 `PI_BLOCKED_AGENT` 非空（omp 的 task 工具会设置）。
- argv 含 `--no-session`（旧版 pi 的 subagent 标志）。
- argv 同时含 `--mode`、`json` 和 `-p`。omp subagent 走 JSON 协议；要求
  `--mode json` 同时出现是为了不误杀用户自己的一次性 `omp -p "<prompt>"`
  命令。

omp 的确切 spawn 标志没有定论，所以首次 `session_start` 会把 argv 与判定
结果记进加载日志，便于按真实运行情况收紧。

## 错误处理与日志

| 场景                          | 行为                                     |
|-------------------------------|------------------------------------------|
| CLI 缺失、执行失败或超过 5 秒 | 记加载日志，本轮跳过注入                 |
| 注入路径内部抛异常            | 记加载日志，返回 undefined，宿主不受影响 |
| `agenote-pi.json` 解析失败    | 回退默认配置并记加载日志                 |
| `agenote-cli health` 失败     | 打印一行失败提示，退出码 0               |

加载日志路径是 `<omp 配置根>/agent/extensions/.load-errors.log`。
omp 会静默吞掉扩展抛出的错误，这个文件是唯一的排查入口。
简报查询的超时是 5 秒（`BRIEFING_TIMEOUT_MS`），`/agenote-health` 走的
`runKb` 超时是 30 秒。

## 依赖版本

注入功能要求 agenote CLI 带 `context` 子命令（2026-09 之后的版本）。
旧版 CLI 没这个子命令时会静默降级并记 `.load-errors.log`，其余钩子不受影响。

## OMP 扩展规范

omp 扩展是单文件 TypeScript，无需构建步骤：

- omp 运行时自动扫描 `~/.config/omp/extensions/*/index.ts`
- 内置 TypeScript 运行时，直接加载 `.ts` 源码，类似 tsx 或 bun
- `config.yml` 中无需显式注册扩展
- `ExtensionAPI` 类型由 omp 运行时作为全局类型注入，无需 import
- 所有 import 都是 Node.js 内置模块（`node:child_process` 等），无第三方
  npm 依赖

入口签名：

```typescript
export default function init(pi: ExtensionAPI): void {
  // ...
}
```

## 部署

### Guix-configs 钉版本部署（主流程）

扩展随 agenote monorepo 以 `agenote-pi-v<版本>` tag 发布 GitHub Release
tarball。`Guix-configs` dotfiles 不再使用 submodule，改为在 `agenote.lock`
里钉版本，由 `sync-agenote.sh` 拉取 Release 产物、校验 sha256、解包到内容
寻址缓存后铺软链。部署后 omp 扩展目录里是逐文件软链：

```
~/.config/omp/extensions/agenote-hooks/index.ts → 缓存中的 agenote-pi 源
```

机制细节见 monorepo 根 AGENTS.md §7 与 docs/adr/0005。

### 手动安装

没有 dotfiles 时，从对应 tag 的 GitHub Release 下载
`agenote-pi-<版本>.tar.gz`，把 `index.ts` 放到
`~/.config/omp/extensions/agenote-hooks/` 下即可，omp 会自动扫描。

### 从源码开发

clone [agenote monorepo](https://github.com/ShineBreaker/agenote)，扩展源码
在 `packages/agenote-pi/index.ts`。生效方式见文末「改源生效路径」。

## 依赖

- **agenote CLI**：本插件通过 `agenote-cli` 这个 shim 执行 `context` 与
  `health` 查询。shim 由 agenote 包的 console_scripts 产出，Guix profile 下
  在 `~/.guix-home/profile/bin/agenote-cli`。必须先装好并在 PATH 里。
- **omp（oh-my-pi）**：扩展宿主。

## 改源生效路径

omp 直接加载 `.ts` 源码，改源即生效，下次 omp 会话启动时重新扫描。

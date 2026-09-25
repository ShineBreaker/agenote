# pi-agenote — oh-my-pi (omp) agenote 集成扩展

> oh-my-pi (omp) 的 agenote 集成钩子扩展。在 omp 会话中注入 agenote 记忆简报、
> 检测任务完成信号触发经验采集、提供 `/agenote-*` 斜杠命令快捷入口。

本仓库是 [agenote](https://github.com/ShineBreaker/agenote) 跨 Agent 经验平台在
omp（oh-my-pi）侧的集成扩展，作为 `Guix-configs` 的 Git 子模块嵌入。

## 扩展功能

| 钩子/命令            | 触发           | 作用                                                                 |
| -------------------- | -------------- | -------------------------------------------------------------------- |
| `context`            | 每次 LLM 调用前 | 注入 agenote 记忆简报（指纹缓存幂等重写，先剥旧块再注新块）          |
| `session_start`      | 会话启动       | 重置触发状态（健康度摘要由 pi-ui 扩展在欢迎框中显示，不经本插件）    |
| `agent_end`          | agent 响应结束 | 检测"任务完成信号"，命中时注入 agenote-review 评估提示（含留痕）     |
| `/agenote-summarize` | 斜杠命令       | 在当前会话触发经验总结 + 资料留痕                                    |
| `/agenote-curate`    | 斜杠命令       | 执行 agenote 策展（健康+去重+归档+权重重分配）                       |
| `/agenote-health`    | 斜杠命令       | 显示 agenote 健康度报告                                              |

信号清单、写入流程、卡片格式由 [agenote-skills](https://github.com/ShineBreaker/agenote-skills)
的 `agenote-{base,curator,review}` skill 提供，本插件只做"事件触发 + 命令快捷入口"，
避免与 skill 重复维护。

## 记忆简报注入（context 事件）

每次 LLM 调用前，把 `agenote context --mode session --host pi --budget N` 的输出以
固定标签（`<!-- agenote-inject:start -->...<!-- agenote-inject:end -->`）包裹后注入
首条 user 消息头部：

- **幂等重写**：每次注入前先全量剥离旧注入块再注新块——无论宿主是否把注入过的消息
  持久化进会话历史，重复处理都不会累积；
- **零 spawn 缓存**：按 KB 侧 `MEMORY.org` + `memories/` 的 mtime+size 指纹缓存 CLI
  输出，指纹未变直接重放、不 spawn CLI；KB 变更才重跑；
- **自动撤下**：CLI 零字节输出（记忆为空或语义开关关闭）时只剥不注，旧简报自动撤下；
- **子代理豁免**：复用 `PI_BLOCKED_AGENT` + argv 启发式，subagent 进程完全不参与；
- **错误静默**：CLI 缺失/失败/超时（5s）只记加载日志（`.load-errors.log`），跳过本轮
  注入，绝不炸宿主会话。

### 双层开关

| 层 | 开关 | 生效方式 |
| --- | --- | --- |
| 语义开关（真相源） | agenote 配置 `[injection].enabled` / `[injection.hosts].pi_enabled` | CLI 自身遵守，关闭时输出零字节；每轮生效，无需重启 |
| 物理开关（快速断开） | `~/.config/omp/agenote-pi.json` 的 `"injectEnabled": false` | 本扩展对 context 事件完全透明（不 spawn CLI）；init 时读一次，需重启 omp 会话生效 |

改注入行为优先改 agenote SCHEMA（语义层）；物理开关只在需要快速/临时断开时使用。
`agenote-pi.json` 还支持 `"budget": N`（单次注入字符预算，默认 8000，透传 CLI
`--budget`）。缺文件 = 默认开；解析失败容错回退默认并记加载日志。

### 依赖版本

注入功能要求 agenote CLI 含 `context` 子命令（2026-09 后的版本）。旧版 CLI 无该
子命令时扩展静默降级（记 `.load-errors.log`），其余功能不受影响。

## OMP 扩展规范

omp 扩展是**单文件 TypeScript**，无需构建步骤：

- omp 运行时**自动扫描** `~/.config/omp/extensions/*/index.ts`
- 内置 TypeScript 运行时，直接加载 `.ts` 源码（类似 tsx/bun）
- `config.yml` 中**无需显式注册**扩展
- `ExtensionAPI` 类型由 omp 运行时作为**全局类型**注入，无需 import
- 所有 import 都是 Node.js 内置模块（`node:child_process` 等），无第三方 npm 依赖

入口签名：

```typescript
export default function init(pi: ExtensionAPI): void { ... }
```

## 部署

### 作为 Guix-configs 子模块（主流程）

本仓库登记为 `Guix-configs` 的 `dotfiles/mutable/agenote/.config/omp/extensions/agenote-hooks`
子模块，由 `blue stow` 统一纳管：

```bash
git submodule update --init dotfiles/mutable/agenote/.config/omp/extensions/agenote-hooks
blue stow agenote           # 部署软链
blue stow --restow agenote  # 重建
```

部署后：

```
~/.config/omp/extensions/agenote-hooks/index.ts → 本仓库源（逐文件软链）
```

### 独立部署（原生 stow）

```bash
git clone https://github.com/ShineBreaker/pi-agenote.git ~/pi-agenote
stow --dir=~/pi-agenote --target=$HOME
```

## 依赖

- **[agenote](https://github.com/ShineBreaker/agenote) CLI**：本插件通过调用 agenote 的
  轻量 shim（`~/.local/bin/agenote-cli`）执行 health/context 简报，必须先安装 agenote CLI。
- **omp（oh-my-pi）**：扩展宿主。

## 改源生效路径

omp 直接加载 `.ts` 源码，**改源即生效**（下次 omp 会话启动时重新扫描）。

## 许可证

MIT，见 [LICENSE](LICENSE)。

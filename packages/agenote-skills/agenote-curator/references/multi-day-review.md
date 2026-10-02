# 多日跨平台回顾（agent 主导编排）

适用：用户要求「总结最近 N 天**所有平台**的对话」时的端到端流程。单会话内的经验采集走 `agenote-review`；
本文件只管「跨天 × 跨平台」的批量回顾。首次成形于 2026-09-14 的 14 天回顾（zcode/omp/crush/opencode/codex/claude + hermes 会话，3.5MB 原始语料）。

> 通用模式（不限 agenote、适用于任意大语料分片提炼：切片纪律 / 只读 worker 任务书要素 / 幂等落库）见 skill `corpus-harvest-fanout`
> （`~/.local/share/hermes/skills/autonomous-ai-agents/corpus-harvest-fanout/`）。本文件是它在 agenote KB 上的具体化。

## 步骤

**0. 定窗口。** 默认最近 14 天，逐日算出 `YYYY-MM-DD` 列表。
完成判据：拿到 N 个具体日期串。

**1. 抽取。** `extract` 只吃单日 `--date`，不支持范围，必须循环：

```bash
for d in $(seq 0 13); do
  day=$(date -d "$(date +%F) -$d day" +%F)
  agenote extract --source all --date "$day" --limit 0
done
```

完成判据：`conversations/<日期>/*.org` 全部存在且 >0 字节；每源的 `total_facts` 与 `noise_filtered` 已打印。
`noise_filtered` 占比异常低（<10%）时先确认跑的是 v0.1.9 以上（噪声过滤在该版本落地）。

**2. 压缩降噪（信噪比闸门）。** 剥掉 `[reasoning]` 段与 `[tool: *]` 占位，实测可降到原体积 ~48%。
`hermes.org` 在 v0.1.10 之前会把整个记忆库按日重复一份（117 条 × 14 天），旧版本直接跳过该文件。
完成判据：压缩产物总量 ≤ 原文 60%，且随机抽两天能 grep 到已知的真实结论。

**3. 分片外派（token 关键）。** 先 `find -printf '%s %p\n' | sort -rn` 看单文件体积，按体积切片、单份 ≤300KB
（约 100k token），不要按天数均分（单日可差 10 倍）。每份任务书必须自包含：

- 文件清单 + 「大文件用 python 分块读，别整文件 read_file」；
- 固定 JSON 产出：`threads`（工作主线，每条 ≤60 字）/ `cards`（≤4 张候选，含 title/category/tech/type/entry/
  summary/body/evidence/dedupe）/ `prefs` / `env_improvements`；
- 「只用文件里真实出现的内容，不推测，不确定标 `存疑`」「**不要写 KB，只提议**」；
- 要求它自己对每张候选卡跑 `agenote search` 查重，命中就写 `已有: <ID> <标题>`。

完成判据：每个切片都返回四段结构，`cards` 不超过 4 张。

**4. 汇总落卡。**

- 幂等：先取 `agenote list --all --json` 的标题集合，已存在的标题直接 skip（脚本崩溃重跑不会重复写卡）。
- **单轮新增卡有 Andon 上限（>10 张即暂停）**：候选超出时把清单交给用户拍板，不要自行放量；用户明确
  「全部写入」时不设限，但要在报告里写明本次新增总数。
- 正文保留 `来源: <日期> <平台> <会话/文件>` 溯源行，并附原文 1-2 句证据。
- 写卡归因：脚本里 `AGENOTE_AGENT=hermes agenote add ...`（否则归到默认 agent）。

完成判据：每张写入的卡都能回指到具体日期与平台。

**5. 挂链。** 先按 `category` 限定候选，再把新卡挂到**本类别 hub**（本类别内被引用最多的卡）。
完成判据：孤立率不高于回顾开始前。

**6. 收尾。** `agenote lint --fix` → `agenote reindex` → 提交；「上轮遗留改动」与「本轮回顾产物」分两个 commit。

## 已知坑

| 坑 | 表现 | 处置 |
| --- | --- | --- |
| extract 只吃单日 | `--date` 传范围不生效 | 循环单日 |
| hermes 源重复导出（< v0.1.10；源自 v0.1.12 退役，新抽取不再产出） | 每天一份 117 条记忆 | 升级，或跳过存量 `hermes.org` |
| 相似度自动挂链 | 42 张新卡里 38 张被连到跨类别无关 hub | 按 category 限定候选 + 挂本类别 hub |
| 子 agent 的 `evidence` 字段可能是 list | 批量脚本 `.strip()` 崩 | `str()` 兜底 |
| `agenote list --all --json` 输出 >50KB | 工具截断导致 JSON 解析失败 | 重定向到文件再解析 |
| `update --append-to` 语义 | 是「插在章节标题之后」，不是替换；连续 append 会倒序 | 修正已写入内容要整段重写 |
| `update --stdin` 语义 | 追加到文件末尾 | 同上 |
| 标题配对 | 用 `zip(sorted(A), sorted(B))` 配 ID 与文件会串位 | 按 **ID 映射**，别赌排序一致 |

## 何时不跑本流程

- 单日或单平台 → 直接 `agenote extract` + `agenote-review`；
- KB 卡片总数 < 30 → 收益小于成本；
- 距上次回顾不足一周且期间没有长会话（抽 1-2 天验证即可）。

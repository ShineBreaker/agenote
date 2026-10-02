# 策展参考手册 — 质量标准与机制细节

> 策展**工作流编排**（Step 0–10）在 SKILL.md，本文件承接细节层：卡片质量标准、dream/reconcile/权重机制、数据源注册。操作时按需查阅，不必通读。

## 记忆分层模型

从对话历史提取经验时，遵循以下分层：

| 层级             | 存储位置                       | 内容                                        |
| ---------------- | ------------------------------ | ------------------------------------------- |
| **经验卡片**     | `~/Documents/Org/experiences/` | 跨会话可复用的 bug 修复、配置陷阱、方案对比 |
| **模式文件**     | `~/Documents/Org/patterns.org` | 3 次以上同类经验的通用规则总结              |
| **留在对话历史** | 原始对话文件                   | 任务进度、TODO、一次性操作记录              |

详细经验保留在卡片，常用规则压缩到 `patterns.org`。策展时不要机械堆长文缓存；应维护短、准、可执行的 pattern。

## 归档质量标准

新增卡片应符合以下结构：

### 必须包含

1.  `entry_type` / `type` — debug/research/refactor/config
2.  `title` — 包含结论，一眼能看出解决了什么问题
3.  `summary` — 一句话总结
4.  任务描述 / 执行过程 / 关键发现 三个章节

### 纠错类条目额外要求

- 原始问题
- 用户纠错反馈链
- 最终正确回答或修复
- 这次到底错在哪里
- 下次开始前自检

### 长期事项类条目额外要求

- 为什么值得记录
- 注意事项列表
- 适用场景和例外
- 建议行动

### 飞升模式复盘额外要求

- 前几轮失败的根因
- 检索过的知识源和真实文件
- 最终采用的最强方案
- 是否需要新增经验卡片或修补 pattern

### 写作风格

- **声明式事实**：✓ "Guix swap-space 需同时在 operating-system 声明" — ✗ "总是要在 operating-system 中声明 swap"
- **记录坑点**：重点关注"没想到的地方"，而非流水账过程
- **标题包含结论**：一眼能看出解决了什么问题
- **自包含**：每张卡片能独立理解，不依赖外部上下文
- **时效性标注**：可能过时的声明附上验证日期（"截至 YYYY-MM"）
- **置信度标注**：不确定结论标记 `(推测)`/`(单源)`，确定结论可省略

### 分层决策标准

写入经验卡片前，先判断：

- **这是否是跨会话可复用的知识？** 是 → 经验卡片；否 → 继续判断
- **这是否是通用规则/模式？** 是 → 模式文件；否 → 经验卡片
- **一次性任务进度？** → 留在对话历史，不写入知识库

## 质量评判标准

### 合格卡片应满足

- [ ] 有明确的 CATEGORY / TECH / TYPE / OWNER 元数据
- [ ] 任务描述：一句话说清楚做了什么、为什么
- [ ] 执行过程：包含背景、根因、方案、验证
- [ ] 关键发现：≥1 条可复用的经验教训
- [ ] 使用 Org mode 格式（非 Markdown）
- [ ] 代码块用 `#+begin_src language` 来标明
- [ ] 标题为声明式结论（"X 情况下 Y 导致 Z"），非指令（"总是要..."）
- [ ] 记录的是坑点/教训，而非任务流水账
- [ ] 自包含：不依赖外部上下文即可理解
- [ ] 涉及版本/API/配置的内容标注了验证日期
- [ ] 不确定结论标注了置信度（`(推测)`/`(单源)`）

### 低质卡片特征

- 只有标题没有正文
- 执行过程中只有结论没有过程
- 缺少关键发现/经验教训
- 格式错误（Markdown 污染）
- 标题为指令式（"Always do X"）而非声明式
- 内容为一次性任务进度，无可复用价值
- 与已有卡片高度重复（重叠率 \> 80%）
- 依赖外部上下文才能理解（缺乏自包含性）
- 与同类卡片结论矛盾但未标注关系

## 补充策略优先级

| 优先级 | 场景                 | 行动                                |
| ------ | -------------------- | ----------------------------------- |
| P0     | 完全空白的类别       | 从对话历史 + 最近工作提取           |
| P1     | 已有类别缺关键类型   | 补充缺失类型的卡片                  |
| P2     | 薄弱领域（≤2 张）    | 回顾该领域工作，补录经验            |
| P3     | 质量缺陷卡片         | 补充缺失章节、修复格式              |
| P4     | 纯 AI 类别缺人类视角 | 添加 human/collaborative owner 卡片 |
| P5     | 陈旧卡片             | 评估是否需要归档或更新              |
| P6     | 矛盾未调和           | 按矛盾调和规则处理                  |
| P7     | 孤立卡片缺关联       | 补充 `[[file:...]]` 链接            |

## dream 综合机制

### 评分算法（IDF × √df × 形态学，TF 作 tie-breaker）

- IDF = log(total_facts / df)：稀有词天然高分
- √df：温和补偿高频好词，不至于被大量 df=MIN_TERM_FREQ 长尾淹没
- 形态学权重：含 `-`/`_` 的代码标识符 +2.0（CJK 二字虚词 ×0.4 降权）
- TF（tie-breaker，不进主评分）：score 并列时 TF 高的候选优先，消除随机排序。每个候选 dict 含 `tf_total` 字段供参考
- 实测 top 候选稳定为高质量项目标识符（guix-configs / self-improving / host-spawn 等），对话虚词（removed / understand / 让我）已被 IDF + 形态学 + 停用词表三重压制

### 参数（与 `agenote dream` CLI 同源）

- `window_days`：时间窗口（天），默认 90 覆盖 ~90% facts。0=不过滤。`0/7/30/90/180` 窗口在真实数据上分别幸存 100%/7%/37%/93%/100% facts
- `offset`：跳过前 N 个候选（多轮抽取跳过噪声词）。**注意 offset 不稳定**：候选排序随 reconcile 索引更新漂移，`report.snapshot_hash` 标识本次候选集指纹——两次调用指纹不同即说明排序已变，同一 offset 可能指向不同候选
- `limit`：本次最多返回 N 个候选（默认 5）
- 无 timestamp 的 fact **默认保留**，不受窗口影响

### 为什么需要 trace

reconcile 索引层 `content` 是 extractor 建索引时的**截断摘要**（opencode/zcode：user 截 1000 字、assistant 截 2000 字、tool/patch 退化为 `[tool: name]` 标记）。要判断一个 dream 候选是否真的有具体经验价值，必须读真实完整对话——这正是 `agenote trace` 的职责。dream 候选的 `score` 反映"统计上像经验词"，`agenote trace` 让你确认"语义上确实是有用经验"。

**降级行为**：只有实现了 trace_session 的源（opencode/omp/zcode）能返回完整对话；crush/codex/claude 未实现，trace 降级返回索引层摘要——对这些源，深入判断只能回到 `conversations/<date>/` 原始文件。

### token 经济与质量门槛

- dream 候选 ≤limit 条，每条只暴露代表事实正文（截断版）；不需读全部 reconcile 事实，要深入按需调 trace
- score 高 ≠ 值得沉淀。优先综合 `score` 高**且** `representative_content` 含具体技术细节的候选；纯流程性/工具名词性的候选丢弃

### 两轮评估工作流示例

```bash
# 第一轮：取 top 候选
agenote dream --limit 5 --json
# 逐条看 score/term/frequency；score<30 直接跳过
# 需深入判断：agenote trace --id <source_trace> 读完整对话
# 决定：
#   写 KB      → agenote add ...（正文引用 source_facts 保留溯源）
#   复用已有   → agenote touch <现有卡片ID>
#   其余       → 跳过 / 矛盾调和

# 第二轮：前 5 个都不满意就跳页
agenote dream --offset 5 --limit 5
```

## reconcile 行为细节

- **三重只读**：每个 extractor 内部 SQLite `mode=ro` + `PRAGMA query_only=1` + 仅 SELECT；JSONL 仅读；文件缺失返回 `([], [msg])` 不抛异常
- **元消息过滤**：写入层自动过滤 TodoWrite / system-reminder / checkpoint 等源自 harness 注入而非用户经验的内容（判据见 `agenote.core.is_noise_fact`）
- **KB 优先**：reconcile 抽到的事实若与 KB 已有卡片同名，跳过不索引
- **去重**：同一事实可能从多个 DB 出现（如 crush 全局 + bind-mount 项目级），按 `id` 去重保留先出现的
- **0-fact 警告**：extractor 跑通但抽不到任何事实（数据未生成 / schema 漂移）→ 自动报 `[warn]`
- **不写回源**：绝不写回任何外部 agent 的原始数据（DB/JSONL 只读）
- **不污染 KB**：reconcile 事实进 `.reconcile/index.json`（独立目录），不进 `experiences/`

## 权重机制（spec §7）

检索时 `agenote search` 跨域扫描人类（weight 默认 1.5）+ agent（weight 默认 1.0）+ reconcile（weight 0.6-0.7）卡片，最终分数 = 原始相关度 × WEIGHT。

**WEIGHT 是派生值**：索引层（`_card_dict`）按以下公式计算，`reindex` / `touch` 等任何索引重建路径自动重算，卡片文件中的 WEIGHT 属性已废弃（新卡不再写入）：

```
WEIGHT = 基础权重 × 使用系数 × 新鲜度系数

基础权重:   人类=1.5, agent=1.0, reconcile=0.6-0.7
使用系数:   1 + 0.1 × min(USAGE_COUNT, 10)   # 常用的提升，上限 +1.0
新鲜度系数: last_used 超 STALE_DAYS(30天) → 0.8，否则 1.0
```

**reconcile 权重梯度**：opencode / omp / zcode / crush = 0.7（`weights.reconcile_default`，自家或信任度高）；codex / claude = 0.6（外部源，`weights.reconcile_default + weights.external_delta`，略低避免淹没 KB）。

## 数据源注册

### 会话抽取源（extract，当前 6 源）

opencode / crush / codex / claude / omp / zcode。注意 pi 宿主在 extract 侧的注册名是 `omp`（与 scan-memories 侧的 `pi` 是同一宿主两个注册名）。hermes 会话源自 v0.1.12 退役（SQLite 布局不再跟随），仅保留 `scan-memories --source hermes` 只读记忆扫描。抽取器实现在 `agenote/extract/`，源列表与路径以代码为准。新增源参考任一现有 extractor 的签名 `() -> tuple[list[ReconciledFact], list[str]]`，在 `agenote/extract/base.py` 的 `_resolve_extractors()` 注册即可。

### 记忆库扫描源（scan-memories，6 源）

实现于 `agenote/memscan.py`，读各 agent 持久记忆文件（区别于会话源），注册于 `memscan.SOURCES`。路径解析一律 env 优先 → config.toml `[memories.sources]` → XDG 占位符 → 默认目录：

| 源       | env                   | 默认根                    | 布局                          |
| -------- | --------------------- | ------------------------- | ----------------------------- |
| zcode    | `ZCODE_MEMORIES_DIR`  | `~/.zcode/cli/memories`   | projects/\<slug\>/memory/*.md |
| claude   | `CLAUDE_CONFIG_DIR`   | `~/.claude`               | projects/\<slug\>/memory/*.md |
| codex    | `CODEX_HOME`          | `~/.codex`                | memories/**/*.md              |
| pi       | `PI_CODING_AGENT_DIR` | `$XDG_CONFIG_HOME/omp`    | agent/memory/**/*.md          |
| reasonix | `REASONIX_HOME`       | `$XDG_DATA_HOME/reasonix` | projects/\<slug\>/memory/*.md |
| hermes   | `HERMES_HOME`         | `$XDG_DATA_HOME/hermes`   | memories/*.md（`§` 分节）     |

目录式源跳过 MEMORY.md 索引；frontmatter 宽容解析；同样纯只读。

---
name: agenote-review
description: 会话后经验采集与留痕。**触发信号**：agenote-hooks 插件检测到完成信号 / 用户触发 `/agenote-summarize` / 长任务结束前的例行评估 / 用户纠正了你 / 排查 >2 步才定位根因 / 发现了比初始方案更优的写法。**当上述任一信号出现时立即调用本 skill** 做"经验信号识别 + ENTRY_TYPE 判定 + 留痕决策树"，按其内部规则写卡片或 touch 已有卡片。基础用法见 `agenote-base`。
---

# agenote-review — 会话后经验采集与留痕

会话结束时（或检测到完成信号时），评估是否有可记录的经验，并对用到的资料留痕。

> 全部操作通过 `agenote` CLI（`~/.local/bin/agenote`）完成。CLI 命令速查与任务前预检见 `agenote-base`。

## 触发时机

- agenote-hooks 插件检测到完成信号（完整信号清单见 [references/triggers.md](references/triggers.md)，它是插件单一真相源）
- 用户主动 `/agenote-summarize`
- 长会话结束前的例行评估

## 评估决策树

```
本次对话是否有可记录的经验信号？
│
├─ 是 → 判断 ENTRY_TYPE
│   ├─ 用户纠正/走弯路/误判 → agenote add --entry mistake ...
│   ├─ 查到的有用知识/方案 → agenote add --entry note ...
│   └─ 多轮试错后的最优方案 → agenote add --entry ascended ...
│
└─ 否 → 还要评估"留痕"
    │
    本轮用到了哪些外部资料？
    ├─ 来自 agenote/人类KB 的已有卡片 → agenote touch <ID>
    └─ 来自联网的新知识
        ├─ 已确认有用（实际应用到代码/答案）→ agenote add --entry note ...
        └─ 仅浏览未采用 → 不记录（避免噪音）

如果既无经验信号、又无留痕需求 → 明确回复"本次无可记录经验"
```

## 可记录信号清单

### 经验信号（触发 add）

- **用户纠正**：用户指出 agent 的错误（"不对"、"应该是"、"重新做"）
- **踩坑**：agent 遇到报错、卡住、反复调试
- **更优方案**：发现比当前做法更好的方式
- **项目决策**：确定某技术选型、架构方向

写入轻重选择见 [references/write-decision.md](references/write-decision.md) 与 [references/lightweight-writing.md](references/lightweight-writing.md)。

### 记忆信号（写入 MEMORY.org）

| 信号类型 | 记忆类型    | 关键词/信号                          | 写入命令                                               |
| -------- | ----------- | ------------------------------------ | ------------------------------------------------------ |
| 用户画像 | user        | 用户角色/目标/稳定背景，无法从代码推导 | `agenote memory --add --type user`                     |
| 偏好表达 | feedback    | "我喜欢..."、"不要..."、"停..."      | `agenote memory --add --type feedback`                 |
| 行为纠正 | feedback    | 用户纠正了你的工作方式（非技术错误） | `agenote memory --add --type feedback`                 |
| 习惯模式 | feedback    | 同一偏好出现 ≥2 次                   | `agenote memory --add --type feedback`                 |
| 项目决策 | project     | 不可从代码推导的项目级决策/状态      | `agenote memory --add --type project --project <slug>` |
| 环境约束 | environment | 机器/OS/工具链的不可推导约束         | `agenote memory --add --type environment`              |
| 外部指针 | reference   | 外部系统/文档/资源的位置信息         | `agenote memory --add --type reference`                |

`--project <slug>` 填项目/工作区 slug（目录名或宿主 `projects/<slug>` 原名）；带分区键的条目只在对应项目上下文注入。memory 写入有 secret 门禁——命中密钥形态即拒写，确认误判用 `--allow-secret` 豁免；含敏感内容但需留存时用 `--sensitivity <LEVEL>` 标记（不注入不投影，仅存 SSOT）。

**MEMORY vs KB 边界**："你怎么做"（风格/流程/工具选择偏好）→ MEMORY；"你做错了"（事实/技术错误）→ KB。同一事件可能两者并存。技术性纠正（"正则写错了"、"参数传反了"）只写 KB；普通确认（"好的"、"行"）不触发任何写入。

## 写入时机

**必须写入**：非显而易见的 bug（排查 >2 步）、环境特殊行为、配置踩坑、用户纠正。

**不必写入**：语法拼写修正、文档有明确答案、一次性操作、已有经验完全覆盖、用户仍在纠正中（等尘埃落定再记完整链）。

**增量原则**：任务完成后立即触发，不累积多任务统一总结；一个任务一张卡片，不把多个不相关经验塞进一张卡片。

## 写入流程

1. `agenote search` 去重 — 已有卡片覆盖时补充修正，不新建重复卡
2. 矛盾检测 — 新发现是否与已有卡片/pattern 矛盾
3. `agenote fields` 查看标签，优先复用现有 category/tech，只有全新领域才创建新类别
4. `agenote add` 写入卡片（CLI 用法见 `agenote-base`）
5. `agenote lint` 校验格式
6. 传播联动 — 检查受影响的 pattern/卡片；隐含关联的卡片 `agenote connect` 建立双向链接
7. 如涉及偏好/项目变化，按记忆信号表写入 `agenote memory`

### `mistake` 卡片必须覆盖

1. 原始问题
2. 用户纠错反馈链
3. 这次错在哪里
4. 最终正确处理
5. 下次自检
6. 不确定结论标注 `(推测)`/`(单源)`，可能过时标注验证日期

### `note` 卡片必须覆盖

1. 事项内容
2. 为什么值得长期保留
3. 适用场景和边界
4. 后续行动

完整写作规范（声明式标题、自包含、时效标注）见 [references/writing-guide.md](references/writing-guide.md) 和 [references/ai-first-rules.md](references/ai-first-rules.md)。

## Entry type 映射

| 来源语义     | `--entry`  | 推荐 type             | 推荐 owner |
| ------------ | ---------- | --------------------- | ---------- |
| 用户纠错     | `mistake`  | `debug` / `config`    | `collab`   |
| 长期注意事项 | `note`     | `workflow` / `config` | `ai`       |
| 飞升模式复盘 | `ascended` | `debug` / `workflow`  | `collab`   |

轻量写入（inbox / append / 不写）的分档判据见 [references/write-decision.md](references/write-decision.md)。

## 模式归纳

同类经验 ≥3 次 → 晋升为 pattern。晋升不删除原卡片。

**模式结构**（≤5 行）：

```
** <结论性标题>
   <一句话声明式规则>。
   适用：<场景>
   例外：<反例或边界条件>
   参考：<经验卡片 ID>
```

**模式即时修补**：使用 pattern 时发现过时/不完整/有误，立即修补并标注修订原因，不要等待用户提醒——过时 pattern 比没有 pattern 危害更大，它带着"已验证"的光环误导后续判断。

## 回顾机制

定期（建议每月）执行以下回顾，可手动也可由 `agenote-curator` 策展时一并完成：

1. **知识内化检查** — 浏览最近 10 张卡片，这些经验是否已成为默认行为？
2. **模式有效性** — patterns.org 中的模式是否已被新实践推翻？
3. **连接补全** — 扫描孤立卡片（无 connect 链接），评估是否建立关联
4. **记忆更新** — MEMORY.org 与 project memory 是否反映当前真实偏好和项目状态

## 飞升模式

同一问题被纠正 ≥2 次仍无法解决 → 进入飞升模式：全面检索知识源 → 筛出最接近经验 → 解释失败原因 → 给出最强方案 → 写入经验卡片。

详细步骤见 [references/ascended-mode.md](references/ascended-mode.md)。

## 详细参考

- [经验信号检测触发器](references/triggers.md) — 完整信号清单（agenote-hooks 单一真相源）
- [写入决策树](references/write-decision.md) — 轻量 vs 完整卡片
- [轻量写入模式](references/lightweight-writing.md) — 快速捕获与补充
- [写作指南](references/writing-guide.md) — 传播联动、自包含、时效性、矛盾检测
- [AI-First 卡片规则](references/ai-first-rules.md) — 自包含上下文、摘要、置信度标注
- [飞升模式详解](references/ascended-mode.md)

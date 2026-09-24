# agenote

跨 agent 知识库工具：把多个 AI agent 的对话与沉淀经验统一为可检索的 Org 知识库，
供人与 agent 复用。人类域与 agent 域分离。

## Language

### 知识库结构

**experiences 卡片（经验卡片）**:
一张 Org 文件，记录一条可复用的经验/知识；知识库的权威原子单元，存于 `experiences/`。
_Avoid_: 笔记、note、card（泛指时）、entry

**KBContext（知识库上下文域）**:
一个完整知识库域的路径与权重封装。人类域（`default_context`）与 agent 域（`agenote_context`）分离；agent 域卡片默认检索权重更低，避免淹没人类权威经验。
_Avoid_: 配置、config、环境、session

**inbox（快速捕获）**:
未分类的临时捕获条目，待整理为 experiences 卡片。
_Avoid_: 草稿、draft、暂存

**MEMORY（记忆索引）**:
`feedback`/`project`/`reference`/`deprecated` 四类长期记忆的 Org 索引；区别于 experiences 卡片（后者是具体经验，前者是跨卡片的偏好与约束）。
_Avoid_: 笔记库、knowledge base、记忆库

### 跨 agent 溯源

**source_agent（写入者）**:
记录一张卡片由哪个 agent 写入，供跨 agent 检索与健康度统计。人类手写卡片留空。取自 `AGENOTE_AGENT` 环境变量。
_Avoid_: 来源、author、owner（`owner` 另有语义：human/ai/collab）

### 经验沉淀与抽取

**reconcile（经验沉淀）**:
从外部 agent 的 memory store 抽取**已沉淀的经验**，写入只读索引 `.reconcile/index.json`，**不进权威 `experiences/`**。低权重、冲突时 KB 优先。
_Avoid_: 同步、sync、抽取（与 extract 易混）

**ReconciledFact（外部事实）**:
reconcile 抽取的一条只读事实。**无对应 .org 文件**，只活在 `.reconcile/index.json`，作为检索辅助。
_Avoid_: 卡片（它不是 experiences 卡片）、记录、record

**extractor（对话抽取器）**:
从某 agent 的原始对话（SQLite/JSONL）抽取为 Org 文件，供人/agent 提炼**新**经验。抽取的是**原始对话**，区别于 reconcile 抽取的已沉淀经验。
_Avoid_: 解析器、parser、importer、同步器

**turn（对话回合）**:
抽取器处理的最小单位——user 或 assistant 的一回合。每个对话源都通过 user→assistant
配对产出一条事实；Hermes 的内置 `MEMORY.md` / `USER.md` 由 `scan-memories` 单独只读扫描，
不进入 reconcile/extract 管道。
_Avoid_: 消息、message、轮次

### 知识维护

**dream（覆盖空白发现）**:
分析 reconcile 事实，发现知识库未覆盖的高频主题，返回候选清单。只读，不写知识库。
_Avoid_: 推荐、suggest、分析、report

**distill（聚类蒸馏）**:
把同 `category`+`tech` 下多张高频/ascended 卡片聚类为**候选工作流清单**（含源卡片 id）。纯只读、不落盘；skill 撰写由 agent 评估候选后自行完成。零候选即成功。
_Avoid_: 提炼、extract（易混）、总结、summarize、草稿生成

**curate（策展）**:
agent 依据 agenote-curator skill 主导执行的 KB 维护流程（诊断 → 状态重整 → 去重合并 → 矛盾调和 → reconcile/dream 综合 → 重整提交）；CLI 只提供只读候选发现与原子写命令，无一键编排（ADR-0004）。
_Avoid_: 清理、cleanup、整理、maintain、一键策展

**候选清单（candidates）**:
CLI 检测命令输出的只读待办集合（降级候选 / 归档候选 / 重复对 / dream·distill 候选），每项含判定依据（天数、相似度、评分）；agent 审查后用原子命令显式执行，CLI 不代为取舍。
_Avoid_: 建议、suggestion、自动处理

**noise fact（噪声事实）**:
源自 harness 注入的元消息（TodoWrite、system-reminder、checkpoint、`[CONTEXT]` 框架标记等），非用户真实经验；reconcile/dream 过滤之。
_Avoid_: 垃圾、junk、无关条目、spam

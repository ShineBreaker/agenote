# extract 抽取器收敛为 framework + adapter

**Status**: accepted

8 个跨 agent 对话抽取器（opencode/zcode/omp/crush/codex/claude/hermes + 已弃用的 pi）只有 4 种数据 schema，
其中 opencode↔zcode、omp↔pi 是 ~95% 互为副本的双胞胎；user→assistant 配对状态机重复 62 处，
ReconciledFact 构造模板在 7 个文件逐字复制。决定收敛为 1 个 deep extraction framework + 每源一个薄 adapter，
把 ~1600 行压到 ~600 行，新增源成本从"复制 230 行"降到"写 ~40 行 adapter"。

## 核心抽象选择

adapter **统一产 ReconciledFact**；framework 提供 `pair_turns()` helper——配对型源（6 个）调用它完成
user→assistant 配对 + 构造，事实型源（hermes，无 turn 概念）直接产出。配对逻辑集中到 framework 一处。

## Considered Options

- **adapter 产 turn，framework 统一配对**：被拒。hermes 是 facts 表 1:1 映射、无 turn 概念，
  要么伪造单 turn 要么开第二条路径，违背"统一 interface"初衷。
- **维持现状（每源独立文件 + 三套 dispatch）**：被拒。~550 行纯复制是维护债，
  新增源要在 `_resolve_extractors` / `KNOWN_SOURCES` / `_TRACE_DISPATCH` 改三处。
- **一次性迁移全 8 个**：被拒。项目当前零测试，一次性迁移回归面过大；改用增量迁移验证设计。

## Consequences

- 三套 dispatch（`_resolve_extractors` / `KNOWN_SOURCES` / `_TRACE_DISPATCH`）统一为单一 `extract.SOURCES` registry。
- `trace_session` 进 adapter interface（可选方法），删除 `_TRACE_DISPATCH`。
- `pi.py` 删除（与 omp 是 95% 副本的上游原版，未注册且 cli help 误列它为 bug）。
- `extract_hermes` 从 `reconcile.py` 迁到 `extract/hermes.py`；`ReconciledFact` 抽到 `extract/models.py`，
  解除 extract→reconcile 的反向依赖（extract 子包自此自洽）。
- 引入 pytest：framework 单测（pair_turns / 聚合 / 过滤 / 截断 / 错误收集）+ 每个 adapter 小 fixture 烟雾测试。
- 迁移顺序：framework 骨架 → opencode+zcode（验证设计）→ omp → codex/claude/crush/hermes。

## 实施记录

- **2026-08-14 阶段 1a/1b/1c 全部完成**：7 源全部 @register 进 `extract.base.SOURCES`；
  `pi.py` 已删除；`extract_hermes` 迁至 `extract/hermes.py`（`_extract_hermes_title`
  与 `extract_title` 的重复实现合并）；`ReconciledFact`/`RECONCILE_DEFAULT_WEIGHT`
  迁至 `extract/models.py`（reconcile re-export 兼容旧路径）；reconcile 的
  `KNOWN_SOURCES`（7 项 lambda `__import__` 分发）与 trace_fact 的局部
  `_TRACE_DISPATCH` 均已删除，改由 SOURCES 派生——新增源只写一个 adapter 文件。
- 实施中追加的框架语义：`pair_turns` 的 categorize 回调签名定为
  `(session, user_text, assistant_text)`（crush 的内容关键词分类需要文本）；
  空 text 回合不参与配对（对齐 7 源原版行为）。

## 关联

候选 2（cards 拆分）与候选 3（core 拆分 + orgserde）尚未立 ADR；其共识见架构评审报告。
orgserde 的"写"部分（`run_extract` 内联渲染迁移）依赖本 ADR 完成后再抽。

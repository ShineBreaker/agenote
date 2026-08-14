# cards 拆分为 cards / search / curator

**Status**: accepted

`cards.py` 1381 行混装三个不相干的职责：卡片 CRUD、全文检索、策展状态机（archive/restore/
dedup/review/curate）。策展侧还通过 `health → cards._jaccard_similarity` 形成一条本不必要的
跨模块 seam。决定按职责拆为三个顶层模块：`cards`（CRUD：add/get/list/update/touch/merge/
connect/inbox/stats/fields/tags）、`search`（单域+跨域检索，顺带吸收 core 的 5 个搜索辅助函数
`_query_terms`/`_range_score`/`_merge_ranges`/`_line_contains_any`/`_iter_search_targets`）、
`curator`（archive/restore/dedup/review/curate + `_jaccard_similarity`）。

## Why

- locality：检索算法（打分/片段/snippet）集中到 search 一处；策展状态机（done→stable→stale→
  archived + 权重重分配）集中到 curator 一处，bug 不再散落两个文件。
- 消除 health→cards 的跨职责依赖：`_jaccard_similarity` 归 curator，health 与 curator 同属
  「知识维护」语境，依赖方向自然。
- core 免费瘦身：5 个搜索辅助函数只有搜索路径用，迁出后 core 回归「常量 + KBContext + 索引」。

## Considered Options

- **不拆，cards.py 内部分节**：被拒。1381 行已超阈值，三职责间共享的只有 core helper，
  拆分零成本而可读性收益直接。
- **拆成子包 cards/**：被拒。模块间无共享状态，子包反而增加 import 路径深度。

## Consequences

- 依赖更新点 3 处：cli.py 拆 import、health.py 改 `from agenote.curator import`、
  shim.py 的 cmd_curate 改从 curator 导入。
- cmd_curate 内对 health 的 lazy import 保持不变（curator→health 运行时导入，无循环）。
- viz / memory / lint / dream / extract 不受影响。

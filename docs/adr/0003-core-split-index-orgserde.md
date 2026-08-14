# core 拆分为 core / index / orgserde

**Status**: accepted

`core.py` 混装四类职责：常量与 KBContext、JSON 索引管理（`_load/_save/_rebuild/_upsert/
_card_dict`）、Org 属性解析（`parse_org_prop/read_org_title/_parse_*_prop`）、卡片操作。
Org 解析逻辑此前散落三处（core 的属性读取、extract/base.py `run_extract` 的内联 Org
渲染、orgfmt 的格式美化）。决定拆为：`core`（常量 + KBContext + 工具 + ensure_dirs +
touch_card/_resolve_card/is_noise_fact）、`index`（JSON 索引读写）、`orgserde`（Org
序列化/反序列化：属性解析 + facts→Org 渲染，吸收 `run_extract` 的内联渲染）。

## Why

- Org 读写归一处：orgserde 拥有「属性解析 + 文档渲染」，orgfmt 保持独立为纯格式美化器
  （职责不重叠）。
- 索引逻辑的 locality：`_card_dict` 的字段抽取/`_load/_save` 的骨架回退集中到 index。
- 依赖方向单向：orgserde（零依赖）← index ← core ← 调用方；core 内部对 index 的两处
  调用（touch_card/ensure_dirs）走运行时 lazy import，避免顶层循环。

## Considered Options

- **core 顶层 re-export 新模块函数**：被拒。re-export 层是 shallow wrapper，import 路径
  不再反映真实归属；15 个调用方一次性更新成本可控。
- **orgserde 吞并 orgfmt**：被拒。orgfmt 是交互式格式美化器（依赖 jieba 分词），
  与序列化职责不同层。

## Consequences

- 调用方 import 批量更新：索引函数改 `from agenote.index import`，Org 解析函数改
  `from agenote.orgserde import`。
- `run_extract`（extract/base.py）的 Org 渲染段替换为 `orgserde.render_facts_org` 调用。
- KBContext 仍在 core；index 函数签名不变（ctx 可选参数）。

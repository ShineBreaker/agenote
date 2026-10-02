# 策展分工：CLI 只做原子工具，流程由 agent 依据 skill 主导

**Status**: accepted

策展（curate）此前由 CLI 的 `curate` 一键命令编排（健康检查 → 权重重配 → stale
自动降级 → 去重 → 自动归档 → 重建索引），其中状态转换与归档是 CLI 按天数阈值
自动写盘的语义决策。决定删除一键编排与全部自动决策入口，CLI 收敛为「只读候选
发现 + 原子写命令」两层；流程编排与去留取舍由 agent 依据 agenote-skills 的
SKILL.md 逐步执行。2026-08-29 三仓落地：agenote `fc24d6e` / skills `19e06e7` /
pi `f8ccec3`。

## Why

- CLI 做不了语义判断：「LAST_USED 超 30 天」只说明没人用，卡片是否真过时需要
  读正文理解；按天数自动降级/归档会误伤刚写的待沉淀卡片。
- skill 是跨 agent 行为规范的唯一载体：策展策略（阈值取舍、步骤顺序、矛盾
  调和规则）改动走文档，不用动代码、不用发版。
- token 经济性不构成反对理由：CLI 输出 JSON 候选清单（`list --unused-days` /
  `archive --stale`）并支持批量原子执行（`archive <id...> --reason`），agent 的
  语义把关发生在「审查清单 → 显式执行」，无需逐张读卡。

## Considered Options

- **保留 `curate` 为「机械阶段一键」（无 LLM 批量步骤交给 CLI）**：被拒。该方案
  落地过（curate = 机械阶段 2 步 + agent 综合阶段），但「机械」的边界会腐化——
  权重公式、天数阈值、噪声过滤都是伪装成机械的语义决策，最终又长回一键编排。
- **`curate` 改 `--dry-run` 报告 / `--apply` 执行**：被拒。`--apply` 仍是自动
  写盘，只是把决策从「默认发生」挪到「一个 flag」，与被拒方案同构。
- **权重重配独立为 `reweight` 命令**：被拒。WEIGHT 是 usage_count/last_used 的
  纯公式派生值（无语义取舍），与 index.json 同类；并入 reindex 全量重算，少一个
  命令，且根治旧实现「第 2 步改 index 权重、第 5 步全量重建又从文件属性读回
  旧值冲掉」的不自洽。

## Consequences

- WEIGHT 成为 index 层派生值：`index._card_dict` 按公式（基础权重 × 使用系数 ×
  新鲜度系数）计算；文件层 WEIGHT 属性废弃（add 不再写，遗留属性被忽略）。
- 命令面铁律：检测类（health / gaps / deduplicate / list --unused-days /
  archive --stale / dream / distill）恒只读；写类（update / archive / merge /
  touch / connect / memory --archive*）恒由 agent 显式指定目标，无「自动批量」参数。
- pi 的 `/agenote-curate` 与 `/agenote-summarize` 同模式（注入提示词，不
  execSync 策展）；agenote-cli shim 只剩 health。
- 后续给策展加步骤的判据：无语义取舍的批量重算 → CLI（参照 reindex）；有去留
  取舍的 → SKILL.md 步骤组合原子命令。禁止再引入「一键编排 / 自动批量写盘」
  入口（含 memory / review / deduplicate 等旁路）。

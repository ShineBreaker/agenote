# 轻量写入模式

## 适用场景

不是所有经验都值得写完整卡片。以下场景用轻量写入：

- 一次性环境失败（不值得记录）
- 简单的参数/配置修正
- 已有卡片的补充说明
- 一句话注意事项

## 方式

### 1. agenote inbox — 快速捕获

```bash
echo "注意: X 工具的 --flag 参数在 v2.0 后行为变更" | agenote inbox
```

适合：尚未整理的想法、待后续分类的注意点。

### 2. agenote update --append — 补充已有卡片

```bash
agenote update <id> --append-to "关键发现" --append-text "补充: 新发现的边界条件"
```

适合：已有卡片需要补充新信息。

### 3. agenote memory --add — 记录偏好/习惯

```bash
echo "内容" | agenote memory --add --type feedback --title "偏好描述"
```

适合：用户偏好、行为模式。

## 自动模式

在策展（agenote-curator skill 主导）中：

- `agenote health --quality --duplicates` 检查知识库状况与重复
- `agenote review <id>` 审查单卡质量（只读，修复用 update 显式执行）
- 状态转换由 agent 审查候选后显式执行（done → stable → stale → archived）

## 卡片生命周期

```
done ─── 策展验证 ──→ stable ─── >90天未验证 ──→ stale ─── >90天未验证 ──→ archived（候选清单）
  │                                                                              ↑
  └── >30天未使用 ──→ stale（sweep 降级路径）──────────────────────────────────┘
        └──────────── agenote restore <id> 可恢复
```

done → stale 按 `last_used`（`curation.stale_days`，默认 30 天）；stable → stale 与
stale → archived 候选均按 `LAST_VERIFIED`（`curation.archive_days`，默认 90 天）。
`agenote sweep` 列/执行 done+stable → stale 降级（默认 dry-run，`--apply` 落地）。

# 写入决策树

## 判断流程

```
检测到经验信号
    │
    ├─ 是技术经验吗？
    │   ├─ 排查 >2 步？ ──────── agenote add 完整卡片
    │   ├─ 跨工具集成？ ──────── agenote add 完整卡片
    │   ├─ 架构/设计决策？ ──── agenote add 完整卡片
    │   ├─ 一句话修正？ ──────── agenote inbox 或 agenote update --append
    │   └─ 否 ────────────────── 不写
    │
    ├─ 是偏好/习惯吗？ ────────── agenote memory --add --type feedback
    │
    ├─ 是用户画像吗？ ────────── agenote memory --add --type user
    │
    ├─ 是项目决策吗？ ────────── agenote memory --add --type project --project <slug>
    │
    ├─ 是环境约束吗？ ────────── agenote memory --add --type environment
    │
    └─ 是外部资源位置？ ──────── agenote memory --add --type reference
```

## 优先级

1. **纠正(mistake)** — 用户纠正必须记录
2. **调试(debug/config)** — 踩坑经验
3. **工作流(workflow)** — 流程优化
4. **功能(feature)** — 新功能实现经验

## 轻量 vs 完整卡片

| 标准     | 轻量写入                                    | 完整卡片      |
| -------- | ------------------------------------------- | ------------- |
| 排查步骤 | ≤2 步                                       | >2 步         |
| 复用价值 | 本项目                                      | 跨项目        |
| 内容量   | 1-3 行                                      | 需要章节结构  |
| 命令     | `agenote inbox` / `agenote update --append` | `agenote add` |

## 新增命令辅助写入

- `agenote touch <id>` — 标记卡片"刚用过"
- `agenote review <id>` — 快速检查卡片质量（只读）
- `agenote deduplicate` — 写入前检查重复
- `agenote health` — 查看知识库整体状况

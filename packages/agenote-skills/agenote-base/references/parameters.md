# 参数取值

`category` 和 `tech` 均**不设白名单**，允许自由输入——但写入前先用 `agenote fields --category` / `agenote fields --tech` 查看已有标签，优先复用，只有全新领域才创建新类别。原因：标签是检索的聚合维度，同义异写的标签（如 `guix` / `Guix System`）会把同一主题的卡片打散成多个桶，彼此检索不到。

## --category（类别，自由输入，优先复用已有标签）

请直接通过 `agenote fields --category` 来获取已有的内容

## --tech（技术栈域，自由输入，优先复用已有标签）

`agenote fields --tech` 查看当前取值。写入时**复用已有一级技术域**，不要新造同义/上下位标签：

- **一卡一主域**：只写最主要的技术域（如 `guix` / `emacs` / `rust`）；具体版本、子库、函数、症状写进正文，不进 TECH
- **分隔符只用逗号**（聚拢后通常只有一个值）。不要用 `;` 或空格——它们会破坏索引的标签展开
- **大小写统一小写**（写 `guix` 而非 `Guix`）

技术域集合**不设白名单、动态演进**，由 `agenote-curator` Step 3.5 在每轮策展按实际分布聚拢。当前快照（仅供参考，非白名单）：`guix` / `emacs` / `agent` / `linux` / `common-lisp` / `rust` / `virtualization` / `typescript` / `git` / `godot` / `python` / `network`。

## --type（类型）

| 值         | 说明        |
| ---------- | ----------- |
| `debug`    | 调试/排障   |
| `refactor` | 重构        |
| `research` | 调研/探索   |
| `workflow` | 工作流/流程 |
| `feature`  | 新功能开发  |
| `config`   | 配置调整    |

## --owner（执行者）

| 值       | 说明                |
| -------- | ------------------- |
| `ai`     | AI 独立完成（默认） |
| `human`  | 人工独立完成        |
| `collab` | 人机协作            |

## --entry / --entry-type（条目语义，可选）

| 值         | 默认映射                        | 说明                 |
| ---------- | ------------------------------- | -------------------- |
| `mistake`  | `type=debug`, `owner=collab`    | 用户纠错后的复盘卡片 |
| `note`     | `type=workflow`, `owner=collab` | 长期注意事项         |
| `ascended` | `type=debug`, `owner=collab`    | 飞升模式后的复盘卡片 |

显式传入 `--type` 或 `--owner` 时，以显式值为准。

## --status（状态，挂 `update` / `restore`）

| 值         | 说明                                                       |
| ---------- | ---------------------------------------------------------- |
| `done`     | 写作完成（新建默认；>30 天未使用经 sweep 降级为 stale）    |
| `stable`   | 经策展验证，长期有效（>90 天未验证经 sweep 降级为 stale）  |
| `stale`    | 已降级（stale 且 >90 天未验证进入 `archive --stale` 候选） |
| `archived` | 已归档                                                     |

`update --status stable` 时自动更新 LAST_VERIFIED 为当前时间；`restore --status` 默认恢复到 `stable`。

## PROPERTIES 新增字段

| 字段             | 说明                                    |
| ---------------- | --------------------------------------- |
| `LAST_USED`      | 最后一次通过 agenote get/touch 访问时间 |
| `LAST_VERIFIED`  | 最后策展验证时间                        |
| `MERGED_INTO`    | 合并目标卡片 ID（被合并的卡片）         |
| `MERGED_FROM`    | 合并来源卡片 ID 列表（主卡片）          |
| `ARCHIVED_AT`    | 归档时间                                |
| `ARCHIVE_REASON` | 归档原因                                |

## 新命令概览

| 命令                  | 用法                                        | 说明                 |
| --------------------- | ------------------------------------------- | -------------------- |
| `agenote touch`       | `agenote touch <id> [--used-only] [--session SID]` | 更新时间戳（同会话幂等） |
| `agenote sweep`       | `agenote sweep [--apply] [--json]`          | done/stable → stale 降级（默认只读；done 按未用天数，stable 按未验证天数） |
| `agenote merge`       | `agenote merge <primary> <sec>...`          | 合并卡片             |
| `agenote archive`     | `agenote archive <id...> [--reason]`        | 归档（批量）         |
| `agenote archive`     | `agenote archive --stale`                   | 列出归档候选（只读） |
| `agenote restore`     | `agenote restore <id> [--status stable]`    | 恢复                 |
| `agenote deduplicate` | `agenote deduplicate [--threshold 0.7]`     | 检测重复             |
| `agenote review`      | `agenote review <id>`                       | 审查卡片（只读）     |
| `agenote health`      | `agenote health [--quality] [--duplicates]` | 健康度报告           |
| `agenote list`        | `agenote list --unused-days N`              | 降级候选（只读）     |

# 记忆系统模型

agenote 的 memory 子系统记录跨会话的偏好与项目元数据，存储在 `~/Documents/Org/agenote/MEMORY.org` 和 `memories/projects/`。

## 六节 + 五类型（N1 类型化模型）

| 节          | 前缀 | 用途     | scope            | 典型内容                                   |
| ----------- | ---- | -------- | ---------------- | ------------------------------------------ |
| user        | U    | 用户癖好 | user（跨项目）   | 回复用中文、删除一律 trash-put             |
| feedback    | F    | 行为纠正 | user / project   | 被纠正后的做法（如读文件用 Read 而非 cat） |
| project     | P    | 项目取向 | project          | 该仓库提交前跑测试（混合节：项目索引行 + P 序号条目） |
| environment | E    | 环境局限 | machine / user   | Guix 禁持久安装、无 sudo                   |
| reference   | R    | 参考资料 | 任意             | 可跨项目复用的指针（构建产物路径等）       |
| deprecated  | —    | 归档     | —                | 终态；supersede 的旧条目落此处             |

条目可选属性：`:TYPE: :SCOPE: :PROJECT: :SENSITIVITY: :MACHINE: :ORIGIN_AGENT: :ORIGIN_ID: :ORIGIN_PATH: :VALIDATED_AT: :EXPIRES_AFTER: :USAGE_COUNT: :SUPERSEDES: :SUPERSEDED_BY: :ARCHIVED_AT: :NEEDS_REVIEW:`；无 `:TYPE:` 的存量条目按前缀/节推导。

关键语义：

- `:PROJECT:` 是**分区键**而非类型限定——任意类型（U/F/R 等）带此属性即只在对应项目上下文注入/投影；外部记忆 import 时自动落源侧 `projects/<slug>` 原值（zcode `-<16hex>` 尾缀、claude 消毒路径 slug 由匹配侧判定，不改写）。
- `:SENSITIVITY:` 非空即受限：条目留在 SSOT 可本地读取，但**不进** `context` 注入与 `memory --export` 投影。
- `:USAGE_COUNT:` 由 `--touch` 递增，与 `:UPDATED:` 内容时效分离；`--archive`/`--supersede` 落 `:ARCHIVED_AT:`/`SUPERSEDED_BY:` 墓碑后可追溯。

## 检索方式

```bash
agenote memory                          # 全部概览
agenote memory --list [--type U|F|P|E|R] [--scope S] [--json]  # 只读列出条目（含钩子与时效；敏感条目带 sensitive 标记）
agenote memory --type feedback          # 只看 feedback
agenote memory --project <名称|路径|.>   # 按项目检索（源 slug 也可命中）
agenote memory --get                    # 全文
agenote memory --get --type project     # 只看 project 节
agenote memory --stale                  # 陈旧记忆（超 30 天未更新）
```

## 写入

```bash
# feedback 记忆
echo "用户偏好简洁回复，不要长篇解释" | \
  agenote memory --add --type feedback --title "回复风格偏好" --stdin

# project 记忆（按项目拆分到独立文件）
echo "该项目用 Guix 构建，blue rebuild 部署" | \
  agenote memory --add --type project --title "构建方式" \
  --project Guix-configs --stdin

# 限定项目的偏好（任意类型可带 --project 分区）与不外发的敏感条目
agenote memory --add --type feedback --title "内部代号" --sensitivity private --stdin

# 写入侧 secret 门禁默认开启：命中高置信密钥前缀即拒写；确认非密钥加 --allow-secret
```

## feedback 条目格式

```org
** F001 回复风格偏好
   :PROPERTIES:
   :CREATED:  [2026-06-25]
   :UPDATED:  [2026-06-25]
   :END:
   用户偏好简洁回复，不要长篇解释
```

## 维护

```bash
agenote memory --touch F001              # 更新时间戳
agenote memory --archive F001            # 归档到 deprecated
agenote memory --stale                   # 列出陈旧记忆（只读；归档逐条 --archive/--archive-to-file）
```

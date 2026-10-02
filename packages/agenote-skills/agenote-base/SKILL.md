---
name: agenote-base
description: 跨 agent 共享知识库（agenote）：任务开始查经验、过程中复用、结束记录。触发：非平凡任务开工前 / 疑似踩过同坑 / 联网查到新方案 / 被用户纠正 / 长任务收尾——任一出现即调用，按内部规则 list→search→get、add→touch。KB 健康度维护与多 agent 对话 reconcile 转 agenote-curator；会话结束经验评估转 agenote-review。
---

# agenote — agent 专属记事本

agenote 是人类知识库（`~/Documents/Org/`）的**并行子集**，专为 AI agent 记录而设。数据隔离在 `~/Documents/Org/agenote/` 子目录，与人类卡片互不污染。

> **调用方式**：所有操作通过 CLI `agenote`（`~/.local/bin/agenote`，bash 直接调用），无 MCP 接口。以 `agenote` 开头的子命令（`add`、`search`、`list` 等）即为本 skill 的入口。`--domain human` 切到人类知识库根，不指定时 search 默认做跨域加权检索。

> **来源溯源**：agent 写入的卡片自动打 `:SOURCE_AGENT:` 标签（取自启动 env `AGENOTE_AGENT`，缺失回退 `pi`）。当前接入 pi/crush/opencode/hermes/zcode；新增 agent 在其启动环境设 `AGENOTE_AGENT=<name>` 即可纳入归因，`agenote health` 的 `by_source` 字段可看各 agent 写卡分布。外部 agent（codex/claude/omp 等）经 loopctl 调起时，通过其 adapter 的 `env` 块设置。

## 任务前预检

开始非平凡任务（调试/排障、配置修改、用过的技术栈、与之前类似的问题）前，先查已有经验——KB 的价值靠复用兑现，检索一次的成本远低于重新踩坑：

```
agenote list --category <相关类别> --all   # ①查经验：领域标题索引
agenote get <明显相关的卡片ID>
agenote search "<技术 工具 症状>"           # 标题不足以定位时正文检索
agenote memory --list --json               # ②查画像：用户偏好/项目约定/环境事实（--type U|F|P|E|R --scope 过滤）
agenote memory --project .                 # 当前项目记忆（含健康提示）
```

全新领域开发、简单编辑、有明确文档的标准操作可跳过。结果处理：高相关 → 作为上下文；低相关/空 → 静默继续；矛盾 → 以较新/经验证的为准。

**注入简报在场时降级**：注入器开启的宿主中，会话可能已带 `agenote context` 记忆简报（首行 marker `<!-- agenote-context v1 ... -->` 可识别）。marker 在场**且**简报覆盖当前任务域（U/E/P/F/R 条目命中本任务涉及的项目、环境或主题）时，预检从「必跑」降为「补查」——简报已覆盖的维度不再重查，未覆盖的照常查。注意简报只含记忆条目的一行式精选，不含经验卡片，卡片检索（list/search/get）按任务需要照常跑。简报末尾固定的「更多：`agenote memory --list --type X` / `agenote search <kw>`」指引照常有效，需要条目全文时按指引展开。marker 缺席或覆盖不足 → 照常完整预检。

## 何时记录（场景 → 写法）

| 场景                                    | 写法                           |
| --------------------------------------- | ------------------------------ |
| 联网/文档查到并实际用到的方案、API 用法 | `add --entry note`             |
| 调试踩坑、被用户纠正、误判需求、走弯路  | `add --entry mistake`          |
| 多轮试错后找到的正确做法                | `add --entry ascended`         |
| 用户对 agent 工作方式的偏好（跨会话）   | `memory --add --type feedback` |
| 项目技术栈、构建命令、已知坑点          | `memory --add --type project`  |

不记录：纯浏览未采用的资料、临时调试输出、可从代码直接推导的信息、一次性任务细节——噪音会稀释检索质量。

**写入门禁（T1）**：可从项目现状直接推导的内容、git 历史既成事实、临时状态（构建产物路径、当前分支/进程状态等）——**即使被显式要求也拒收**，只转述其中非显然、跨会话仍成立的部分。U/F 边界判据：被纠正过的行为记 F（feedback），用户直接陈述的偏好记 U（user_preference）；边界样本有纠正史按 F、无则按 U。

## CLI 速查

### 检索

```
agenote list --category <类别> --all     # 按领域列出标题+元数据（检索首选）
agenote get <ID>                         # 读卡片（用 ID，不是 title）
agenote search "<关键词>" [--json]       # 跨域加权检索（人类域权重更高）
agenote search "<关键词>" --all-terms    # 要求全部词项命中（AND 语义）
agenote search "<关键词>" --context N --limit N
agenote search --regex "<正则>"
```

### 写入

```
agenote add --title "标题" --category <类别> --tech <技术栈> \
  --type <类型> --entry <note|mistake|ascended> --summary "一句话总结" --stdin <<EOF
** 任务描述
...
** 执行过程
...
** 关键发现
...
EOF
```

**type 门禁**：`--type` 只用正式 type（`agenote fields --type` 查看；种子 6 类 debug/refactor/research/workflow/feature/config 免检，其余需非归档 ≥10 张才晋升正式）；非正式 type 会被 CLI 拒绝，确需延续/新建才加 `--force`（新 type 持续写到 10 张后自动转正免检）。

### 管理

```
agenote init [--no-git]                  # 仅首次：创建目录结构 + git 仓库（--no-git 跳过 git）
agenote touch <ID>                        # 留痕（USAGE_COUNT+1）
agenote touch <ID> --session <SID>          # 同卡同会话只计一次 USAGE（防重复刷分）
agenote sweep [--apply] [--json]            # done/stable → stale 降级候选（默认只读清单；done 按未用天数，stable 按未验证天数）
agenote update <ID> --status done|stable|stale
agenote update <ID> --append-to "关键发现" --append-text "新发现"
agenote connect <A> <B> --desc "描述"     # 双向链接
agenote merge <primary> <sec>             # 合并卡片（secondary 自动归档）
agenote archive <ID> / agenote restore <ID>
agenote archive --list                    # 列出已归档（restore 前先在此找 ID）
agenote inbox "待捕获的想法"
agenote stats / agenote health / agenote fields
agenote lint [--fix] / agenote lint --json   # 后者输出分类结构化报告（可差分）
agenote reindex / agenote doctor
agenote commit -m "一句话总结"            # 提交 KB git 变更
```

完整参数取值见 [references/parameters.md](references/parameters.md)。

## 用 ID 而非 title 定位卡片

`get`/`touch`/`archive`/`update` 用 **ID 或文件名片段**匹配，不匹配 title。先用 `list` 或 `search` 找到卡片 ID（形如 `20260625-014305`）再操作。

## 留痕机制

查询资料后，对**实际用到**的部分留痕：已有卡片 `agenote touch <ID>` 递增 USAGE_COUNT；联网新知识 `add --entry note` 写卡留档，`--summary` 写"来源 + 核心结论一句话"，正文首行留 `来源: <URL/API>` 保留可回溯性。频繁使用的卡片 WEIGHT 自动提升（usage_count 参与权重公式，reindex 时重算），检索排名更靠前——留痕是提升下次命中率的手段，不是仪式。

## 记忆系统

四种类型：feedback（行为偏好）/ project（按项目拆分，存 `memories/projects/<项目>.org`）/ reference（可跨项目复用的参考）/ deprecated（陈旧归档）。

```
echo "正文" | agenote memory --add --type feedback --title "标题" --stdin
echo "正文" | agenote memory --add --type project --project <项目> --title "标题" --stdin
# --project 是分区键（任意类型可用）；--sensitivity private 标"不注入不投影"；
# 写入默认过 secret 门禁，命中密钥前缀即拒写（确非密钥加 --allow-secret）
agenote memory                            # 概览
agenote memory --project .                # 当前项目记忆（含健康提示）
agenote memory --get                      # 全文
agenote memory --stale                    # 陈旧清单（只读）
agenote memory --touch F001 / agenote memory --archive F001
```

F/R 序号由 CLI 自动分配（feedback 记 F 序号入 MEMORY.org `* feedback` 节，reference 记 R 序号，project 追加到 `memories/projects/<name>.org`），无需手工管理。模型细节见 [references/memory-model.md](references/memory-model.md)。

### 记忆 SSOT 速查（N1–N5 全链路已落地）

```
agenote memory --list [--type U|F|P|E|R] [--scope S] [--json]  # 只读列出事实条目
agenote memory --import [--source zcode|claude|codex|pi|reasonix|hermes|all] [--dry-run]  # 摄取导入（写命令；--dry-run 只预览，仍持锁）
agenote memory --export [--type T] [--scope S] [--project P]  # 投影到宿主聚合文件（幂等+漂移检测）
agenote memory --conflicts [--json]          # 冲突队列（只读）
agenote memory --supersede <新ID> <旧ID>      # 裁决：新条记 SUPERSEDES，旧条入 deprecated
agenote memory --revalidate                  # 待重验清单（只读）
agenote memory --validate <ID>               # 刷新单条 VALIDATED_AT
agenote dream --window-days 90 --limit 5     # 候选新卡片（只读；游标自动推进，唯一落盘是 dream-cursor.json）
```

日常单条增改仍走 `memory --add` / `--touch` / `--archive`；import/export/冲突裁决/重验属策展编排，流程见 `agenote-curator` Step 8.5。

## 注入器与开关

注入器开启的宿主（zcode/claude/codex/pi/opencode/hermes）会在会话中由插件/hook 自动调 `agenote context` 注入记忆简报——agenote 本身仍是纯粹 CLI，不做常驻进程。与用户既有的 context-select 决策核（`~/.config/agents/context-select.sh`）两层并列：决策核注入**原则层**（怎么做事的稳定规范），agenote 注入**事实层**（动态更新、会裁决的记忆/画像）——不融合、不互斥、不重复。

注入行为不对或想启停时，**优先改 agenote 配置，再考虑物理移除注入器**（配置可逆、一处生效）：

1. `~/.config/agenote/config.toml` 的 `[injection]` 节：`enabled = false` 一键全关；预算/召回参数（`default_budget` / `recall_topk` / `recall_min_score` 等）也在此节
2. `[injection.hosts]` 平铺键 `<host>_enabled = false`（zcode/claude/codex/pi/opencode/hermes）按宿主单独关
3. 配置解决不了才动注入器本体（卸宿主 hook/插件；恢复需重装）

生效值核对用 `agenote config show`（优先级 env > file > default；env 前缀 `AGENOTE_INJECTION_*`）。

## 可视化

`agenote viz -o out.html` 把 KB 渲染成可搜索的单文件 HTML（自带主题）；`--serve --port 8765` 起本地服务。用户说"用 md2html"或"把 KB 渲染出来看"时，先想 `agenote viz`。内容是单份 markdown 报告（非 KB 卡片）时退回 `pandoc -s <md> -o <html>`。

## 相关 skill

- KB 例行策展（健康度维护、去重归档、reconcile、dream 综合）→ `agenote-curator`
- 会话结束经验评估与留痕决策 → `agenote-review`

## 详细参考

- [卡片格式与字段](references/card-format.md)
- [记忆系统模型](references/memory-model.md)
- [ENTRY_TYPE 语义映射](references/entry-types.md)
- [参数取值表](references/parameters.md) — `--category`/`--tech`/`--type`/`--owner`/`--entry`/`--status` 等
- [Org 格式规范](references/markdown-to-org.md) — 代码块/强调/标题 Org vs Markdown 对照
- [体验卡片模板](references/experience-template.org) — 完整 Org 模板（Emacs org-capture 用）

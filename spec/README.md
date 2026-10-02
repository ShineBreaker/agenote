# spec/ — 跨组件语义的单一真相源

这里放**跨宿主复制**的语义：完成信号清单、时序阈值、每宿主注入预算。
决策依据见 [`docs/adr/0005`](../docs/adr/0005-monorepo-unify-seven-components.md)。

## 为什么不放所有配置

| 内容 | 真相源 | 理由 |
|---|---|---|
| 完成信号、注入预算、时序阈值 | `spec/injection.toml` | 跨 4–6 处复制，必须机器保障 |
| CLI 配置项（`[injection]` 等 84 键） | `packages/agenote/src/agenote/config.py` 的 `SCHEMA` | 只有 CLI 读，注入器不读数值键 |
| 各插件的 Config schema 默认值 | 各包 `Config` 声明 | 宿主加载期校验用，形状随宿主而异 |
| 卡片格式 / 写入流程 / 策展策略 | `packages/agenote-skills/*/SKILL.md` | 文档即载体，改策略不该发版 |

`spec` 里有一处**刻意的镜像值**：`[append].min_query = 6` 对应 CLI 的
`recall_min_query`。两边仅通过 env（`AGENOTE_INJECTION_MIN_QUERY`）联动，
改 `config.toml` 不同步注入器；CLI 侧始终是召回与否的最终裁决，注入器这道门槛
只为省一次 spawn。

## 工作流

```bash
# 1. 改 spec/injection.toml
# 2. 把常量块写进各包（生成物提交进仓）
python3 tools/codegen/generate.py
# 3. 校验不能生成的产物（JSON 正则 / Markdown 散文）+ 副本清点
python3 tools/codegen/check.py
# 4. 语法与漂移
python3 tools/codegen/generate.py --check && git diff --exit-code
```

## 三条约束

1. **生成物提交进仓。** 宿主插件是独立安装的（`~/.zcode/plugins/`、omp 扩展
   目录、hermes 插件目录），运行时不能依赖 monorepo 存在。所以生成物进版本库，
   漂移由 CI 拦截，而不是靠开发纪律。
2. **每个生成块有 BEGIN/END 标记。** 工具只改写两标记之间的内容，标记外的代码
   与注释一律不碰——所以生成块可以嵌在文件中间，不要求整文件模板化。
3. **不允许出现未登记的副本。** `check.py` 会扫出任何自行定义
   `COMPLETION_SIGNALS` 却没有生成块标记的文件并报错。迁移前信号散落 6 处
   （pi / dsh / zcode / hermes 四份代码 + `hooks.json` 正则 + `triggers.md`
   散文）靠纪律同步——`agenote-zcode/hooks/hooks.json` 的预筛正则就曾漏掉
   `完成`，该信号在 zcode 侧实际失效，正是这条纪律失效的实证。

## 何时该新建 spec 文件

先问一句：这个语义是否**跨两个以上包**复制？

- 是 → 进 `spec/`，配生成块或契约校验。
- 否 → 留在代码里，照常写注释。spec 不是垃圾桶，把只在一处用的常量塞进来
  反而增加维护面。

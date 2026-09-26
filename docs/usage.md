# agenote-skills 使用文档

本文档覆盖 skill 规范、职责对照、部署与依赖。产品定位与设计说明见
[README](README.zh.md)。

## 目录

- [Skill 规范](#skill-规范)
- [职责对照](#职责对照)
- [部署](#部署)
- [依赖](#依赖)
- [改源生效路径](#改源生效路径)

## Skill 规范

每个 skill 是一个自包含目录，核心是 `SKILL.md`，可选 `references/` 放参考文档：

```
<skill-name>/
├── SKILL.md              # 必需：元数据 + 完整指令
└── references/           # 可选：参考文档子目录
    └── *.md / *.org
```

`SKILL.md` 的 front matter 只有两个字段：

```yaml
---
name: <skill-name>
description: <功能描述，含触发信号，供 agent 框架匹配调度>
---
```

## 职责对照

| Skill | 触发信号 | 职责 |
| --- | --- | --- |
| `agenote-base` | 非平凡任务开工前 / 疑似踩过同坑 / 联网查到新方案 / 被用户纠正 / 长任务收尾 | 日常读写：list→search→get 查经验，add→touch 记录，commit 落盘 |
| `agenote-curator` | 每周或长会话后 / 卡片超过 50 张 / 检索质量下降 / 发现重复或矛盾 | 健康度维护：诊断、去重、归档、重算检索权重、reconcile 多源 memory |
| `agenote-review` | 完成信号检测 / 用户触发总结 / 长任务收尾评估 / 用户纠正 / 排查超过 2 步 | 会话后经验采集：信号识别、ENTRY_TYPE 判定、留痕决策树 |

skill 之间互相引用对方名字，框架据此切换加载哪份规范。agent 名字通过启动环境变量
`AGENOTE_AGENT` 归因，写入的卡片自动打上 `:SOURCE_AGENT:` 标签。

## 部署

### 作为 Guix-configs 子模块

本仓库在 `Guix-configs` 中登记为 `dotfiles/mutable/agenote/.config/agents/skills`
子模块。检出后由配置仓的 stow 流程部署软链：

```bash
git submodule update --init dotfiles/mutable/agenote/.config/agents/skills
blue stow agenote           # 部署软链
blue stow --restow agenote  # 重建
```

部署后 skill 出现在 `~/.config/agents/skills/`。agent 框架从 `~/.agents/skills/`
扫描加载，该路径是指向前者的软链。

### 独立部署

```bash
git clone https://github.com/ShineBreaker/agenote-skills.git ~/agenote-skills
stow --dir=~/agenote-skills --target=$HOME
```

## 依赖

**[agenote](https://github.com/ShineBreaker/agenote) CLI**。所有卡片与记忆操作都通过
PATH 中的 `agenote` 命令执行，规范本身不实现存储。使用前先装好 CLI。

## 改源生效路径

skills 是纯 Markdown，agent 框架每个会话扫描目录加载，改完源文件即生效，没有
编译和部署步骤。

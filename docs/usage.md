# agenote-el 使用文档

本文档覆盖 agenote-el 的安装、命令参考、browse mode 与配置。
产品定位与功能概览见 [README](README.zh.md)。

## 目录

- [安装](#安装)
- [命令参考](#命令参考)
- [知识库总览（browse mode）](#知识库总览browse-mode)
- [配置项](#配置项)
- [包结构](#包结构)
- [Dashboard 集成](#dashboard-集成)

## 安装

### 依赖

| 依赖                                                         | 说明                                                                               |
| ------------------------------------------------------------ | ---------------------------------------------------------------------------------- |
| **[agenote](https://github.com/ShineBreaker/agenote) CLI**   | 必须先装：`uv tool install git+https://github.com/ShineBreaker/agenote.git`        |
| **Emacs 29.1 及以上**                                        | `Package-Requires: ((emacs "29.1"))`，见 `agenote.el` 文件头                       |

无第三方 elisp 依赖，只用 Emacs 内置的 `cl-lib`、`json`、`org` 和 `tabulated-list`。

### 方式一：use-package + load-path（推荐）

```elisp
(use-package agenote
  :load-path "/path/to/agenote-el"            ; 或 stow 部署后的路径
  :custom
  (agenote-org-directory "~/Documents/Org"))   ; 知识库根（KB_ROOT）
```

### 方式二：直接加 load-path

```elisp
(add-to-list 'load-path "/path/to/agenote-el")
(require 'agenote-keybinds)                    ; 加载全部命令 + command-map
```

### 绑定快捷键

本包不绑定任何全局前缀。`agenote-command-map` 是裸 keymap，由宿主配置挂到喜欢的前缀上，键位这件事由宿主配置说了算：

```elisp
;; 挂到 C-c o k 前缀，which-key 会自动显示子命令
(keymap-global-set "C-c o k" agenote-command-map)

;; 或用 use-package
(use-package agenote-keybinds
  :after agenote
  :bind-keymap ("C-c o k" . agenote-command-map))
```

## 命令参考

加载 `agenote-keybinds` 后，`agenote-command-map` 含以下绑定：

| 键    | 命令                                      | 功能                  |
| ----- | ----------------------------------------- | --------------------- |
| `c`   | `agenote-knowledge-capture`               | 捕获经验卡片          |
| `s`   | `agenote-knowledge-search`                | 搜索经验              |
| `t`   | `agenote-knowledge-search-by-tag`         | 按标签搜索            |
| `I`   | `agenote-knowledge-open-inbox`            | 打开 Inbox            |
| `S`   | `agenote-knowledge-stats`                 | 知识库统计            |
| `v`   | `agenote-knowledge-browse-human`          | 总览人类域全部        |
| `b`   | `agenote-knowledge-browse-agenote`        | 总览 agenote 域全部   |
| `a`   | `agenote-knowledge-archive-inbox-entry`   | 归档 Inbox 条目       |
| `d`   | `agenote-knowledge-deduplicate`           | 检测重复卡            |
| `e`   | `agenote-knowledge-merge`                 | 合并卡片              |
| `l`   | `agenote-knowledge-lint`                  | 校验知识库            |
| `m`   | `agenote-knowledge-memory`                | 记忆系统              |
| `n`   | `agenote-knowledge-connect`               | 链接卡片              |
| `o`   | `agenote-knowledge-commit`                | 提交知识库            |
| `r`   | `agenote-knowledge-review`                | 审查卡片              |
| `u`   | `agenote-knowledge-touch`                 | 更新卡片时间          |
| `V`   | `agenote-knowledge-viz-open-browser`      | 浏览器可视化          |

另含 `agenote-health`（健康度面板）与 dashboard 数据源函数（见下）。

## 知识库总览（browse mode）

`M-x agenote-knowledge-browse` 打开双域分组总览：human 与 agenote 各显示前 20 张卡片。前 20 这个数字定义在
`agenote-knowledge-browse--default-limits`，在分组头上按 `RET` 可以在前 20 张和全部之间切换。

列有状态、作者、上次使用、次数、类别、标题。作者列优先取 `source_agent`，没有则取 `owner`。
mode-line 显示各域总数和当前过滤词。

| 键      | 动作                                                          |
| ------- | ------------------------------------------------------------- |
| `RET`   | 条目行打开卡片文件，分组头行在该域前 20 条和全部之间切换      |
| `/`     | 子串过滤，匹配标题、类别、状态、作者，如 `stale`              |
| `B`     | 回到双域总览，重置展开状态并清除过滤                          |
| `g`     | 重新拉取两域数据                                              |
| `o`     | 用浏览器打开光标所在域的网页可视化，即 `agenote viz --open`   |

数据来自 `agenote --domain <domain> list --all --json`，每次刷新都全量拉取两个域，分组截取在 Emacs 侧完成，所以过滤对全量数据准确。

## 配置项

| defcustom                         | 默认值                          | 说明                                        |
| --------------------------------- | ------------------------------- | ------------------------------------------- |
| `agenote-org-directory`           | `~/Documents/Org`               | 知识库根（对应 agenote CLI 的 `KB_ROOT`）   |
| `agenote-experiences-directory`   | `<org-directory>/experiences`   | 经验卡片目录                                |
| `agenote-inbox-file`              | `<org-directory>/inbox.org`     | 收件箱文件                                  |
| `agenote-subdomain-directory`     | `<org-directory>/agenote`       | agent 写入子域                              |

## 包结构

| 文件                     | 职责                                                                                         |
| ------------------------ | -------------------------------------------------------------------------------------------- |
| `agenote.el`             | 适配层：`agenote-call`、`agenote-call-async`、`agenote-call-string` 加路径 defcustom         |
| `agenote-knowledge.el`   | 知识库交互命令（增删改查、检索、记忆、策展）加 `agenote-knowledge-browse-mode`               |
| `agenote-health.el`      | 健康度面板（`agenote-health-mode`，从 special-mode 派生）                                    |
| `agenote-dashboard.el`   | 纯数据函数，不缓存、不起进程、不注册 UI，供宿主 dashboard 调用                               |
| `agenote-keybinds.el`    | `agenote-command-map`，17 个命令的裸 keymap，不含全局前缀绑定                                |

### 设计要点

- 适配层是唯一入口。所有 CLI 调用走 `agenote-call` 和 `agenote-call-async`，不直接拼 `executable-find` 和 `call-process`。
- 路径每次解析。`executable-find "agenote"` 每次调用都重新解析 PATH，不缓存绝对路径，所以长驻 daemon 在切换 Guix profile 后能直接用上新版 agenote。
- 域隔离。每次调用显式传 `--domain human` 或 `--domain agenote`，不依赖 CLI 默认值。
- dashboard 不持有状态。`agenote-dashboard.el` 只提供纯函数（如 `agenote-recent-knowledge-entries`），缓存、进程和 UI 注册都归宿主 dashboard 框架，因此能被任何 dashboard 实现复用。

## Dashboard 集成

宿主 dashboard 通过纯函数获取最近条目数据（自行管理缓存与异步刷新）：

```elisp
;; 返回最近 N 条知识条目，每条是 (ID CATEGORY TITLE) 三元组，供 dashboard 渲染
;; 默认 agenote 域（agent 写入卡片所在地；human 域多为手写、常为空）
(agenote-recent-knowledge-entries 5)
;; 可显式指定域
(agenote-recent-knowledge-entries 5 'human)
```

宿主负责：缓存、异步进程刷新、widget 注册。本包不介入这些。

# agenote-el — agenote Emacs 集成

> [agenote](https://github.com/ShineBreaker/agenote) 跨 Agent 经验平台的 Emacs 集成包。
> 在 Emacs 内调用 agenote CLI 进行知识卡片 CRUD、记忆管理、策展与健康度查看。

本包把 agenote CLI 的能力以交互命令、浏览 mode、健康度面板和 dashboard 数据源的形式
接入 Emacs。所有文件系统操作都委托给 agenote CLI，Emacs 只负责交互调用与 buffer 刷新，
避免 Emacs 与 CLI 之间的索引/扫描逻辑漂移。

## 依赖

| 依赖                                                       | 说明                                                         |
| ---------------------------------------------------------- | ------------------------------------------------------------ |
| **[agenote](https://github.com/ShineBreaker/agenote) CLI** | 必须先安装（`uv tool install`，产出 `~/.local/bin/agenote`） |
| **Emacs ≥ 29.1**                                           | `Package-Requires: ((emacs "29.1"))`                         |

无第三方 elisp 依赖，仅用 `cl-lib` / `json` / `org`（内置）。

## 安装

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

本包**不绑定任何全局前缀**——`agenote-command-map` 是裸 keymap，由宿主配置挂到喜欢的
前缀上（保留宿主对键绑定的单一真相源）：

```elisp
;; 挂到 C-c o k 前缀（which-key 会自动显示子命令）
(keymap-global-set "C-c o k" agenote-command-map)

;; 或用 use-package
(use-package agenote-keybinds
  :after agenote
  :bind-keymap ("C-c o k" . agenote-command-map))
```

## 命令

加载 `agenote-keybinds` 后，`agenote-command-map` 含以下绑定：

| 键  | 命令                                    | 功能            |
| --- | --------------------------------------- | --------------- |
| `c` | `agenote-knowledge-capture`             | 捕获经验卡片    |
| `s` | `agenote-knowledge-search`              | 搜索经验        |
| `t` | `agenote-knowledge-search-by-tag`       | 按标签搜索      |
| `I` | `agenote-knowledge-open-inbox`          | 打开 Inbox      |
| `S` | `agenote-knowledge-stats`               | 知识库统计      |
| `v` | `agenote-knowledge-browse-human`        | 浏览人类知识库  |
| `b` | `agenote-knowledge-browse-agenote`      | 浏览 agenote 域 |
| `a` | `agenote-knowledge-archive-inbox-entry` | 归档 Inbox 条目 |
| `d` | `agenote-knowledge-deduplicate`         | 检测重复卡      |
| `e` | `agenote-knowledge-merge`               | 合并卡片        |
| `l` | `agenote-knowledge-lint`                | 校验知识库      |
| `m` | `agenote-knowledge-memory`              | 记忆系统        |
| `n` | `agenote-knowledge-connect`             | 链接卡片        |
| `o` | `agenote-knowledge-commit`              | 提交知识库      |
| `r` | `agenote-knowledge-review`              | 审查卡片        |
| `u` | `agenote-knowledge-touch`               | 更新卡片时间    |
| `V` | `agenote-knowledge-viz-open-browser`    | 浏览器可视化    |

另含 `agenote-health`（健康度面板）与 dashboard 数据源函数（见下）。

## 配置项

| defcustom                       | 默认值                        | 说明                                      |
| ------------------------------- | ----------------------------- | ----------------------------------------- |
| `agenote-org-directory`         | `~/Documents/Org`             | 知识库根（对应 agenote CLI 的 `KB_ROOT`） |
| `agenote-experiences-directory` | `<org-directory>/experiences` | 经验卡片目录                              |
| `agenote-inbox-file`            | `<org-directory>/inbox.org`   | 收件箱文件                                |
| `agenote-subdomain-directory`   | `<org-directory>/agenote`     | agent 写入子域                            |

## 包结构

| 文件                   | 职责                                                                                                            |
| ---------------------- | --------------------------------------------------------------------------------------------------------------- |
| `agenote.el`           | **适配层**：`agenote-call` / `-call-async` / `-call-string`（call-process / make-process 封装）+ 路径 defcustom |
| `agenote-knowledge.el` | 知识库交互命令（CRUD/检索/记忆/策展）+ `agenote-knowledge-browse-mode`                                          |
| `agenote-health.el`    | 健康度面板（`agenote-health-mode`，special-mode 派生）                                                          |
| `agenote-dashboard.el` | dashboard 纯数据函数（无缓存/无进程/无 UI，供宿主 dashboard 调用）                                              |
| `agenote-keybinds.el`  | `agenote-command-map`（17 命令的裸 keymap，不含全局前缀绑定）                                                   |

### 设计要点

- **适配层是唯一入口**：所有 CLI 调用走 `agenote-call` / `agenote-call-async`，不直接
  `executable-find` + `call-process`。
- **路径每次解析**：`executable-find "agenote"` 每次调用都重新解析 PATH，**不缓存**绝对
  路径——长生命周期 daemon 在 Guix profile 切换后能自动用到新版 agenote。
- **域隔离**：每次调用显式传 `--domain human|agenote`，不依赖 CLI 默认值。
- **dashboard 零状态**：`agenote-dashboard.el` 只提供纯函数（如
  `agenote-recent-knowledge-entries`），缓存/进程/UI 注册归宿主 dashboard 框架——这让它
  可被任何 dashboard 实现复用。

## Dashboard 集成

宿主 dashboard 通过纯函数获取最近条目数据（自行管理缓存与异步刷新）：

```elisp
;; 返回最近 N 条知识条目（alist 列表，供 dashboard 渲染）
(agenote-recent-knowledge-entries 5)
```

宿主负责：缓存、异步进程刷新、widget 注册。本包不介入这些。

## 许可证

MIT，见 [LICENSE](LICENSE)。

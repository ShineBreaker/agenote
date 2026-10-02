# agenote-el：agenote 的 Emacs 前端

agenote-el 把 [agenote](https://github.com/ShineBreaker/agenote) CLI 的知识库操作接进 Emacs，
让你在 buffer 里捕获、检索、审阅和整理经验卡片，人和 AI 读的是同一批 org 文件。

## 它解决什么问题

agenote 把经验沉淀成知识库：每张卡片是一个 org 文件，人手写或用 org-capture 录入，
AI 也可以往同一棵目录里写。麻烦在于这些操作原本只在终端里发生，用 Emacs 的人得来回切窗口。

agenote-el 把终端命令变成 Emacs 交互命令。卡片就是普通的 org 文件，捕获走 org-capture，
搜索走 consult-ripgrep 或 rgrep，打开卡片就是 `find-file`。没有第二套数据格式需要同步。

## 核心能力

- **知识库总览**：`M-x agenote-knowledge-browse` 按 human 和 agenote 两个域分组列出卡片，
  每行给出状态、作者、上次使用、次数、类别和标题。按 `/` 按标题、类别、状态、作者过滤，
  按 `RET` 把某一域从前 20 张展开成全部条目。
- **知识库健康度**：`M-x agenote-health` 把 CLI 的健康度报告渲染成面板，按 `g` 重新拉取。
- **卡片生命周期**：捕获、归档、提交、合并、查重、连接两张卡片、更新时间戳，各有对应的交互命令。
  耗时的操作走异步进程，结果落在独立 buffer 里，不卡住编辑。
- **网页可视化**：把某个域的卡片关系图交给浏览器打开。

实现上有一条硬规矩：所有 CLI 调用都经过 `agenote-call` 和 `agenote-call-async`，
Emacs 侧不复制索引或扫描逻辑，排序和过滤都交给 CLI 决定。每次调用都重新解析
`executable-find "agenote"`，不缓存绝对路径，所以长驻 daemon 在切换 Guix profile 后能直接用上新版 CLI。

`agenote-dashboard.el` 另有一组纯函数，宿主 dashboard 调用它们取最近条目，自行管理缓存和刷新。

## 不适合谁

- 不用 Emacs 的。这个包的全部价值在 Emacs 里，终端用户直接用 CLI。
- 知识库不由 agenote CLI 管理的。Elisp 端不会自己去扫 org 目录。
- 想要自带数据库和全文索引的知识库工具的。agenote 的存储就是 org 文件加一份 CLI 索引。

## 快速开始

```elisp
(use-package agenote
  :load-path "/path/to/agenote-el"
  :custom
  (agenote-org-directory "~/Documents/Org"))
```

需要 Emacs 29.1 及以上，无第三方 elisp 依赖。安装步骤、键位表、browse mode 全部按键说明和配置项见
[使用文档](docs/usage.md)。

## 深入阅读

- [使用文档](docs/usage.md)
- [English README](README.md)
- [agenote 主仓库](https://github.com/ShineBreaker/agenote)

## 许可证

MIT，见 [LICENSE](LICENSE)。

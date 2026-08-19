# 贡献指南

感谢关注 agenote！本文说明开发环境搭建、commit 规范与提交流程。

## 开发环境

```bash
git clone https://github.com/ShineBreaker/agenote.git
cd agenote
uv sync --extra test        # 安装依赖 + 测试工具
uv run pytest -q            # 运行测试（提交前必须全绿）
uv tool install --editable .  # 本地体验 CLI（改源即生效）
```

## Commit 规范（Conventional Commits）

本仓库采用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/)，
格式为：

```
<type>(<scope>): <简短描述>

[可选 body：解释为什么改]

[可选 footer：BREAKING CHANGE: <说明> 或 Closes #<issue>]
```

### type 速查

| type | 用途 |
| --- | --- |
| `feat` | 新增功能 |
| `fix` | 修复 bug |
| `refactor` | 重构（不改行为） |
| `perf` | 性能优化 |
| `docs` | 文档变更 |
| `test` | 测试相关 |
| `chore` | 杂务/清理 |
| `build` | 构建系统 / 依赖 |
| `ci` | CI/CD |

### 规则

- 描述用动词开头、祈使语气；首字母小写、末尾不加句号。
- scope 填模块名：单文件改动用文件名（如 `fix(core): …`），
  跨文件用组件名（如 `refactor(extract): …`）。
- 不兼容变更在 type 后加 `!`，如 `feat!: drop chemacs2 support`，
  并在 footer 写 `BREAKING CHANGE: <说明>`。

> 注意：仓库 2026-08 之前的历史提交使用 `FEATURE:` / `REFACTOR:` 等
> 大写前缀，现已废弃，新提交一律用小写标准格式。

## 提交 PR

1. fork 仓库并从 `main` 切出特性分支；
2. 改动附带测试（新功能必须有测试，修复附回归测试）；
3. 确保 `uv run pytest -q` 全绿、CI 通过；
4. PR 标题遵循 Conventional Commits（与 commit 规范一致）。

## 语言约定

本项目文档与代码注释使用简体中文；标识符（函数/变量/模块名）使用英文。
架构决策记录见 [docs/adr/](docs/adr/)。

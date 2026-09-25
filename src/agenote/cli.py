#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
#
# agenote — 跨 Agent 经验平台 CLI（agenote 体系的主入口）
# 人机协作经验卡片的增删查改，供人类在终端与 cron 直接调用。
# agent 侧由 agenote-cli shim + omp-hooks 扩展桥接（TS 插件 execSync 调用）。
#
# 用法: agenote <子命令> [参数]
#
# 详细子命令列表请运行 agenote help。

import sys
import importlib.metadata

# 从已安装包的 metadata 读取版本（pyproject.toml [project] version 是唯一真相源）。
# 直接从源码树运行（未 pip/uv install）时 metadata 不存在，降级为 "unknown"——
# 避免 --version 因 PackageNotFoundError 崩溃。
try:
    _VERSION = importlib.metadata.version("agenote")
except importlib.metadata.PackageNotFoundError:
    _VERSION = "unknown"

from agenote import config
from agenote.core import (
    KB_ROOT,
    KB_EXPERIENCES,
    KB_MEMORIES,
    KB_MEMORY,
    KB_INDEX,
    KB_INBOX,
    SEED_TYPES,
    VALID_OWNERS,
    VALID_ENTRY_TYPES,
    VALID_STATUSES,
    STALE_DAYS,
    DEDUP_THRESHOLD,
    die,
    now,
    today,
    ensure_dirs,
    default_context,
    agenote_context,
    safe_error_message,
)
from agenote.index import (
    _load_index,
    _save_index,
    _rebuild_index,
)
from agenote.cards import (
    cmd_add,
    cmd_get,
    cmd_list,
    cmd_fields,
    cmd_tags,
    cmd_inbox,
    cmd_stats,
    cmd_connect,
    cmd_update,
    cmd_touch,
    cmd_sweep,
    cmd_merge,
)
from agenote.search import cmd_search
from agenote.safeio import atomic_write, kb_lock
from agenote.curator import (
    cmd_archive,
    cmd_restore,
    cmd_deduplicate,
    cmd_review,
)
from agenote.inbox_archive import cmd_inbox_archive
from agenote.memory import cmd_memory
from agenote.lint import cmd_lint
from agenote.orgfmt import cmd_format
from agenote.health import CARD_STALE_DAYS, cmd_health, cmd_gaps
from agenote.doctor import cmd_doctor
from agenote.viz.cli import add_viz_parser, cmd_viz

# 跨 agent 协同 4 件套（lazy import 到 wrapper 内，避免顶层拉起 sqlite/JSONL 依赖）
from agenote.reconcile import reconcile_source, trace_fact
from agenote.dream import DEFAULT_LIMIT as DEFAULT_DREAM_LIMIT, run_dream
from agenote.dream import DEFAULT_WINDOW_DAYS as DEFAULT_DREAM_WINDOW_DAYS
from agenote.distill import run_distill
from agenote.extract import run_extract
from agenote.extract.base import (
    EXTRACT_LIMIT,
    UnknownSourceError,
    safe_adapter_error,
)
from agenote.memscan import SOURCES as MEMSCAN_SOURCES, cmd_scan_memories

# 本 CLI 默认操作 agenote 域（~/Documents/Org/agenote/），与 MCP server 对齐。
# --domain human 切到人类知识库根（~/Documents/Org/）。
# 三入口共享 agenote 内核：本 CLI（通用）、agenote_mcp.py（MCP server）、
# agenote_cli.py（TS hooks 桥，已默认 agenote 域）。

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path


def cmd_init(args: argparse.Namespace, ctx=None) -> None:
    """初始化知识库目录结构，可选初始化 git 仓库。"""
    ctx = ctx or default_context()
    root = ctx.root
    # 检查根目录是否已存在且有实质内容
    if root.exists() and any(root.iterdir()):
        has_cards = ctx.experiences.exists() and any(ctx.experiences.iterdir())
        if has_cards:
            print(f"知识库已存在且有内容: {root}")
            print("如需重新初始化，请先备份并删除该目录。")
            return
        # 有目录但无卡片——可能是损坏的初始化，继续
        print(f"知识库目录已存在但为空，继续初始化: {root}")

    # 创建目录和模板文件（复用 ensure_dirs 逻辑）
    ensure_dirs(ctx)
    print(f"目录结构已创建: {root}")

    # git 初始化（默认启用，--no-git 跳过）
    use_git = not args.no_git
    if use_git:
        git_bin = shutil.which("git")
        if not git_bin:
            print("提示: 未找到 git，跳过版本控制初始化。")
            print(
                f"安装 git 后可手动运行: cd {root} && git init && git add -A && git commit -m 'chore(init): 初始化知识库'"
            )
            return

        git_dir = root / ".git"
        if git_dir.exists():
            print("git 仓库已存在，跳过 git init。")
            return

        # 检查父目录是否已有 git 仓库（避免嵌套）
        parent_git = _find_parent_git(root)
        if parent_git:
            print(f"警告: 父目录已有 git 仓库 ({parent_git})")
            print("跳过 git init 以避免嵌套仓库。")
            print(f"如需独立版本控制，请手动运行: cd {root} && git init")
            return

        # 创建 .gitignore
        gitignore = root / ".gitignore"
        if not gitignore.exists():
            atomic_write(
                gitignore,
                "# 机器生成索引\nindex.json\n# KB 进程锁\n.agenote.lock\n",
            )

        # git init + 初始 commit
        _run_git(["init"], cwd=root)
        _run_git(["add", ".gitignore", "MEMORY.org", "inbox.org"], cwd=root)
        _run_git(["commit", "-m", "chore(init): 初始化知识库"], cwd=root)
        print("git 仓库已初始化，初始 commit 已创建。")


def cmd_commit(args: argparse.Namespace, ctx=None) -> None:
    """提交知识库变更，封装 git add + commit。

    与「无脑 git add -A」的区别（D1 修复）：
      - **精准 add**：只提交本次策展产物（experiences/ + index.json + conversations/
        + kb-viz.html），不吞无关文件。可用 --all 退回旧行为。
      - **git 根解析**：ctx.root（如 ~/Documents/Org/agenote/）往往不是 git 仓库根
        （~/Documents/Org/），用 git rev-parse --show-toplevel 找真实根。
      - **commit.template**：默认走仓库 commit.gpgsign + commit.template 配置，
        不强制 --no-gpg-sign（除非显式 --no-gpg-sign）。
      - **message 规范**：建议用 Conventional Commits 前缀（chore(curate)/feat(card) 等），
        但不强校验——agent 可传任意 message。
    """
    ctx = ctx or default_context()
    root = ctx.root
    git_bin = shutil.which("git")
    if not git_bin:
        die("未找到 git，无法提交。")

    # 解析 git 仓库根：ctx.root 可能是子目录（agenote 域），.git 在更上层
    try:
        repo_root = _run_git(["rev-parse", "--show-toplevel"], cwd=root).strip()
    except SystemExit:
        die(f"不在 git 仓库内 ({root})。请先运行 'agenote init' 初始化。")
    repo_root_path = Path(repo_root)

    message = args.message
    if not message:
        die("请通过 -m 指定 commit message。")

    # 决定 add 策略：--all 退回旧行为（git add -A），否则精准 add 策展产物
    if args.all:
        add_targets: list[str] = ["-A"]
    else:
        # 策展产物清单（相对 repo_root）：由 config [commit].curated_paths 提供，
        # 默认跟随 paths.agenote_dir 动态生成。git add 对不存在的路径会报错，故逐个探测。
        candidates = [str(c) for c in config.get("commit", "curated_paths")]
        add_targets = [c for c in candidates if (repo_root_path / c).exists()]
        if not add_targets:
            print("未发现策展产物路径（experiences/index.json/conversations 等）。")
            print('如需提交全部变更，请用 agenote commit --all -m "..."')
            return

    # 检查是否有变更（用拟 add 的范围预览；--all 模式下 status 不传 -A，那是 add 的参数）
    status_args = [] if args.all else add_targets
    status = _run_git(["status", "--porcelain"] + status_args, cwd=repo_root_path)
    if not status.strip():
        print("没有待提交的变更。")
        return

    # --dry-run 预览不提交
    if args.dry_run:
        staged = [line[3:] for line in status.splitlines() if line.strip()]
        print(f'dry-run: 将 add 以下文件并 commit -m "{message}"')
        for f in staged:
            print(f"  + {f}")
        return

    # add
    _run_git(["add"] + add_targets, cwd=repo_root_path)

    # commit：遵循仓库 commit.gpgsign 配置（不强制 --no-gpg-sign）
    commit_cmd = ["commit", "-m", message]
    if args.no_gpg_sign:
        commit_cmd = ["-c", "commit.gpgsign=false"] + commit_cmd
    _run_git(commit_cmd, cwd=repo_root_path)
    print(f"已提交 ({ctx.name} @ {repo_root}): {message}")


def cmd_config(args: argparse.Namespace, ctx=None) -> None:
    """配置管理：init 生成带注释模板 / show 打印当前生效配置及来源。"""
    if args.config_cmd == "init":
        if config.CONFIG_PATH.exists():
            die(f"配置文件已存在: {config.CONFIG_PATH}（不覆盖；如需重新生成请先手动删除）")
        config.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        # config 模板写用户 XDG 配置目录（KB 外），保留直接写
        config.CONFIG_PATH.write_text(config.render_template(), encoding="utf-8")
        print(f"已生成配置模板: {config.CONFIG_PATH}")
        print("全部键默认注释掉——取消注释需要定制的键即可启用。")
        print("查看当前生效配置: agenote config show")
    elif args.config_cmd == "show":
        current_section = None
        for key, val, src in config.iter_resolved():
            # 节名 = SCHEMA 节（如 "extract.sources"），键名 = 最后一段
            section, name = key.rsplit(".", 1)
            if section != current_section:
                print(f"[{section}]")
                current_section = section
            print(f"  {name} = {val!r}  ({src})")
        print(f"\n配置文件: {config.CONFIG_PATH}")


def cmd_completions(args: argparse.Namespace, ctx=None) -> None:
    """生成并打印指定 shell 的补全脚本。"""
    from agenote.completions import COMPLETIONS_SHELLS, generate

    shell = (args.shell or "").lower()
    if shell not in COMPLETIONS_SHELLS:
        die(
            f"未知 shell: {args.shell!r}，可选: {', '.join(COMPLETIONS_SHELLS)} "
            f"（用法: agenote completions <{'|'.join(COMPLETIONS_SHELLS)}>）"
        )
    print(generate(shell), end="")


def _run_git(args: list[str], cwd: Path) -> str:
    """执行 git 命令，返回 stdout。失败时 die。"""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    if result.returncode != 0:
        # 这里是手动检查 returncode，并无异常对象；错误细节在 result.stderr
        # （刻意收口不外泄），消息只如实描述命令本身失败。
        die(f"git {' '.join(args)} 失败")
    return result.stdout


def _find_parent_git(start: Path) -> Path | None:
    """向上查找父目录中的 .git，返回其路径或 None。"""
    current = start.parent
    while current != current.parent:
        if (current / ".git").exists():
            return current / ".git"
        current = current.parent
    return None


def cmd_reindex(args: argparse.Namespace, ctx=None) -> None:
    """
    全量扫描 experiences/ 目录下所有卡片，重建 index.json。
    """
    ctx = ctx or default_context()
    ensure_dirs(ctx)

    index = _rebuild_index(ctx)
    _save_index(index, ctx)
    print(f"索引已重建 ({ctx.name}): {index['total']} 条卡片 → {ctx.index}")
    # S7：遗留 WEIGHT 属性不再静默忽略，告警提示清理（索引值已按公式重算）
    from agenote.index import find_legacy_weight_files  # lazy：cli 聚合层惯例

    legacy = find_legacy_weight_files(ctx)
    if legacy:
        preview = "、".join(legacy[:5]) + ("…" if len(legacy) > 5 else "")
        print(
            f"警告：{len(legacy)} 张卡片仍含遗留 :WEIGHT: 属性"
            f"（索引已忽略该值，按 usage/新鲜度公式重算），建议清理：{preview}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 跨 agent 协同 4 件套：reconcile / dream / distill / extract
# 均为薄 wrapper：读 args → 调 agenote 函数 → 格式化输出。
# 这 4 个函数内部自建 agenote_context（与 KB 卡片同库），不接受外部 ctx，
# 但为保持 dispatch 签名统一 (args, ctx) 仍接受并忽略 ctx。
# ═══════════════════════════════════════════════════════════════════════════════


def _print_report(
    d: dict,
    json_flag: bool,
    title: str,
    *,
    stream=None,
) -> None:
    """统一报告输出；失败报告可显式写 stderr，避免伪装成成功 stdout。"""
    output = stream or sys.stdout
    if json_flag:
        print(json.dumps(d, ensure_ascii=False, indent=2), file=output)
        return
    print(f"=== {title} ===", file=output)
    for k, v in d.items():
        if isinstance(v, list):
            print(f"{k}: {len(v)} 项", file=output)
            for it in v[:10]:
                if isinstance(it, dict):
                    # 取最像标题/来源的字段
                    label = (
                        it.get("title")
                        or it.get("source")
                        or it.get("id")
                        or str(it)[:60]
                    )
                    print(f"  + {label}", file=output)
                else:
                    print(f"  + {it}", file=output)
        elif isinstance(v, dict):
            print(f"{k}:", file=output)
            for sk, sv in v.items():
                print(f"  {sk}: {sv}", file=output)
        else:
            print(f"{k}: {v}", file=output)


def cmd_reconcile(args: argparse.Namespace, ctx=None) -> None:
    """跨 agent memory 只读 reconcile：抽取事实到 .reconcile/index.json。"""
    try:
        report = reconcile_source(
            source=args.source,
            dry_run=args.dry_run,
            prune_orphans=getattr(args, "prune_orphans", False),
        )
    except UnknownSourceError as exc:
        die(str(exc))
    except ValueError as exc:
        # adapter/schema 错误已由编排层向上传播；CLI 只展示受控提示或异常类型。
        die(safe_error_message(exc))
    title = f"reconcile ({args.source}, dry_run={args.dry_run})"
    if report.errors:
        _print_report(
            report.to_dict(),
            getattr(args, "json", False),
            title,
            stream=sys.stderr,
        )
        die("reconcile 完成但存在 adapter 错误；索引未更新")
    _print_report(
        report.to_dict(),
        getattr(args, "json", False),
        title,
    )


def cmd_dream(args: argparse.Namespace, ctx=None) -> None:
    """从 reconcile 事实启发式提炼候选新卡片。"""
    report = run_dream(
        window_days=args.window_days,
        offset=args.offset,
        limit=args.limit,
    )
    _print_report(
        report.to_dict(),
        getattr(args, "json", False),
        f"dream (window={args.window_days}d, offset={args.offset}, limit={args.limit})",
    )


def cmd_trace(args: argparse.Namespace, ctx=None) -> None:
    """回查 dream 候选的原始完整对话（溯源，不截断）。"""
    result = trace_fact(args.id)
    if "error" in result:
        die(f"[trace 错误] {safe_adapter_error(result['error'])}")
    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    source = result.get("source", "?")
    sess = result.get("session", {})
    print(f"=== trace {source}: {args.id} ===")
    if result.get("degraded"):
        print(f"[降级] {result.get('message', '')}")
        print(f"标题: {result.get('title', '')}")
        print(f"摘要内容:\n{result.get('content', '')}")
        return
    print(f"session: {sess.get('title', sess.get('cwd', '?'))}")
    msgs = result.get("messages", [])
    print(f"消息数: {len(msgs)}")
    print("-" * 60)
    for m in msgs:
        role = m.get("role", "?")
        ts = m.get("ts", "")
        print(f"\n--- [{role}] {ts} ---")
        # opencode/zcode: parts 列表；omp/其他: content 字段
        if "parts" in m:
            for p in m.get("parts", []):
                ptype = p.get("type", "")
                if ptype == "text":
                    print(p.get("text", ""))
                elif ptype == "reasoning":
                    print(f"[reasoning] {p.get('text', '')}")
                elif ptype == "tool":
                    inp = json.dumps(p.get("input", {}), ensure_ascii=False)
                    print(f"[tool: {p.get('tool', '?')}] {inp[:500]}")
                elif ptype == "patch":
                    print(f"[patch: {len(p.get('files', []))} files]")
        else:
            content = m.get("content", "")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        ptype = part.get("type", "")
                        if ptype == "text":
                            print(part.get("text", ""))
                        elif ptype == "tool_use":
                            inp = json.dumps(part.get("input", {}), ensure_ascii=False)
                            print(f"[tool_use: {part.get('name', '?')}] {inp[:500]}")
                        elif ptype == "tool_result":
                            c = part.get("content", "")
                            print(
                                f"[tool_result] {c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)[:500]}"
                            )
                    elif isinstance(part, str):
                        print(part)
            else:
                print(content)


def cmd_distill(args: argparse.Namespace, ctx=None) -> None:
    """工作流蒸馏：发现可沉淀为 skill 的工作流候选（纯只读）。"""
    report = run_distill()
    _print_report(
        report.to_dict(),
        getattr(args, "json", False),
        "distill",
    )


def cmd_extract(args: argparse.Namespace, ctx=None) -> None:
    """跨 agent 对话抽取为 Org 文件。"""
    try:
        d = run_extract(
            source=args.source,
            date=args.date,
            output_dir=args.output_dir,
            dry_run=args.dry_run,
            limit=args.limit,
        )
    except UnknownSourceError as exc:
        die(str(exc))
    except ValueError as exc:
        # adapter/schema 错误已由编排层向上传播；CLI 只展示受控提示或异常类型。
        die(safe_error_message(exc))
    title = f"extract ({args.source}, date={args.date or 'all'}, limit={args.limit}, dry_run={args.dry_run})"
    if d.get("failed"):
        _print_report(
            d,
            getattr(args, "json", False),
            title,
            stream=sys.stderr,
        )
        die("extract 完成但存在 adapter 错误")
    _print_report(
        d,
        getattr(args, "json", False),
        title,
    )


# ═══════════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════════
# 帮助文本
# ═══════════════════════════════════════════════════════════════════════════════


def print_help() -> None:
    print(f"""agenote — 知识库命令行工具

用法: agenote [--domain human|agenote] <子命令> [参数]

全局选项:
  --domain {{human,agenote}}  操作域（search 默认跨域，其余命令默认 agenote 域）
        agenote  → ~/Documents/Org/agenote/（agent 写入的卡片，本 CLI 默认）
        human    → ~/Documents/Org/（人类知识库根）
  --version              显示版本号

子命令:
  add       添加经验卡片
            agenote add --title "标题" [--category 类别] [--tech 技术栈]
                    [--type 类型] [--owner 执行者] [--entry 条目语义]
                    [--summary 总结] [--stdin] [--force]

  get       读取卡片详情
            agenote get <卡片文件名或ID> [--used]  读取并记录使用

  list      列出卡片
            agenote list [--category 类别] [--type 类型] [--owner 执行者] [--recent N] [--all]
            agenote list --unused-days 30            只列超 N 天未使用的卡片（降级候选，只读）

  search    全文检索
            agenote search <关键词...> [--context N] [--limit N]
            agenote search <关键词> [--context N] [--json]
            默认按空格、/、逗号拆分多关键词，大小写不敏感，按命中词数和次数排序。

  fields    列出已有字段值（用于优先复用标签）
            agenote fields [--category] [--tech] [--type] [--owner]

  tags      按标签检索
            agenote tags <标签> [标签2 ...]

  memory   管理记忆系统
            agenote memory                          列出记忆概览
            agenote memory --type feedback|project|reference  按类型过滤
            agenote memory --project <名称|路径|.>   检索项目记忆（含 PATH/UPDATED 健康提示）
            agenote memory --add --type <类型> --title "标题" --stdin  添加记忆
            agenote memory --list [--type U|F|P|E|R] [--scope S] [--json]  只读列出条目
            agenote memory --stale                   列出陈旧记忆
            agenote memory --revalidate             只读列出待重验条目
            agenote memory --validate <ID>          刷新单条 VALIDATED_AT
            agenote memory --touch <ID>              更新时间戳
            agenote memory --archive <ID>            归档记忆到 deprecated
            agenote memory --archive-to-file <ID>    归档 feedback 到 MEMORY-ARCHIVE.org
            agenote memory --project-touch <名称>    更新项目 LAST_ACTIVE
             agenote memory --export [--type T] [--scope S] [--project P]  投影到宿主聚合文件
             agenote memory --supersede <新ID> <旧ID>  裁决：旧条目入 deprecated
             agenote memory --migrate                 一次性迁移 ORIGIN_ID 到相对路径派生
             agenote memory --conflicts              只读列出冲突队列
            agenote memory --get                     查看全文
            agenote memory --import [--source X|all] [--dry-run]  N2 摄取导入

  reindex   重建知识库索引（WEIGHT 随之按 usage/新鲜度公式重算）
            agenote reindex

  lint      格式校验与报告（格式问题 + 语义问题：枚举漂移、缺失字段）
            agenote lint                 检查所有卡片（格式 + 语义）
            agenote lint --fix           自动修复可安全自动化的（= format）
            agenote lint --check         仅检查，退出码=问题数（CI 用）
            agenote lint --fix file.org  修复指定文件

  format   格式化卡片（默认直接写盘，agent 写完卡片调用一次即可）
            agenote format               格式化所有卡片
            agenote format --check       只检查不写盘
            agenote format file.org      格式化指定文件

  inbox    快速捕获到 inbox.org
            agenote inbox "待捕获的想法" 或 echo "内容" | agenote inbox

  stats    知识库统计概览
            agenote stats

  connect  双向链接两张卡片
            agenote connect <卡片ID> <卡片ID> [--desc 描述]

  update   更新已有卡片
            agenote update <卡片ID> [--status STATUS] [--category 类别] [--tech 技术]
                     [--type 类型] [--owner 执行者] [--append-to 章节 --append-text 内容] [--stdin]
                     [--force]

  init     初始化知识库
            agenote init            创建目录结构 + git 仓库 + 初始 commit
            agenote init --no-git   仅创建目录结构，跳过 git

  completions shell 补全脚本
            agenote completions bash   生成 bash 补全
            agenote completions zsh    生成 zsh 补全
            agenote completions fish   生成 fish 补全（推荐 fish 用户）

  config   配置管理（~/.config/agenote/config.toml，优先级 env > 文件 > 默认）
            agenote config init     生成带注释的配置模板
            agenote config show     打印当前生效配置及来源（env/file/default）

  commit   提交知识库变更（默认只 add 策展产物：experiences/index.json/conversations/kb-viz.html）
            agenote commit -m "chore(curate): 新增 K 张 / 更新 M 张"
            agenote commit --all -m "..."              提交全部变更（git add -A）
            agenote commit --no-gpg-sign -m "..."      跳过 GPG 签名（cron 等无 pinentry 场景）
            message 建议 Conventional Commits 格式（chore(curate)/feat(card) 前缀）

  touch    更新卡片时间戳
            agenote touch <卡片ID>              更新 LAST_USED + LAST_VERIFIED
            agenote touch <卡片ID> --used-only  只更新 LAST_USED
            agenote touch <卡片ID> --session <ID>  同会话重复 touch 只计一次 USAGE

  sweep    done/stable → stale 降级（默认 dry-run 只读出清单）
            agenote sweep                 列出降级候选（done 按未用天数，stable 按未验证天数）
            agenote sweep --apply         执行降级（STATUS=stale + 刷 LAST_VERIFIED）

  merge    合并卡片
            agenote merge <主卡片ID> <次卡片ID>... [--desc 原因]

  archive  归档卡片
            agenote archive <卡片ID>... [--reason 原因]  归档指定卡片（支持批量）
            agenote archive --list [--json]             列出归档卡片
            agenote archive --stale [--json]            列出归档候选（只读，去留由 agent 审查）

  restore  恢复归档卡片
            agenote restore <卡片ID> [--status stable]

  deduplicate 检测重复卡片
            agenote deduplicate [--threshold {DEDUP_THRESHOLD}] [--json]

  review   审查卡片（只读；修复用 update/connect 显式执行）
            agenote review <卡片ID>

  health   知识库健康度报告
            agenote health [--duplicates] [--quality]

  gaps     知识空白检测（类别×类型矩阵）
            agenote gaps [--stale-days {CARD_STALE_DAYS}] [--json]

  reconcile 跨 agent memory 只读 reconcile（抽取事实到 .reconcile/，不写回源）
            agenote reconcile [--source all] [--dry-run] [--prune-orphans]
            agenote reconcile --source opencode --dry-run

  dream    从 reconcile 事实启发式提炼候选新卡片（不调 LLM；唯一落盘是游标 dream-cursor.json）
            agenote dream [--window-days {DEFAULT_DREAM_WINDOW_DAYS}] [--offset N] [--limit N]
            候选含 source_trace 字段——用 trace 命令回查完整原始对话

  trace    回查 dream 候选的原始完整对话（溯源，不截断，含工具调用/推理/补丁）
            agenote trace --id <source_trace>
            --id 取自 dream 候选的 source_trace（如 opencode:ses_x:msg_y）

  distill  工作流蒸馏：发现可沉淀为 skill 的工作流候选（纯只读，草稿由 agent 撰写）
            agenote distill

  extract  跨 agent 对话抽取为 Org 文件（输出到 conversations/<date>/）
            agenote extract [--source all] [--date YYYY-MM-DD] [--limit N] [--dry-run]
            --date 非空时按对话时间戳过滤（只抽该日对话）；--limit 0=不限（默认 500）

  help     显示本帮助

配置常量（修改文件头部即可调整）:
  KB_ROOT      人类知识库根 ({KB_ROOT})
  STALE_DAYS   陈旧记忆阈值 ({STALE_DAYS} 天)
  SEED_TYPES  种子 type 值 ({", ".join(sorted(SEED_TYPES))})
              正式 type = 种子 ∪ 索引中非归档卡片数达晋升阈值（默认 10）的 type
  VALID_OWNERS 合法 owner 值 ({", ".join(sorted(VALID_OWNERS))})

默认操作域: agenote（~/Documents/Org/agenote/）
  人类知识库根: {KB_ROOT}
  --domain human 切到人类根；reconcile/dream/distill/extract 始终操作 agenote 域
""")


# ═══════════════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════════════


def main() -> None:
    try:
        _main()
    except SystemExit as exc:
        if exc.code is None or isinstance(exc.code, int):
            raise
        print("错误: 操作失败（SystemExit）", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except GeneratorExit:
        raise
    except BaseException as exc:
        # 公共入口也不让自定义 BaseException 的正文/traceback 逃逸。
        print(f"错误: {safe_error_message(exc)}", file=sys.stderr)
        raise SystemExit(1) from None


def _main() -> None:
    # 手动拦截 -h/--help（在 argparse 之前，避免 prog 名暴露问题 + 兼容 --domain 前置）
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print_help()
        sys.exit(0)

    parser = argparse.ArgumentParser(
        prog="agenote",
        description="agenote — 知识库命令行工具（默认 agenote 域；search 默认跨域）",
        add_help=False,
    )
    # 全局参数：--domain 决定读写哪棵目录树；--version 修复 argparse 露馅问题
    parser.add_argument(
        "--domain",
        choices=["human", "agenote"],
        default="__auto__",
        help="操作域：agenote（默认，~/Documents/Org/agenote/）或 human（~/Documents/Org/）。"
        "不指定时 search 做跨域加权检索，其他命令默认 agenote 域。",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"agenote {_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command")

    # ── add ───────────────────────────────────────────────────────────────
    add_parser = subparsers.add_parser("add", help="添加经验卡片")
    add_parser.add_argument("--title", required=True, help="任务标题")
    add_parser.add_argument(
        "--category", default="general", help="类别（自由输入，优先复用已有标签）"
    )
    add_parser.add_argument("--tech", help="技术栈（自由输入，优先复用已有标签）")
    add_parser.add_argument(
        "--type",
        help="类型（标准: debug|refactor|research|workflow|feature|config；"
        "复用已有值免检，新类型需 --force）",
    )
    add_parser.add_argument("--owner", default="ai", help="执行者（human|ai|collab）")
    add_parser.add_argument(
        "--entry",
        "--entry-type",
        dest="entry",
        help="条目语义（mistake|note|ascended）",
    )
    add_parser.add_argument("--summary", help="一句话总结")
    add_parser.add_argument(
        "--stdin", action="store_true", help="从标准输入读取详细内容"
    )
    add_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="允许创建知识库中不存在的新 type",
    )

    # ── get ───────────────────────────────────────────────────────────────
    get_parser = subparsers.add_parser("get", help="读取卡片详情")
    get_parser.add_argument("target", help="卡片文件名或ID")
    get_parser.add_argument(
        "--used",
        action="store_true",
        help="读取后递增 USAGE_COUNT 并更新 LAST_USED",
    )

    # ── list ──────────────────────────────────────────────────────────────
    list_parser = subparsers.add_parser("list", help="列出卡片")
    list_parser.add_argument("--category", help="按类别过滤")
    list_parser.add_argument("--type", help="按类型过滤")
    list_parser.add_argument("--owner", help="按执行者过滤")
    list_parser.add_argument("--recent", type=int, help="显示最近 N 条")
    list_parser.add_argument(
        "--unused-days",
        type=int,
        help="只列最后使用（缺省用创建日期）距今超 N 天的卡片（降级候选，只读）",
    )
    list_parser.add_argument("--all", action="store_true", help="显示全部")
    list_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    # ── search ────────────────────────────────────────────────────────────
    search_parser = subparsers.add_parser("search", help="全文检索")
    search_parser.add_argument(
        "query",
        nargs="+",
        help="关键词；多个词可空格分隔（自动合并为一次查询）",
    )
    search_parser.add_argument(
        "--context",
        type=int,
        default=int(config.get("search", "context_lines")),
        help="上下文行数",
    )
    search_parser.add_argument(
        "--limit",
        type=int,
        default=int(config.get("search", "limit")),
        help="最多显示的文件数",
    )
    search_parser.add_argument(
        "--max-blocks",
        type=int,
        default=int(config.get("search", "max_blocks")),
        help="每个文件最多显示的上下文块数",
    )
    search_parser.add_argument(
        "--all-terms", action="store_true", help="只显示包含所有关键词的文件"
    )
    search_parser.add_argument(
        "--case-sensitive", action="store_true", help="大小写敏感匹配"
    )
    search_parser.add_argument("--json", action="store_true", help="JSON 输出")
    search_parser.add_argument(
        "--freshness", action="store_true", help="超期未验证结果追加 (unverified Nd) 标记"
    )

    # ── fields ────────────────────────────────────────────────────────────
    fields_parser = subparsers.add_parser("fields", help="列出已有字段值")
    fields_parser.add_argument(
        "--category", action="store_true", help="只列出 category"
    )
    fields_parser.add_argument("--tech", action="store_true", help="只列出 tech")
    fields_parser.add_argument(
        "--type", dest="type_", action="store_true", help="只列出 type"
    )
    fields_parser.add_argument("--owner", action="store_true", help="只列出 owner")
    fields_parser.add_argument("--json", action="store_true", help="JSON 输出")

    # ── tags ──────────────────────────────────────────────────────────────
    tags_parser = subparsers.add_parser("tags", help="按标签检索")
    tags_parser.add_argument("tags", nargs="+", help="标签")
    tags_parser.add_argument("--json", action="store_true", help="JSON 输出")

    # ── memory ──────────────────────────────────────────────────────────
    memory_parser = subparsers.add_parser("memory", help="管理记忆系统")
    memory_parser.add_argument(
        "--type",
        choices=["feedback", "project", "reference", "user", "environment",
                 "U", "F", "P", "E", "R"],
        help="记忆类型过滤（长名或 U|F|P|E|R 短字母）",
    )
    memory_parser.add_argument(
        "--list", action="store_true", help="只读列出条目（支持 --type/--scope/--json）"
    )
    memory_parser.add_argument(
        "--scope", choices=["user", "project", "machine"], help="按 SCOPE 过滤（配合 --list）"
    )
    memory_parser.add_argument(
        "--json", action="store_true", help="JSON 输出（配合 --list）"
    )
    memory_parser.add_argument("--project", metavar="IDENTIFIER", help="项目名或路径")
    memory_parser.add_argument("--add", action="store_true", help="添加记忆")
    memory_parser.add_argument("--get", action="store_true", help="查看记忆全文")
    memory_parser.add_argument("--title", help="记忆标题")
    memory_parser.add_argument(
        "--stdin", action="store_true", help="从标准输入读取内容"
    )
    memory_parser.add_argument("--stale", action="store_true", help="列出陈旧记忆")
    memory_parser.add_argument(
        "--revalidate", action="store_true", help="只读列出待重验条目（切机批量/过期/孤儿）"
    )
    memory_parser.add_argument("--validate", metavar="ID", help="刷新单条 VALIDATED_AT")
    memory_parser.add_argument("--touch", metavar="ID", help="更新记忆时间戳")
    memory_parser.add_argument("--archive", metavar="ID", help="归档记忆到 deprecated")
    memory_parser.add_argument(
        "--archive-to-file", metavar="ID", help="归档 feedback 到 MEMORY-ARCHIVE.org"
    )
    memory_parser.add_argument(
        "--project-touch", metavar="NAME", help="更新项目 LAST_ACTIVE 时间戳"
    )
    memory_parser.add_argument(
        "--freshness", action="store_true", help="条目列表追加 (unverified Nd) 时效标记"
    )
    memory_parser.add_argument(
        "--import", dest="do_import", action="store_true",
        help="N2 摄取：从 scan-memories 源导入（写命令；--dry-run 只预览，仍持锁）",
    )
    memory_parser.add_argument(
        "--source", default="all",
        help="import 来源（zcode|claude|codex|pi|reasonix|hermes|all，默认 all）",
    )
    memory_parser.add_argument(
        "--dry-run", action="store_true", help="只预览不落盘（import 用）",
    )
    memory_parser.add_argument(
        "--export", action="store_true",
        help="N3 投影到宿主聚合文件（幂等+漂移检测；路径只来自配置）",
    )
    memory_parser.add_argument(
        "--supersede", nargs=2, metavar=("NEW_ID", "OLD_ID"),
        help="N4 裁决：新条目记 SUPERSEDES，旧条目入 deprecated",
    )
    memory_parser.add_argument(
        "--migrate", action="store_true",
        help="一次性迁移：存量 ORIGIN_ID 重算为相对源根派生（幂等可重跑）",
    )
    memory_parser.add_argument(
        "--conflicts", action="store_true", help="只读列出冲突队列（配合 --json）",
    )

    # ── reindex ───────────────────────────────────────────────────────────
    subparsers.add_parser(
        "reindex", help="重建知识库索引（WEIGHT 随之按 usage/新鲜度公式重算）"
    )

    # ── lint ──────────────────────────────────────────────────────────────
    lint_parser = subparsers.add_parser("lint", help="格式校验与报告（含语义问题）")
    lint_parser.add_argument(
        "--fix", action="store_true", help="自动修复可安全自动化的（= format）"
    )
    lint_parser.add_argument(
        "--check", action="store_true", help="仅检查，退出码=min(问题数,127)"
    )
    lint_parser.add_argument(
        "--json", action="store_true", help="分类结构化输出（agent 可消费、可差分）"
    )
    lint_parser.add_argument("files", nargs="*", help="目标文件（默认检查全部）")

    # ── format ───────────────────────────────────────────────────────────
    fmt_parser = subparsers.add_parser(
        "format", help="格式化卡片（默认直接写盘，agent 调用一次即可）"
    )
    fmt_parser.add_argument("--check", action="store_true", help="只检查不写盘")
    fmt_parser.add_argument("files", nargs="*", help="目标文件（默认格式化全部）")

    # ── inbox ──────────────────────────────────────────────────────────────
    inbox_parser = subparsers.add_parser("inbox", help="快速捕获到 inbox.org")
    inbox_parser.add_argument("content", nargs="?", help="捕获内容")

    # ── inbox-archive ──────────────────────────────────────────────────────
    # PLAN §2.3:把 inbox 条目归档为结构化经验卡片,slug/reindex/prune 全部由 CLI 处理。
    inbox_archive_parser = subparsers.add_parser(
        "inbox-archive",
        help="把 inbox 条目归档为 experiences/<category>/ 经验卡片",
    )
    inbox_archive_parser.add_argument(
        "--category", required=True, help="目标 category(experiences/ 子目录)"
    )
    inbox_archive_parser.add_argument(
        "--reason", help="可选,写入卡片顶部 Archive reason 注释"
    )
    inbox_archive_parser.add_argument(
        "--no-reindex",
        action="store_true",
        help="跳过完成后的全量 reindex(批量场景)",
    )
    inbox_archive_parser.add_argument(
        "--prune",
        action="store_true",
        help="从 inbox.org 删除已归档条目(默认保留以备回查)",
    )
    inbox_archive_parser.add_argument(
        "--stdin",
        action="store_true",
        help="显式从 stdin 读 JSON(默认即从 stdin 读)",
    )

    # ── stats ───────────────────────────────────────────────────────────────
    subparsers.add_parser("stats", help="知识库统计概览")

    # ── connect ─────────────────────────────────────────────────────────────
    connect_parser = subparsers.add_parser("connect", help="双向链接两张卡片")
    connect_parser.add_argument("id_a", help="卡片 A 的 ID 或文件名")
    connect_parser.add_argument("id_b", help="卡片 B 的 ID 或文件名")
    connect_parser.add_argument("--desc", help="链接描述")

    # ── update ──────────────────────────────────────────────────────────────
    update_parser = subparsers.add_parser("update", help="更新已有卡片")
    update_parser.add_argument("target", help="卡片 ID 或文件名")
    update_parser.add_argument("--status", help="新状态")
    update_parser.add_argument("--category", help="新类别")
    update_parser.add_argument("--tech", help="新技术栈")
    update_parser.add_argument(
        "--type", dest="type_", help="新类型（重分类：同步属性/标签/文件名/索引）"
    )
    update_parser.add_argument("--owner", help="新执行者")
    update_parser.add_argument("--append-to", help="追加内容到指定章节")
    update_parser.add_argument("--append-text", help="要追加的内容")
    update_parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="配合 --type 允许改为知识库中不存在的新 type",
    )
    update_parser.add_argument(
        "--stdin", action="store_true", help="从标准输入读取追加内容"
    )

    # ── init ──────────────────────────────────────────────────────────────
    init_parser = subparsers.add_parser("init", help="初始化知识库")
    init_parser.add_argument("--no-git", action="store_true", help="跳过 git 初始化")

    # ── commit ────────────────────────────────────────────────────────────
    commit_parser = subparsers.add_parser("commit", help="提交知识库变更")
    commit_parser.add_argument(
        "-m",
        "--message",
        required=True,
        help="commit message（建议 Conventional Commits 格式，如 chore(curate): …）",
    )
    commit_parser.add_argument(
        "--all",
        action="store_true",
        help="提交全部变更（git add -A，可能吞无关文件；默认只 add 策展产物）",
    )
    commit_parser.add_argument(
        "--no-gpg-sign",
        action="store_true",
        help="跳过 GPG 签名（默认遵循仓库 commit.gpgsign）",
    )
    commit_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览不提交（列出将 add 的文件）",
    )

    # ── help ──────────────────────────────────────────────────────────────

    subparsers.add_parser("help", help="显示本帮助")

    # ── 新命令 ─────────────────────────────────────────────────────────────

    # ── touch ──────────────────────────────────────────────────────────────
    touch_parser = subparsers.add_parser("touch", help="更新卡片时间戳")
    touch_parser.add_argument("target", help="卡片 ID 或文件名")
    touch_parser.add_argument(
        "--used-only", action="store_true", help="只更新 LAST_USED"
    )
    touch_parser.add_argument(
        "--session", metavar="ID", help="会话幂等键：同卡同 session 首次 USAGE_COUNT+1，重复只刷时间戳"
    )

    # ── sweep ──────────────────────────────────────────────────────────────
    sweep_parser = subparsers.add_parser("sweep", help="列出或执行 done/stable → stale 降级")
    sweep_parser.add_argument(
        "--apply", action="store_true", help="执行降级（默认 dry-run 只出清单）"
    )
    sweep_parser.add_argument("--json", action="store_true", help="JSON 输出")

    # ── merge ──────────────────────────────────────────────────────────────
    merge_parser = subparsers.add_parser("merge", help="合并卡片")
    merge_parser.add_argument("primary", help="主卡片 ID")
    merge_parser.add_argument("secondary", nargs="+", help="要合并的卡片 ID")
    merge_parser.add_argument("--desc", help="合并原因")

    # ── archive ─────────────────────────────────────────────────────────────
    archive_parser = subparsers.add_parser("archive", help="归档卡片")
    archive_parser.add_argument("id", nargs="*", help="卡片 ID（支持批量）")
    archive_parser.add_argument("--reason", help="归档原因")
    archive_parser.add_argument(
        "--list", dest="list_cards", action="store_true", help="列出归档卡片"
    )
    archive_parser.add_argument(
        "--stale", action="store_true", help="列出归档候选（只读，去留由 agent 审查）"
    )
    archive_parser.add_argument("--json", action="store_true", help="JSON 输出")

    # ── restore ─────────────────────────────────────────────────────────────
    restore_parser = subparsers.add_parser("restore", help="恢复归档卡片")
    restore_parser.add_argument("id", help="卡片 ID")
    restore_parser.add_argument(
        "--status", default="stable", help="恢复到的状态（默认 stable）"
    )

    # ── deduplicate ─────────────────────────────────────────────────────────
    dedup_parser = subparsers.add_parser("deduplicate", help="检测重复卡片")
    dedup_parser.add_argument(
        "--threshold",
        type=float,
        default=DEDUP_THRESHOLD,
        help=f"相似度阈值（默认 {DEDUP_THRESHOLD}）",
    )
    dedup_parser.add_argument("--json", action="store_true", help="JSON 输出")

    # ── review ──────────────────────────────────────────────────────────────
    review_parser = subparsers.add_parser("review", help="审查卡片（只读）")
    review_parser.add_argument("id", help="卡片 ID")

    # ── health ──────────────────────────────────────────────────────────────
    health_parser = subparsers.add_parser("health", help="知识库健康度报告")
    health_parser.add_argument(
        "--duplicates", action="store_true", help="检测疑似重复卡片"
    )
    health_parser.add_argument(
        "--quality", action="store_true", help="检测质量问题（章节/元数据/Markdown）"
    )

    # ── gaps ────────────────────────────────────────────────────────────────
    gaps_parser = subparsers.add_parser("gaps", help="知识空白检测（类别×类型矩阵）")
    gaps_parser.add_argument(
        "--stale-days",
        type=int,
        default=CARD_STALE_DAYS,
        help=f"陈旧阈值（天，默认 {CARD_STALE_DAYS}）",
    )
    gaps_parser.add_argument("--json", action="store_true", help="JSON 格式输出")

    add_viz_parser(subparsers)

    # ── reconcile ───────────────────────────────────────────────────────────
    reconcile_parser = subparsers.add_parser(
        "reconcile", help="跨 agent memory 只读 reconcile（抽取事实到 .reconcile/）"
    )
    reconcile_parser.add_argument(
        "--source",
        default="all",
        help="opencode|zcode|omp|crush|codex|claude|all（默认 all）",
    )
    reconcile_parser.add_argument("--dry-run", action="store_true", help="只预览不落盘")
    reconcile_parser.add_argument(
        "--prune-orphans",
        action="store_true",
        help="显式清理 orphan 标记的旧事实（单源模式只清本源）",
    )
    reconcile_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    # ── dream ───────────────────────────────────────────────────────────────
    dream_parser = subparsers.add_parser(
        "dream",
        help="从 reconcile 事实启发式提炼候选新卡片（不调 LLM；唯一落盘是 dream-cursor.json 游标）",
    )
    dream_parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_DREAM_WINDOW_DAYS,
        help=f"回看窗口（天，默认 {DEFAULT_DREAM_WINDOW_DAYS}；0=不过滤）",
    )
    dream_parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="跳过前 N 个候选（多轮抽取用）。注意排序随索引更新漂移，见 report.snapshot_hash",
    )
    dream_parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_DREAM_LIMIT,
        help=f"本次最多返回 N 个候选（默认 {DEFAULT_DREAM_LIMIT}）",
    )
    dream_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    # ── trace ───────────────────────────────────────────────────────────────
    trace_parser = subparsers.add_parser(
        "trace", help="回查 dream 候选的原始完整对话（溯源，不截断）"
    )
    trace_parser.add_argument(
        "--id",
        required=True,
        help="fact_id（DreamCandidate.source_trace 的值，如 opencode:ses_x:msg_y）",
    )
    trace_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    # ── distill ─────────────────────────────────────────────────────────────
    distill_parser = subparsers.add_parser(
        "distill", help="工作流蒸馏：发现可沉淀为 skill 的工作流候选（纯只读）"
    )
    distill_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    # ── extract ─────────────────────────────────────────────────────────────
    extract_parser = subparsers.add_parser(
        "extract", help="跨 agent 对话抽取为 Org 文件（输出到 conversations/）"
    )
    extract_parser.add_argument(
        "--source",
        default="all",
        help="opencode|zcode|omp|crush|codex|claude|all（默认 all）",
    )
    extract_parser.add_argument(
        "--date",
        default="",
        help="目标日期 YYYY-MM-DD（默认昨天）；非空时按对话时间戳过滤，只抽该日对话",
    )
    extract_parser.add_argument(
        "--output-dir", default="", help="输出目录（默认 conversations/<date>/）"
    )
    extract_parser.add_argument(
        "--limit",
        type=int,
        default=EXTRACT_LIMIT,
        help=f"每源最大抽取条数（默认 {EXTRACT_LIMIT}；0=不限制）",
    )
    extract_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="只预览不落盘（默认会真写盘；传 --dry-run 才不写）",
    )
    extract_parser.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    # ── scan-memories ───────────────────────────────────────────────────────
    scan_mem_parser = subparsers.add_parser(
        "scan-memories",
        help="只读扫描各 agent 记忆库（zcode/claude/codex/pi/reasonix/hermes，"
        "供策展审查后显式导入，不写 KB）",
    )
    scan_mem_parser.add_argument(
        "--source",
        default="all",
        choices=sorted(MEMSCAN_SOURCES) + ["all"],
        help="zcode|claude|codex|pi|reasonix|hermes|all（默认 all）",
    )
    scan_mem_parser.add_argument(
        "--json", action="store_true", help="输出 JSON（含记忆全文，agent 消费）"
    )

    # ── doctor ──────────────────────────────────────────────────────────────
    doctor_parser = subparsers.add_parser(
        "doctor", help="环境自诊断：外部工具/配置/KB 结构（纯只读）"
    )
    doctor_parser.add_argument(
        "--json", action="store_true", help="JSON 结构化输出（agent 消费）"
    )

    # ── completions ─────────────────────────────────────────────────────────
    comp_parser = subparsers.add_parser(
        "completions", help="生成 shell 补全脚本（bash/zsh/fish）"
    )
    comp_parser.add_argument(
        "shell",
        nargs="?",
        choices=["bash", "zsh", "fish"],
        help="目标 shell（bash|zsh|fish）",
    )

    # ── config ──────────────────────────────────────────────────────────────
    config_parser = subparsers.add_parser(
        "config", help="配置管理（config.toml 生成与查看）"
    )
    config_sub = config_parser.add_subparsers(dest="config_cmd", required=True)
    config_sub.add_parser(
        "init", help=f"生成带注释的配置模板到 {config.CONFIG_PATH}"
    )
    config_sub.add_parser(
        "show", help="打印当前生效配置及来源（env / file / default）"
    )

    args = parser.parse_args()

    if args.command is None or args.command == "help":
        print_help()
        sys.exit(0)

    # 变更类子命令：dispatch 时持 KB 锁执行（读-改-写型命令的并发互斥）。
    # review/lint 默认只读，但带 --fix 时写文件，统一加锁换取简单。
    MUTATING_COMMANDS = {
        "add", "update", "touch", "sweep", "merge", "archive", "restore", "connect",
        "inbox", "inbox-archive", "memory", "reindex",
        "lint", "format", "commit", "init",
        "distill", "reconcile", "extract",
    }
    # dream 保持只读：游标写在 run_dream 内自持 kb_lock，不进全局锁。
    if args.command == "get" and getattr(args, "used", False):
        MUTATING_COMMANDS.add("get")

    commands = {
        "add": cmd_add,
        "get": cmd_get,
        "list": cmd_list,
        "search": cmd_search,
        "fields": cmd_fields,
        "tags": cmd_tags,
        "memory": cmd_memory,
        "reindex": cmd_reindex,
        "lint": cmd_lint,
        "format": cmd_format,
        "inbox": cmd_inbox,
        "inbox-archive": cmd_inbox_archive,
        "stats": cmd_stats,
        "connect": cmd_connect,
        "update": cmd_update,
        "init": cmd_init,
        "commit": cmd_commit,
        "config": cmd_config,
        "completions": cmd_completions,
        # 新命令
        "touch": cmd_touch,
        "sweep": cmd_sweep,
        "merge": cmd_merge,
        "archive": cmd_archive,
        "restore": cmd_restore,
        "deduplicate": cmd_deduplicate,
        "review": cmd_review,
        "health": cmd_health,
        "gaps": cmd_gaps,
        "doctor": cmd_doctor,
        "viz": cmd_viz,
        # 跨 agent 协同 4 件套
        "reconcile": cmd_reconcile,
        "dream": cmd_dream,
        "trace": cmd_trace,
        "distill": cmd_distill,
        "extract": cmd_extract,
        # 记忆库巡检（只读，免锁）
        "scan-memories": cmd_scan_memories,
    }
    if args.command in commands:
        # 初始化上下文、确保目录和读取参数都在同一公共错误边界内；否则
        # ensure_dirs 阶段的未知异常会绕过下面的命令级捕获。
        try:
            # 解析域：--domain 显式指定 → 仅该域；__auto__ → search 做跨域，其余默认 agenote
            if args.domain == "human":
                ctx = default_context()
            elif args.domain == "agenote":
                ctx = agenote_context()
            elif args.command == "search":
                ctx = agenote_context()  # 仅用于 ensure_dirs；search 内部做跨域
                args._cross_domain = True
            else:
                ctx = agenote_context()
            if ctx is not None and args.command not in ("init", "config", "completions"):
                ensure_dirs(ctx)
        except SystemExit:
            raise
        except Exception as exc:
            # 与命令级边界一致：只展示受控错误或异常类型，不泄漏内部异常正文。
            die(safe_error_message(exc))
        # 变更类命令持全局 KB 锁（学 claude-obsidian：多 agent 并发写入互斥，
        # 锁在 agent 域根，一把锁覆盖人类+agent 两域；临界区毫秒级无性能问题）
        command = commands[args.command]
        try:
            # memory --list/--conflicts 只读不持锁；--export 按 N3 不持 KB 锁（外部目录无锁语义）；
            # import 即使 --dry-run 仍持锁（与写路径同一临界区）；其余 memory 子动作仍走锁
            read_only = args.command == "memory" and bool(
                getattr(args, "list", False) or getattr(args, "conflicts", False)
            )
            no_lock = read_only or (
                args.command == "memory" and bool(getattr(args, "export", False))
            )
            if args.command in MUTATING_COMMANDS and not no_lock:
                with kb_lock(agenote_context().root / ".agenote.lock"):
                    command(args, ctx)
            else:
                command(args, ctx)
        except SystemExit:
            raise
        except Exception as exc:
            # CLI 是用户边界：无论受控错误还是未知异常，都不能把 traceback
            # 暴露给公共终端；safe_error_message 只给出受控消息或异常类型。
            die(safe_error_message(exc))
    else:
        die(f"未知子命令: {args.command}。运行 'agenote help' 查看帮助。")


if __name__ == "__main__":
    main()

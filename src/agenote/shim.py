#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
#
"""agenote_cli — pi 插件用的轻量入口（不走 MCP 协议）。

pi 的 ExtensionAPI 不提供 MCP 调用接口，TS 插件只能 execSync 外部进程。
本脚本复用 agenote 内核，输出人类可读文本。纯 stdlib，零依赖，直接 python3 运行。

用法:
    agenote_cli health          agenote 健康度报告（只读，供 /agenote-health 与状态注入）
    agenote_cli context ...     注入简报（只读免锁；fetchBriefing 消费 stdout）

策展不在 shim 提供——由 agent 依据 agenote-curator skill 编排原子命令执行。
"""

import argparse
import sys

from agenote.core import agenote_context, ensure_dirs, safe_error_message
from agenote.context import cmd_context
from agenote.health import cmd_health


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
        print(f"错误: {safe_error_message(exc)}", file=sys.stderr)
        raise SystemExit(1) from None


def _main() -> None:
    parser = argparse.ArgumentParser(
        prog="agenote_cli",
        description="agenote 轻量 CLI（pi 插件入口，不走 MCP）",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("health", help="agenote 健康度报告")
    # context 子命令：参数集/choices/默认值与 cli.py 的 context parser 对齐，
    # 转发到同一 cmd_context 实现（pi fetchBriefing 调本入口取注入正文）。
    context_parser = sub.add_parser(
        "context", help="生成注入简报（只读免锁；开关/条目不满足时零字节输出）"
    )
    context_parser.add_argument(
        "--mode", choices=["session", "recall"], default="session",
        help="session=开篇简报（默认）；recall=BM25 召回（需 --query）",
    )
    context_parser.add_argument("--query", help="recall 检索词（recall 模式必填）")
    context_parser.add_argument(
        "--budget", type=int, default=None,
        help="输出字符预算（恒为字符；默认取 [injection].default_budget）",
    )
    context_parser.add_argument(
        "--project", metavar="NAME", help="项目名或路径（确定性匹配，不模糊）",
    )
    context_parser.add_argument(
        "--types", default=None, help="逗号分隔类型集（默认取 [injection].types_default）",
    )
    context_parser.add_argument(
        "--host",
        choices=["zcode", "claude", "codex", "pi", "opencode", "hermes", "generic"],
        default="generic",
        help="调用方宿主（查 [injection.hosts] 开关；generic 走默认值）",
    )
    context_parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="text=注入正文（非 ok 态零字节）；json=带 status 的结构化输出",
    )
    args = parser.parse_args()

    if args.cmd == "context":
        # 与主 CLI 同口径：context 走 agenote 域、只读免锁、不 ensure_dirs
        # （全新 KB 按 empty 处理，零写盘副作用）。
        cmd_context(args, agenote_context())
        return

    ctx = agenote_context()
    ensure_dirs(ctx)

    # cmd_health 不读 args
    ns = argparse.Namespace()
    cmd_health(ns, ctx)


if __name__ == "__main__":
    main()

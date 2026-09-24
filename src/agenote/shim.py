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

策展不在 shim 提供——由 agent 依据 agenote-curator skill 编排原子命令执行。
"""

import argparse
import sys

from agenote.core import agenote_context, ensure_dirs, safe_error_message
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
    args = parser.parse_args()

    ctx = agenote_context()
    ensure_dirs(ctx)

    # cmd_health 不读 args
    ns = argparse.Namespace()
    cmd_health(ns, ctx)


if __name__ == "__main__":
    main()

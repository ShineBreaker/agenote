#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
#
# orgfmt — 通用 org-mode 格式化 CLI（独立于 agenote）
#
# 底层格式化核心在 agenote.orgfmt（由 agenote Stow 包提供），本脚本通过
# sys.path 加载它。可独立处理任意 .org 文件；--strict 启用 agenote 卡片专用规则。
#
# 用法:
#   orgfmt <file> [file...]            格式化（默认直接写盘）
#   orgfmt --check <file> [file...]    只检查不写盘
#   orgfmt --strict <file> [file...]   启用 agenote 卡片规则（MD→Org、fingerprint）
#   find . -name "*.org" | xargs orgfmt --check   批量检查

import argparse
import os
import sys

from agenote.orgfmt import format_file
from agenote.core import safe_error_message


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
        prog="orgfmt",
        description="通用 org-mode 格式化工具（属性对齐、block 大小写、空行、表格、标记间距）",
    )
    parser.add_argument("files", nargs="+", help="目标 .org 文件")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只检查不写盘，打印变更清单",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="启用 agenote 卡片专用规则（Markdown→Org 转换、fingerprint 清理）",
    )
    args = parser.parse_args()

    total_issues = 0
    files_with_issues = 0
    failed_files = 0
    reports: list[tuple[str, list[str]]] = []

    for filepath in args.files:
        if not os.path.isfile(filepath):
            print(f"跳过（非文件）: {filepath}", file=sys.stderr)
            failed_files += 1
            continue
        try:
            changes = format_file(filepath, strict=args.strict, dry_run=args.check)
        except (KeyboardInterrupt, GeneratorExit, SystemExit):
            raise
        except BaseException as exc:
            print(f"错误 {filepath}: 操作失败（{type(exc).__name__}）", file=sys.stderr)
            failed_files += 1
            continue

        if changes:
            total_issues += len(changes)
            files_with_issues += 1
            reports.append((os.path.basename(filepath), changes))

    action = "检查" if args.check else "格式化"
    # 先打印已完成文件的报告与汇总再退出：失败时用户仍需要知道
    # 哪些文件已被实际写盘、改了什么（SystemExit 不会吞掉这些信息）。
    for basename, changes in reports:
        print(f"\n{basename} ({len(changes)} 项):")
        for ch in changes:
            print(ch)
    print(
        f"\n{action}完成: {files_with_issues}/{len(args.files)} 个文件"
        f"有变更, 共 {total_issues} 处"
    )
    if failed_files:
        raise SystemExit(1)

    if args.check and total_issues:
        sys.exit(min(total_issues, 127))


if __name__ == "__main__":
    main()

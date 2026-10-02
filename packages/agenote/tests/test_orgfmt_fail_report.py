# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""orgfmt 失败场景不得吞掉已完成文件的报告（P2：SystemExit 移到打印之后）。"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

import pytest

from agenote.orgfmt import cmd_format


_CARD_NEEDING_FORMAT = (
    "* DONE 标题\n"
    ":PROPERTIES:\n"
    ":ID:       20260925-000000\n"
    ":END:\n"
    "- item\n"  # `- ` → `+ ` 触发实际变更
)


def _args(files: list[str], check: bool = False) -> argparse.Namespace:
    return argparse.Namespace(files=files, check=check)


def test_format_failure_still_reports_processed_files(tmp_path, capsys):
    """混合失败：已格式化写盘的文件报告 + 汇总必须出现在 stdout，退出码 1。"""
    good = tmp_path / "good.org"
    good.write_text(_CARD_NEEDING_FORMAT, encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cmd_format(_args([str(good), str(tmp_path / "missing.org")]))

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "good.org" in out
    assert "`- `" in out and "`+ `" in out  # 变更说明可见
    assert "1/2" in out  # 汇总如实统计
    # 文件确实已被写盘（失败场景下用户需要知道改了什么）
    assert "+ item" in good.read_text(encoding="utf-8")


def test_format_check_mode_reports_then_exits_nonzero(tmp_path, capsys):
    """--check 混合失败：可检查文件的报告先打印，再以非零码退出。"""
    good = tmp_path / "good.org"
    good.write_text(_CARD_NEEDING_FORMAT, encoding="utf-8")
    before = good.read_text(encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        cmd_format(_args([str(good), str(tmp_path / "missing.org")], check=True))

    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "good.org" in out
    assert good.read_text(encoding="utf-8") == before  # check 不写盘


def test_orgfmt_cli_reports_processed_files_on_failure(tmp_path):
    """独立 orgfmt CLI 同构行为：失败时 stdout 仍含已处理文件报告，rc=1。"""
    good = tmp_path / "good.org"
    good.write_text(_CARD_NEEDING_FORMAT, encoding="utf-8")
    env = os.environ.copy()
    env["KB_ROOT"] = str(tmp_path / "kb")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "agenote.orgfmt_cli",
            "--strict",
            str(good),
            str(tmp_path / "missing.org"),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "good.org" in result.stdout
    assert "`+ `" in result.stdout
    assert "missing.org" in result.stderr
    assert "+ item" in good.read_text(encoding="utf-8")

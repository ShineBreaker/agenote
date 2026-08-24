# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""doctor 自诊断测试：检测项结构、三态语义、verification_reason 惯例。"""

from __future__ import annotations

from agenote.doctor import run_checks


def test_checks_cover_three_blocks():
    """外部工具 + 配置 + 两域 KB 结构三块齐全。"""
    names = [c["name"] for c in run_checks()]
    for expected in ("python", "sqlite3", "rg", "git", "xdg-open", "config.toml",
                     "kb[human]", "kb[agenote]"):
        assert expected in names, expected


def test_all_checks_have_status_detail():
    for c in run_checks():
        assert c["status"] in ("ok", "warn", "missing"), c
        assert isinstance(c["detail"], str) and c["detail"], c


def test_path_probed_tools_declare_verification_reason():
    """PATH 探测的项必须声明「未做行为验证」（诚实降级惯例）。"""
    checks = {c["name"]: c for c in run_checks()}
    for tool in ("rg", "git", "xdg-open"):
        assert checks[tool].get("verification_reason"), tool


def test_unknown_config_key_flags_warn(tmp_path, monkeypatch):
    """未知键走 config._unknown_keys 同一口径（校验器复用）。"""
    from agenote import config as config_mod
    from agenote.doctor import _check_config

    # 用一段含未知键的 TOML 驱动 _check_config（不落盘：monkeypatch CONFIG_PATH）
    cfg = tmp_path / "config.toml"
    cfg.write_bytes(b"[search]\nlimit = 5\nnot_a_key = 1\n")
    monkeypatch.setattr(config_mod, "CONFIG_PATH", cfg)
    result = _check_config()
    assert result["status"] == "warn"
    assert "未知" in result["detail"]

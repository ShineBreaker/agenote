# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""C3 doctor 宿主自带记忆检测：六项 × 开/关/缺失/未安装 四态；只读契约。"""

from __future__ import annotations

import json

from agenote.doctor import (
    _check_claude_memory,
    _check_codex_memory,
    _check_dual_channel,
    _check_hermes_memory,
    _check_omp_memory,
    _check_zcode_memory,
    _host_memory_checks,
    run_checks,
)

SECTION = "宿主自带记忆（注入接管前置）"


def _write_json(path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _one(checks):
    assert len(checks) == 1, checks
    return checks[0]


# ── zcode：features.memory / memory.use 任一 false 即合规 ─────────────────────


def test_zcode_installed_and_on_warns(tmp_path):
    p = tmp_path / "config.json"
    _write_json(p, {"features": {"memory": True}, "memory": {"use": True}})
    c = _one(_check_zcode_memory(p))
    assert c["status"] == "warn" and "features.memory=false" in c["affects"]
    assert c["section"] == SECTION


def test_zcode_any_false_is_ok(tmp_path):
    p = tmp_path / "config.json"
    _write_json(p, {"features": {"memory": True}, "memory": {"use": False}})
    assert _check_zcode_memory(p)[0]["status"] == "ok"
    _write_json(p, {"features": {"memory": False}})
    assert _check_zcode_memory(p)[0]["status"] == "ok"


def test_zcode_missing_keys_default_on_warns(tmp_path):
    p = tmp_path / "config.json"
    _write_json(p, {"other": 1})
    assert _check_zcode_memory(p)[0]["status"] == "warn"


def test_zcode_unparseable_is_missing(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("{broken", encoding="utf-8")
    assert _check_zcode_memory(p)[0]["status"] == "missing"


def test_zcode_not_installed_skips(tmp_path):
    assert _check_zcode_memory(tmp_path / "nope" / "config.json") == []


# ── claude：autoMemoryEnabled 缺失视为开；dream 键随附提示 ────────────────────


def test_claude_missing_key_treated_on_warns(tmp_path):
    home = tmp_path / ".claude"
    home.mkdir()
    _write_json(home / "settings.json", {"model": "x"})
    c = _one(_check_claude_memory(home))
    assert c["status"] == "warn" and "缺失视为开" in c["detail"]
    assert "autoDreamEnabled" in c["affects"]


def test_claude_false_ok_and_dream_residue_warns(tmp_path):
    home = tmp_path / ".claude"
    home.mkdir()
    _write_json(home / "settings.json", {"autoMemoryEnabled": False})
    assert _check_claude_memory(home)[0]["status"] == "ok"
    _write_json(home / "settings.json",
                {"autoMemoryEnabled": False, "autoDreamEnabled": True})
    c = _one(_check_claude_memory(home))
    assert c["status"] == "warn" and "autoDreamEnabled" in c["detail"]


def test_claude_dir_without_settings_still_warns(tmp_path):
    home = tmp_path / ".claude"
    home.mkdir()
    assert _check_claude_memory(home)[0]["status"] == "warn"


def test_claude_not_installed_skips(tmp_path):
    assert _check_claude_memory(tmp_path / "nohome") == []


# ── codex：默认关缺失合规；仅显式 true → warn ─────────────────────────────────


def test_codex_explicit_true_warns(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[features]\nmemories = true\n", encoding="utf-8")
    c = _one(_check_codex_memory(p))
    assert c["status"] == "warn" and "memories=false" in c["affects"]


def test_codex_absent_or_false_is_ok(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[model]\nname = \"gpt\"\n", encoding="utf-8")
    assert _check_codex_memory(p)[0]["status"] == "ok"
    p.write_text("[features]\nmemories = false\n", encoding="utf-8")
    assert _check_codex_memory(p)[0]["status"] == "ok"


def test_codex_not_installed_skips(tmp_path):
    assert _check_codex_memory(tmp_path / "nope.toml") == []


# ── omp：memory.backend 非 off 或 autolearn.enabled=true → warn（宽松 YAML）──


def test_omp_on_warns(tmp_path):
    p = tmp_path / "config.yml"
    p.write_text("model: x\nmemory:\n  backend: openai\nautolearn:\n  enabled: true\n",
                 encoding="utf-8")
    assert _check_omp_memory(p)[0]["status"] == "warn"


def test_omp_off_is_ok(tmp_path):
    p = tmp_path / "config.yml"
    p.write_text("memory:\n  backend: off\nautolearn:\n  enabled: false\n",
                 encoding="utf-8")
    c = _one(_check_omp_memory(p))
    assert c["status"] == "ok"


def test_omp_missing_keys_is_ok(tmp_path):
    p = tmp_path / "config.yml"
    p.write_text("model: x\n", encoding="utf-8")
    assert _check_omp_memory(p)[0]["status"] == "ok"


def test_omp_not_installed_skips(tmp_path):
    assert _check_omp_memory(tmp_path / "nope.yml") == []


# ── hermes：memory.memory_enabled / user_profile_enabled 任一 true → warn ─────


def test_hermes_on_warns(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("memory:\n  memory_enabled: true\n  user_profile_enabled: false\n",
                 encoding="utf-8")
    c = _one(_check_hermes_memory(p))
    assert c["status"] == "warn" and "memory_enabled" in c["detail"]


def test_hermes_both_false_ok(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("memory:\n  memory_enabled: false\n  user_profile_enabled: false\n",
                 encoding="utf-8")
    assert _check_hermes_memory(p)[0]["status"] == "ok"


def test_hermes_missing_keys_is_ok(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("gateway:\n  port: 8080\n", encoding="utf-8")
    assert _check_hermes_memory(p)[0]["status"] == "ok"


def test_hermes_not_installed_skips(tmp_path):
    assert _check_hermes_memory(tmp_path / "nope.yaml") == []


# ── 双通道并存 + 汇聚/输出流 ──────────────────────────────────────────────────


def test_dual_channel_warn_when_targets_and_injection_on(monkeypatch):
    monkeypatch.setenv("AGENOTE_ZCODE_DIR", "/tmp/dual-channel-probe")
    monkeypatch.setenv("AGENOTE_INJECTION_ENABLED", "true")
    c = _check_dual_channel()
    assert c["status"] == "warn" and "清空" in c["affects"]


def test_dual_channel_ok_when_injection_off(monkeypatch):
    monkeypatch.setenv("AGENOTE_ZCODE_DIR", "/tmp/dual-channel-probe")
    monkeypatch.setenv("AGENOTE_INJECTION_ENABLED", "false")
    assert _check_dual_channel()["status"] == "ok"
    monkeypatch.delenv("AGENOTE_ZCODE_DIR")
    assert _check_dual_channel()["status"] == "ok"


def test_host_checks_appended_with_section(monkeypatch):
    monkeypatch.delenv("AGENOTE_ZCODE_DIR", raising=False)
    checks = run_checks()
    host = [c for c in checks if c.get("section") == SECTION]
    assert host, "至少双通道检测恒在场"
    names = [c["name"] for c in host]
    assert "dual-channel" in names
    assert all(c["status"] in ("ok", "warn", "missing") and c["detail"] for c in host)
    # 既有检测项未被宿主检测改变措辞/位置（前 8 项为原有块）
    assert [c["name"] for c in checks[:8]] == [
        "python", "sqlite3", "config.toml", "rg", "git", "xdg-open",
        "kb[human]", "kb[agenote]"]


def test_host_memory_checks_dispatcher_never_crashes():
    """真实默认路径上的汇聚调用只产合法三态（本机装了哪些宿主都不炸）。"""
    for c in _host_memory_checks():
        assert c["status"] in ("ok", "warn", "missing") and c["detail"]

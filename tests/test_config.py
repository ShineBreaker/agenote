# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""config 层测试：三层优先级（env > file > default）、嵌套节、模板 roundtrip。"""

from __future__ import annotations

import re

import pytest

from agenote import config


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """隔离配置文件路径与进程内缓存（core 等模块的常量在 import 时已固化，不受影响）。"""
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setattr(config, "CONFIG_PATH", cfg_path)
    monkeypatch.setattr(config, "_file_config", None)
    return cfg_path


def test_default_when_no_file(isolated_config):
    assert config.get("curation", "stale_days") == 30
    assert config.origin("curation", "stale_days") == "default"


def test_file_overrides_default(isolated_config):
    isolated_config.write_text("[curation]\nstale_days = 45\n", encoding="utf-8")
    assert config.get("curation", "stale_days") == 45
    assert config.origin("curation", "stale_days") == "file"


def test_env_overrides_file(isolated_config, monkeypatch):
    isolated_config.write_text("[paths]\nkb_root = '~/FromFile'\n", encoding="utf-8")
    monkeypatch.setenv("KB_ROOT", "/from-env")
    assert config.get("paths", "kb_root") == "/from-env"
    assert config.origin("paths", "kb_root") == "env KB_ROOT"


def test_nested_section_toml(isolated_config):
    """TOML [extract.sources] 嵌套节被 tomllib 解析为子 dict，需逐层下钻取值。"""
    isolated_config.write_text(
        "[extract.sources]\nzcode_db = '/custom/zcode.db'\n\n[extract]\nlimit = 7\n",
        encoding="utf-8",
    )
    assert config.get("extract.sources", "zcode_db") == "/custom/zcode.db"
    assert config.get("extract.sources", "opencode_db")  # 未配置键回落默认
    assert config.get("extract", "limit") == 7  # 同文件平级节不受嵌套影响


def test_unknown_section_and_key_warned(isolated_config, capsys):
    isolated_config.write_text(
        "[nope]\nbad = 1\n\n[curation]\ntypo_key = 2\n", encoding="utf-8"
    )
    config.get("curation", "stale_days")  # 触发 _load_file
    err = capsys.readouterr().err
    assert "未知节 [nope]" in err
    assert "未知键 [curation].typo_key" in err
    # 警告不阻塞：已知键仍正常
    assert config.get("curation", "stale_days") == 30


def test_get_path_expands_user(isolated_config, monkeypatch):
    monkeypatch.setenv("HOME", "/home/tester")
    isolated_config.write_text("[paths]\nkb_root = '~/KB'\n", encoding="utf-8")
    assert config.get_path("paths", "kb_root") == __import__("pathlib").Path(
        "/home/tester/KB"
    )


def test_curated_paths_follows_agenote_dir(isolated_config):
    """commit.curated_paths 的动态默认跟随 paths.agenote_dir 前缀。"""
    isolated_config.write_text('[paths]\nagenote_dir = "myagent"\n', encoding="utf-8")
    paths = config.get("commit", "curated_paths")
    assert "myagent/experiences" in paths
    assert "myagent/.reconcile" in paths
    assert not any(p.startswith("agenote/") for p in paths)
    # 非前缀路径不受影响
    assert "conversations" in paths and "MEMORY.org" in paths


def test_render_template_roundtrip(isolated_config, tmp_path):
    """模板反注释后是合法 TOML，且全部值与 SCHEMA 默认一致。"""
    text = config.render_template()
    # 反注释键定义行（# key = value → key = value），保留说明注释
    key_line = re.compile(r"^# (\w+) = ", re.MULTILINE)
    active = key_line.sub(r"\1 = ", text)

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    data = tomllib.loads(active)

    for section, keys in config.SCHEMA.items():
        node = data
        for part in section.split("."):
            node = node[part]
        for key, spec in keys.items():
            expected = config._resolve_default(section, key)
            assert node[key] == expected, f"{section}.{key}: {node[key]!r} != {expected!r}"


def test_bad_toml_exits(isolated_config, capsys):
    isolated_config.write_text("[curation\nbroken", encoding="utf-8")
    with pytest.raises(SystemExit):
        config.get("curation", "stale_days")
    assert "解析失败" in capsys.readouterr().err


def test_wrong_type_exits_with_key_name(isolated_config, capsys):
    isolated_config.write_text('[curation]\nstale_days = "not-a-number"\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        config.get("curation", "stale_days")
    assert "[curation].stale_days" in capsys.readouterr().err


def test_float_key_accepts_int_value(isolated_config):
    """TOML 的 7 与 7.0 等价：float 键给 int 值应宽容通过。"""
    isolated_config.write_text("[weights]\nhuman_default = 2\n", encoding="utf-8")
    assert config.get("weights", "human_default") == 2


def test_crush_search_roots_is_consumed(isolated_config):
    """crush_search_roots 必须有真实消费者（防止死配置键复燃）。"""
    isolated_config.write_text(
        '[extract.sources]\ncrush_search_roots = ["/scan/here"]\n', encoding="utf-8"
    )
    import importlib

    from agenote.extract import crush

    importlib.reload(crush)  # CRUSH_SEARCH_ROOTS 是模块级常量，重执行以读新配置
    assert crush.CRUSH_SEARCH_ROOTS == ["/scan/here"]


def test_agent_env_whitespace_falls_back(monkeypatch):
    """AGENOTE_AGENT 只空白时回落默认（避免 SOURCE_AGENT 写成空白串）。"""
    monkeypatch.setattr(config, "_file_config", None)
    monkeypatch.setattr(config, "CONFIG_PATH", __import__("pathlib").Path("/nonexistent"))
    monkeypatch.setenv("AGENOTE_AGENT", "   ")
    from agenote.core import default_agent

    assert default_agent() == "omp"

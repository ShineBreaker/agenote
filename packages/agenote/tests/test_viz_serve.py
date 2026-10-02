# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""viz --serve：浏览器打开失败不得杀死已启动的 HTTP 服务器（P2）。

无头 / SSH 环境下 xdg-open 缺失很常见；服务器必须继续运行且 URL 始终可见。
用 FakeServer 替换 TCPServer 避免 fixture 真正监听端口，serve_forever 以
KeyboardInterrupt 模拟 Ctrl-C 收尾。
"""

from __future__ import annotations

from pathlib import Path

import agenote.viz.cli as viz_cli


class _FakeServer:
    """替身 TCPServer：不监听端口，serve_forever 立即模拟 Ctrl-C。"""

    def __init__(self, addr, handler):  # noqa: A002 — 对齐基类签名
        self.addr = addr

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


def test_serve_continues_when_browser_open_fails(tmp_path, monkeypatch, capsys):
    """_open_in_browser 抛错时 serve 流程不中断，URL 仍打印到 stdout。"""
    html = tmp_path / "kb.html"
    html.write_text("<html></html>", encoding="utf-8")

    def broken_open(path, *, fatal=True):
        raise RuntimeError("xdg-open missing")

    monkeypatch.setattr(viz_cli, "_open_in_browser", broken_open)
    monkeypatch.setattr(viz_cli, "SERVE_PROBE_TIMEOUT", 0.05)
    monkeypatch.setattr(viz_cli.socketserver, "TCPServer", _FakeServer)

    # 不抛 SystemExit / RuntimeError 即为通过（旧实现 die → SystemExit）
    viz_cli._serve(html, 0, True, success="已生成")

    captured = capsys.readouterr()
    assert "http://localhost:0/kb.html" in captured.out
    assert "已生成" in captured.out
    assert "浏览器打开失败" in captured.err


def test_serve_skips_open_when_not_requested(tmp_path, monkeypatch, capsys):
    """should_open=False 时不触碰浏览器，URL 照常打印。"""
    html = tmp_path / "kb.html"
    html.write_text("<html></html>", encoding="utf-8")

    def unexpected_open(path, *, fatal=True):
        raise AssertionError("不应尝试打开浏览器")

    monkeypatch.setattr(viz_cli, "_open_in_browser", unexpected_open)
    monkeypatch.setattr(viz_cli, "SERVE_PROBE_TIMEOUT", 0.05)
    monkeypatch.setattr(viz_cli.socketserver, "TCPServer", _FakeServer)

    viz_cli._serve(html, 0, False)

    captured = capsys.readouterr()
    assert "http://localhost:0/kb.html" in captured.out
    assert captured.err == ""

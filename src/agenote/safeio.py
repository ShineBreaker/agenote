# SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
#
# SPDX-License-Identifier: MIT
"""agenote.safeio — 写入安全层：原子写 + KB 进程锁。

移植自 claude-obsidian 的「崩溃后可确定性恢复」哲学（裁剪版）：
- atomic_write：同目录 tmp → fsync → os.replace，写一半崩溃不留半文件
- kb_lock：fcntl.flock 独占锁 + 超时退出，多 agent 并发 CLI 调用互斥

agenote 是单命令即时写入；跨文件读改写由调用方用 restore_text_files() 做补偿，
单文件写入本身由 atomic_write() 保证不留半文件。KB 自身的 git 仓库是最终备份层。
"""

from __future__ import annotations

import contextlib
import fcntl
import itertools
import os
import time
from collections.abc import Iterator, Mapping
from pathlib import Path

LOCK_TIMEOUT_SECONDS = 10.0

# tmp 文件名只由受控部分构成（pid + 进程内计数），不拼接目标文件名
_TMP_COUNTER = itertools.count()


def atomic_write(path: Path, text: str) -> None:
    """原子写入 KB 内数据文件：tmp 落盘 fsync 后 rename 替换目标。

    仅允许写 KB_ROOT 之内（agenote 自管理的卡片/索引/记忆/策展产物）；
    用户显式指定任意输出目标的场景（lint --fix / viz --output /
    extract --output-dir / config init）不走本函数，保留调用方原语。
    """
    atomic_write_bytes(path, text.encode("utf-8"))


def _atomic_replace_bytes(path: Path, data: bytes) -> None:
    """原子替换指定字节；调用方负责决定该路径是否可写。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".tmp-{os.getpid()}-{next(_TMP_COUNTER)}"
    try:
        # tmp 写入后 rename：崩溃窗口最坏丢整次写入（回退旧内容），不会留半文件。
        # 不做 fsync：物理持久性由 KB git 兜底，个人工具不值得为此复杂化。
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except BaseException:
            pass
        raise


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """原子写入原始字节，供 KB 内普通写入保持原始字节不变。"""
    from agenote.core import KB_ROOT  # lazy：core 顶层 import safeio，反向引用需延迟

    path = Path(path)
    resolved = path.resolve()
    if not resolved.is_relative_to(KB_ROOT.resolve()):
        raise ValueError(f"atomic_write 仅允许写 KB_ROOT 内: {path}")
    _atomic_replace_bytes(resolved, data)


def _path_in_kb(path: Path) -> bool:
    from agenote.core import KB_ROOT  # lazy：core 顶层 import safeio，反向引用需延迟

    return Path(path).resolve().is_relative_to(KB_ROOT.resolve())


def _restore_bytes(path: Path, data: bytes, *, allow_outside: bool) -> None:
    """恢复文件；默认受 KB containment，显式授权时才允许原路径。"""
    if _path_in_kb(path):
        atomic_write_bytes(path, data)
    elif allow_outside:
        _atomic_replace_bytes(path, data)
    else:
        atomic_write_bytes(path, data)


def restore_text_files(
    originals: Mapping[Path, str | bytes | None],
    *,
    allow_outside: bool = False,
) -> list[str]:
    """尽力恢复一批文本或原始字节文件；返回失败目标的异常类型。"""
    failures: list[str] = []
    for path, original in originals.items():
        try:
            if not allow_outside and not _path_in_kb(path):
                raise ValueError("恢复路径超出 KB_ROOT")
            if original is None:
                path.unlink(missing_ok=True)
            elif isinstance(original, bytes):
                if not path.exists() or path.read_bytes() != original:
                    _restore_bytes(path, original, allow_outside=allow_outside)
            elif not path.exists() or path.read_text(encoding="utf-8") != original:
                if _path_in_kb(path):
                    atomic_write(path, original)
                elif allow_outside:
                    _atomic_replace_bytes(path, original.encode("utf-8"))
                else:
                    atomic_write(path, original)
        except (KeyboardInterrupt, GeneratorExit, SystemExit):
            raise
        except BaseException as exc:
            failures.append(type(exc).__name__)
    return failures


@contextlib.contextmanager
def kb_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Iterator[None]:
    """进程级互斥锁：fcntl.flock 独占，等待超时抛 TimeoutError 而非死等。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"agenote KB 锁等待超时（{timeout}s）：{lock_path}，"
                        "另一个 agenote 进程可能正持有锁。"
                    )
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)

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
import stat
import time
from collections.abc import Iterator, Mapping
from pathlib import Path

LOCK_TIMEOUT_SECONDS = 10.0


def _lock_timeout_from_config() -> float:
    """读配置 [safeio].lock_timeout_seconds 作为锁超时；非法值兜底回默认。

    SCHEMA 的 _validate_types 只校验文件值（env 值以原始字符串透传，无类型
    防线），故在取值层统一转换：非数值 / <=0 一律回落 LOCK_TIMEOUT_SECONDS，
    保证 kb_lock 永远拿到正数超时。
    """
    # lazy：已验证 config 顶层无 agenote 依赖（不成环），循本模块 KB_ROOT 同款
    # 延迟引用惯例，保持 safeio 的 import 面最小。
    from agenote import config

    try:
        val = float(config.get("safeio", "lock_timeout_seconds"))
    except (TypeError, ValueError):
        return LOCK_TIMEOUT_SECONDS
    return val if val > 0 else LOCK_TIMEOUT_SECONDS

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


def path_in_kb(path: Path) -> bool:
    """判定路径是否在 KB_ROOT 内（KB 内走 atomic_write / KB 外豁免的公共判据）。"""
    from agenote.core import KB_ROOT  # lazy：core 顶层 import safeio，反向引用需延迟

    return Path(path).resolve().is_relative_to(KB_ROOT.resolve())


def _restore_bytes(path: Path, data: bytes, *, allow_outside: bool) -> None:
    """恢复文件；默认受 KB containment，显式授权时才允许原路径。"""
    if path_in_kb(path):
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
            if not allow_outside and not path_in_kb(path):
                raise ValueError("恢复路径超出 KB_ROOT")
            if original is None:
                path.unlink(missing_ok=True)
            elif isinstance(original, bytes):
                if not path.exists() or path.read_bytes() != original:
                    _restore_bytes(path, original, allow_outside=allow_outside)
            elif not path.exists() or path.read_text(encoding="utf-8") != original:
                if path_in_kb(path):
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


class KBLockTimeoutError(TimeoutError):
    """kb_lock 等待超时；消息为代码自生成的可操作提示，错误边界可安全透出。"""


@contextlib.contextmanager
def kb_lock(lock_path: Path, timeout: float | None = None) -> Iterator[None]:
    """进程级互斥锁：fcntl.flock 独占，等待超时抛 KBLockTimeoutError 而非死等。

    timeout 未显式传时运行时读配置 [safeio].lock_timeout_seconds——不可用
    默认参数绑定 LOCK_TIMEOUT_SECONDS（定义时求值会把值焊死在函数对象上，
    env/配置覆盖随之失效）。
    """
    if timeout is None:
        timeout = _lock_timeout_from_config()
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
                    raise KBLockTimeoutError(
                        f"agenote KB 锁等待超时（{timeout}s）：{lock_path}，"
                        "另一个 agenote 进程可能正持有锁。"
                    )
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


class SafeReadError(OSError):
    """受控读取拒绝：symlink / 非普通文件 / 读前后身份变化。OSError 子类，调用方原有 except OSError 继续生效。"""


def safe_read_text(path: Path | str, *, encoding: str = "utf-8", errors: str = "strict") -> str:
    """S8 受控读取：lstat 拒 symlink 与非普通文件，O_NOFOLLOW 打开，读前后 fstat 身份比对。

    供 scan-memories / import / export（外部不可信输入）与 KB 内部读取渐进迁移用。
    """
    path = Path(path)
    try:
        pre = os.lstat(path)
    except FileNotFoundError:
        raise
    if stat.S_ISLNK(pre.st_mode):
        raise SafeReadError(f"拒绝读取 symlink: {path}")
    if not stat.S_ISREG(pre.st_mode):
        raise SafeReadError(f"拒绝读取非普通文件: {path}")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        with os.fdopen(fd, "rb") as f:
            before = os.fstat(f.fileno())
            data = f.read()
            after = os.fstat(f.fileno())
    except OSError:
        raise
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise SafeReadError(f"读取中文件身份变化: {path}")
    return data.decode(encoding, errors)

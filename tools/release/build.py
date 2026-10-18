#!/usr/bin/env python3
"""从 monorepo 构建各组件的发布产物，并校验 tag 与包内版本一致。

一个仓、四条分发渠道（ADR 0005）：PyPI / Emacs package / npm / tarball。
把「每个组件怎么打包」收敛在这一个脚本里，workflow 只负责决定用哪个 tag
前缀触发、以及把产物推到哪个渠道——这样新增组件只改本文件一处。

用法::

    python3 tools/release/build.py --component agenote --tag agenote-v0.2.1
    python3 tools/release/build.py --list

产物统一落在 `dist/<component>/`，workflow 直接 `gh release upload` 即可。
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DIST = REPO_ROOT / "dist"

# tag 前缀 → 包目录。前缀即包名，故 tag 形如 `agenote-v0.2.1`。
COMPONENTS: dict[str, str] = {
    "agenote": "agenote",
    "agenote-el": "agenote-el",
    "dsh-agenote": "dsh-agenote",
    "agenote-pi": "agenote-pi",
    "agenote-zcode": "agenote-zcode",
    "agenote-hermes": "agenote-hermes",
    "agenote-skills": "agenote-skills",
}

# 需要哪些构建工具（缺失时报错而不是静默跳过——发版漏产物比失败更糟）。
TOOLS = {
    "agenote": "uv",
    "dsh-agenote": "npm",
}


def die(msg: str) -> None:
    raise SystemExit(f"✗ {msg}")


# ══════════════════════════════════════════════════════════════
# 版本读取：优先用生态自身的 manifest，其次包内 VERSION
# ══════════════════════════════════════════════════════════════


def read_version(pkg: str) -> str:
    d = REPO_ROOT / "packages" / pkg

    pyproject = d / "pyproject.toml"
    if pyproject.exists():
        m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(), re.M)
        if m:
            return m.group(1)

    for rel, key in (("package.json", "version"), (".zcode-plugin/plugin.json", "version")):
        f = d / rel
        if f.exists():
            return str(json.loads(f.read_text())[key])

    # Emacs：define-package 表单的第三个字符串是版本
    pkg_el = d / "agenote-pkg.el"
    if pkg_el.exists():
        m = re.search(
            r'\(define-package\s+"[^"]+"\s+"[^"]*"\s+"([^"]+)"', pkg_el.read_text()
        )
        if m:
            return m.group(1)

    vfile = d / "VERSION"
    if vfile.exists():
        return vfile.read_text().strip()

    die(f"{pkg} 找不到版本声明：需要 pyproject.toml / package.json / "
        f"plugin.json / agenote-pkg.el / VERSION 之一")


def check_tag(tag: str, pkg: str) -> str:
    """tag 必须形如 `<pkg>-v<version>`，且 version 与包内一致。"""
    prefix = f"{pkg}-v"
    if not tag.startswith(prefix):
        die(f"tag {tag!r} 与组件 {pkg!r} 不符——本仓约定 tag 前缀即包名（{prefix}*）")
    version = tag[len(prefix) :]
    actual = read_version(pkg)
    if version != actual:
        die(
            f"tag {tag} 指向提交的 {pkg} 版本是 {actual}，不一致。"
            f"核对 tag 是否打在改过版本的 release commit 上"
        )
    return version


# ══════════════════════════════════════════════════════════════
# 发布说明：从各包 CHANGELOG 抽对应版本段落
# ══════════════════════════════════════════════════════════════


def release_notes(pkg: str, version: str) -> str:
    ch = REPO_ROOT / "packages" / pkg / "CHANGELOG.md"
    if not ch.exists():
        return f"{pkg} {version}\n\n（本组件无 CHANGELOG——首次发布或尚未记录）"
    text = ch.read_text(encoding="utf-8")
    m = re.search(
        rf"^##\s*\[{re.escape(version)}\][^\n]*\n.*?(?=^##\s*\[|\Z)", text, re.M | re.S
    )
    if not m or not m.group(0).strip():
        die(
            f"packages/{pkg}/CHANGELOG.md 中找不到 ## [{version}] 段落"
            f"——发版流程要求先记日志（Keep a Changelog 格式）"
        )
    return m.group(0).strip() + "\n"


# ══════════════════════════════════════════════════════════════
# 产物构建
# ══════════════════════════════════════════════════════════════


def _clean_dist(pkg: str) -> Path:
    out = DIST / pkg
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    return out


# 构建工具会在产物目录里塞自己的 .gitignore 等杂项；只认这些后缀的才是产物。
ARTIFACT_SUFFIXES = (".whl", ".tar.gz", ".tgz", ".tar")


def _artifacts(out: Path) -> list[Path]:
    return sorted(
        p for p in out.iterdir() if p.is_file() and p.name.endswith(ARTIFACT_SUFFIXES)
    )


def _write_checksums(out: Path, artifacts: list[Path]) -> None:
    """写 SHA256SUMS。

    dotfiles 侧的 `sync-agenote.sh` 靠它校验下载的 tarball——固定 tag 的
    产物必须真的不变，否则锁文件里的校验和会在下次重建时失效。但 GitHub
    Release 资产可以被手动替换，校验和是唯一能把「拉到的就是发出来的那个」
    变成可验证事实的手段，所以打包必须走 `_add_to_tar` 规范化元数据。
    """
    lines = [f"{hashlib.sha256(a.read_bytes()).hexdigest()}  {a.name}" for a in artifacts]
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _deterministic_mtime(tag: str | None) -> int:
    """tar 条目与 gzip 头共用的时间戳。

    取 release tag 指向提交的时间：同一 tag 无论谁在何时构建都得到同一个值，
    而 build.py 的 `--tag` 只是可选参数，退回 HEAD 提交时间同样确定。
    """
    rev = tag or "HEAD"
    if tag is not None and not tag.startswith("refs/"):
        rev = f"refs/tags/{tag}"
    try:
        return int(_git("log", "-1", "--format=%ct", rev))
    except (subprocess.CalledProcessError, ValueError):
        die(f"取不到 {rev} 的提交时间——发版构建必须在 git 仓库内针对真实 tag")


def _add_to_tar(tf: tarfile.TarFile, f: Path, arcname: str, mtime: int) -> None:
    """把 f 以规范化元数据加进 tar。

    uid/gid、属主名、非 ASCII pax 头都随构建环境而变，一个都不能进产物：
    它们只影响 sha256，不影响解包结果，却足以让同一 commit 产出两份不同
    校验和的 tarball。mtime 同理——不是「谁构建的」，是「哪个 commit」。
    """
    ti = tf.gettarinfo(str(f), arcname=arcname)
    ti.mtime = mtime
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if getattr(ti, "pax_headers", None):
        ti.pax_headers = {}
    with f.open("rb") as fh:
        tf.addfile(ti, fh)


def _add_tree_to_tar(tf: tarfile.TarFile, src: Path, arcname: str, mtime: int) -> None:
    """递归加目录，目录自身也用同一个 mtime。"""
    ti = tf.gettarinfo(str(src), arcname=arcname)
    ti.mtime = mtime
    ti.uid = ti.gid = 0
    ti.uname = ti.gname = ""
    if getattr(ti, "pax_headers", None):
        ti.pax_headers = {}
    tf.addfile(ti)
    for child in sorted(src.iterdir()):
        sub = f"{arcname}/{child.name}"
        if child.is_dir():
            _add_tree_to_tar(tf, child, sub, mtime)
        elif child.is_file():
            _add_to_tar(tf, child, sub, mtime)


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def build_agenote(pkg: str, version: str, out: Path, mtime: int) -> list[Path]:
    """PyPI：wheel + sdist。由 uv build 产出，直接可 twine upload。"""
    _need("agenote", "uv")
    src = REPO_ROOT / "packages" / "agenote"
    _run(["uv", "build", "--out-dir", str(out)], cwd=src)
    return _artifacts(out)


def build_agenote_el(pkg: str, version: str, out: Path, mtime: int) -> list[Path]:
    """Emacs package（ELPA 格式 tar）。

    ELPA 要求包内所有文件带 `;; Version:` 头，且有 `<name>-pkg.el` 声明
    包。tar 顶层目录名必须是 `<name>-<version>`。
    """
    src = REPO_ROOT / "packages" / "agenote-el"
    els = sorted(src.glob("*.el"))
    if not any(e.name == "agenote-pkg.el" for e in els):
        die("agenote-el 缺 agenote-pkg.el——ELPA 打包需要包声明文件")
    for e in els:
        if ";; Version:" not in e.read_text():
            die(f"agenote-el/{e.name} 缺 `;; Version:` 头，ELPA 会拒绝打包")

    top = f"agenote-{version}"
    archive = out / f"{top}.tar"
    # ELPA 惯例：tar 不压缩、不带目录条目。
    with tarfile.open(archive, "w") as tf:
        for e in els:
            _add_to_tar(tf, e, arcname=f"{top}/{e.name}", mtime=mtime)
        for extra in ("README.md", "README.zh.md", "LICENSE"):
            f = src / extra
            if f.exists():
                _add_to_tar(tf, f, arcname=f"{top}/{extra}", mtime=mtime)
    return [archive]


def build_dsh_agenote(pkg: str, version: str, out: Path, mtime: int) -> list[Path]:
    """npm：`npm pack` 产出 tgz，字段已由 package.json 的 files 白名单收敛。"""
    _need("dsh-agenote", "npm")
    src = REPO_ROOT / "packages" / "dsh-agenote"
    res = subprocess.run(
        ["npm", "pack", "--pack-destination", str(out)],
        cwd=src, check=True, capture_output=True, text=True,
    )
    tgz = [l for l in res.stdout.splitlines() if l.strip().endswith(".tgz")]
    if not tgz:
        die(f"npm pack 未产出 tgz：{res.stdout}\n{res.stderr}")
    return [out / tgz[-1].strip()]


def build_tarball(pkg: str, version: str, out: Path, mtime: int) -> list[Path]:
    """通用 tar.gz：pi / zcode / hermes / skills。

    这四个组件由宿主直接加载目录（omp 自动扫描、ZCode 插件目录、hermes
    插件目录、skills 目录），没有包管理器的安装概念，故用 tarball 分发。
    """
    src = REPO_ROOT / "packages" / pkg
    if not src.is_dir():
        die(f"packages/{pkg} 不存在")
    # 只打 git 跟踪的内容：免得把 __pycache__ / .mimosa 之类的产物打进去。
    tracked = subprocess.run(
        ["git", "ls-files", f"packages/{pkg}"],
        cwd=REPO_ROOT, check=True, capture_output=True, text=True,
    ).stdout.split()
    if not tracked:
        die(f"packages/{pkg} 下没有 git 跟踪的文件")
    archive = out / f"{pkg}-{version}.tar.gz"
    # tar 与 gzip 两层都自己写：tarfile 的 "w:gz" 会把当前时间塞进 gzip 头，
    # 只走 tarfile.SOURCE_DATE_EPOCH 这个模块级变量还受导入时机影响。
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for rel in tracked:
            rel_inside = rel.split("/", 2)[2]
            f = REPO_ROOT / rel
            arcname = f"{pkg}/{rel_inside}"
            # 目录条目随其后的文件隐式重建，包内不留空目录。
            if f.is_dir():
                continue
            _add_to_tar(tf, f, arcname=arcname, mtime=mtime)
    with archive.open("wb") as fh:
        with gzip.GzipFile(filename="", fileobj=fh, mode="wb", mtime=0) as gz:
            gz.write(raw.getvalue())
    return [archive]


BUILDERS = {
    "agenote": build_agenote,
    "agenote-el": build_agenote_el,
    "dsh-agenote": build_dsh_agenote,
    "agenote-pi": build_tarball,
    "agenote-zcode": build_tarball,
    "agenote-hermes": build_tarball,
    "agenote-skills": build_tarball,
}


def _need(pkg: str, tool: str) -> None:
    if shutil.which(tool) is None:
        die(f"{pkg} 的构建需要 {tool}，当前环境没有")


# ══════════════════════════════════════════════════════════════


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--component", choices=sorted(COMPONENTS))
    ap.add_argument("--tag", help="形如 <component>-v<version>；给定时校验与包内版本一致")
    ap.add_argument("--list", action="store_true", help="列出全部组件及其版本")
    ap.add_argument("--notes-only", action="store_true", help="只产出 release_notes.md")
    args = ap.parse_args()

    if args.list:
        for pkg in sorted(COMPONENTS):
            print(f"{pkg:18s} {read_version(pkg)}")
        return 0

    if not args.component:
        ap.error("需要 --component 或 --list")

    pkg = args.component
    version = read_version(pkg)
    if args.tag:
        version = check_tag(args.tag, pkg)

    if args.notes_only:
        out = _clean_dist(pkg)
        (out / "release_notes.md").write_text(release_notes(pkg, version), encoding="utf-8")
        print(f"✓ {pkg} {version} 发布说明已写出")
        return 0

    out = _clean_dist(pkg)
    artifacts = BUILDERS[pkg](pkg, version, out, _deterministic_mtime(args.tag))
    _write_checksums(out, artifacts)
    (out / "release_notes.md").write_text(release_notes(pkg, version), encoding="utf-8")
    print(f"✓ {pkg} {version} 产物：")
    for a in artifacts:
        print(f"    {a.relative_to(REPO_ROOT)}  ({a.stat().st_size} bytes)")
    print(f"    dist/{pkg}/SHA256SUMS  （供 dotfiles 侧拉取脚本校验）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

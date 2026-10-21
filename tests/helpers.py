"""Testhilfen: treiben die C-Binaries syp und syp-convert."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "csrc/bin"
SYP = str(BIN / "syp")
CONVERT = str(BIN / "syp-convert")


def run(*args, check: bool = True, input: str | None = None, env: dict | None = None) -> subprocess.CompletedProcess:
    r = subprocess.run([str(a) for a in args], capture_output=True, text=True, input=input, env=env)
    if check and r.returncode != 0:
        raise AssertionError(f"{args} -> {r.returncode}\nstdout:\n{r.stdout}\nstderr:\n{r.stderr}")
    return r


def syp(*args, **kw) -> subprocess.CompletedProcess:
    return run(SYP, *args, **kw)


def keygen(tmp: Path, name: str = "test") -> tuple[Path, Path]:
    syp("keygen", tmp / name, "--comment", f"{name} key")
    return tmp / f"{name}.key", tmp / f"{name}.pub"


def make_pkg(tmp: Path, name: str, version: str, files: dict[str, str] | None = None,
             key: Path | None = None, out: Path | None = None, **info) -> Path:
    tree = tmp / f"tree-{name}-{version}"
    tree.mkdir(parents=True, exist_ok=True)
    for rel, content in (files or {f"usr/share/{name}/VERSION": version}).items():
        p = tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    info = {"name": name, "version": version, "arch": "x86_64", "summary": f"{name} test", **info}
    ij = tmp / f"info-{name}-{version}.json"
    ij.write_text(json.dumps(info))
    out = out or tmp / "out"
    args = ["pack", tree, ij, "-o", out] + (["-k", key] if key else [])
    return Path(syp(*args).stdout.strip())


def setup_root(tmp: Path, repo_dir: Path, pub: Path) -> Path:
    root = tmp / "root"
    (root / "etc/syp/keys").mkdir(parents=True, exist_ok=True)
    (root / "etc/syp/keys/test.pub").write_text(pub.read_text())
    (root / "etc/syp/syp.toml").write_text(
        f'arch = "x86_64"\n[[repo]]\nname = "core"\nurl = "file://{repo_dir}"\n'
        f'[snapshots]\nenabled = false\n')
    return root


def publish(repo_dir: Path, key: Path, *pkgs: Path) -> None:
    repo_dir.mkdir(parents=True, exist_ok=True)
    syp("repo-add", repo_dir, *pkgs, "--name", "core", "-k", key)

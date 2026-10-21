"""`syp build`: baut ein Paket aus einem SYPBUILD.toml-Rezept.

    [package]
    name = "hello"
    version = "1.0-1"
    arch = "x86_64"          # oder "any"
    summary = "..."
    license = "MIT"
    depends = ["glibc"]

    [[source]]
    url = "https://example.org/hello-1.0.tar.gz"
    sha256 = "..."           # Pflicht bei URLs
    [[source]]
    path = "files/"          # relativ zum Rezept (optional: dest = "name")

    [build]
    prepare = "..."          # bash, cwd=$SRCDIR
    build   = "..."
    check   = "..."
    package = "install -Dm755 hello \\"$DESTDIR/usr/bin/hello\\""

    [scripts]
    post_install = "..."
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import tomllib
import urllib.request
from pathlib import Path

from .crypto import SecretKey
from .package import PackageError, PackageInfo, build_package


def build(recipe: Path, out_dir: Path, signer: SecretKey | None, log=print,
          run_check: bool = True) -> Path:
    recipe = recipe.resolve()
    rdir = recipe.parent
    spec = tomllib.loads(recipe.read_text())
    info = PackageInfo.from_dict(dict(spec["package"], scripts=spec.get("scripts", {}),
                                      origin={"format": "native", "recipe": recipe.name}))
    info.validate()
    with tempfile.TemporaryDirectory(prefix=f"syp-build-{info.name}-") as tmp:
        src, dest = Path(tmp) / "src", Path(tmp) / "pkg"
        src.mkdir()
        dest.mkdir()
        for s in spec.get("source", []):
            if "url" in s:
                if "sha256" not in s:
                    raise PackageError(f"Quelle ohne sha256: {s['url']}")
                target = src / s.get("filename", s["url"].rsplit("/", 1)[-1])
                log(f"  Lade {s['url']}")
                with urllib.request.urlopen(s["url"], timeout=120) as r, open(target, "wb") as f:
                    shutil.copyfileobj(r, f)
                if hashlib.sha256(target.read_bytes()).hexdigest() != s["sha256"]:
                    raise PackageError(f"sha256 falsch: {s['url']}")
                if s.get("extract", True) and shutil.which("bsdtar") and \
                        target.suffix in (".gz", ".xz", ".zst", ".bz2", ".tgz", ".zip"):
                    subprocess.run(["bsdtar", "-xf", target.name], cwd=src, check=True)
            elif "path" in s:
                p = (rdir / s["path"]).resolve()
                if not p.is_relative_to(rdir):
                    raise PackageError(f"Quellpfad außerhalb des Rezepts: {s['path']}")
                dest_name = s.get("dest", p.name)
                if p.is_dir():
                    shutil.copytree(p, src / dest_name, symlinks=True, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns(".git", "out", "work", "__pycache__",
                                                                  "*.syp", ".pytest_cache"))
                else:
                    shutil.copy2(p, src / dest_name)
        env = dict(os.environ, SRCDIR=str(src), DESTDIR=str(dest), RECIPEDIR=str(rdir),
                   PKGNAME=info.name, PKGVER=info.version.split("-")[0],
                   SOURCE_DATE_EPOCH=os.environ.get("SOURCE_DATE_EPOCH", "1700000000"))
        steps = ["prepare", "build"] + (["check"] if run_check else []) + ["package"]
        for step in steps:
            script = spec.get("build", {}).get(step)
            if not script:
                continue
            log(f"==> {step}()")
            r = subprocess.run(["bash", "-euo", "pipefail", "-c", script], cwd=src, env=env)
            if r.returncode:
                raise PackageError(f"{step}() fehlgeschlagen ({r.returncode})")
        os.environ.setdefault("SOURCE_DATE_EPOCH", env["SOURCE_DATE_EPOCH"])
        return build_package(dest, info, out_dir, signer)

from __future__ import annotations

from pathlib import Path

from syp.crypto import SecretKey
from syp.package import PackageInfo, build_package


def make_pkg(tmp: Path, name: str, version: str, files: dict[str, str] | None = None,
             signer: SecretKey | None = None, out: Path | None = None, **kw) -> Path:
    tree = tmp / f"tree-{name}-{version}"
    tree.mkdir(parents=True, exist_ok=True)
    for rel, content in (files or {f"usr/share/{name}/VERSION": version}).items():
        p = tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    info = PackageInfo(name=name, version=version, arch="x86_64", summary=f"{name} test", **kw)
    return build_package(tree, info, out or tmp / "out", signer)


def setup_root(tmp: Path, repo_dir: Path, key: SecretKey) -> Path:
    root = tmp / "root"
    (root / "etc/syp/keys").mkdir(parents=True)
    key.save(tmp / "test.key", root / "etc/syp/keys/test.pub")
    (root / "etc/syp/syp.toml").write_text(
        f'arch = "x86_64"\n[[repo]]\nname = "core"\nurl = "file://{repo_dir}"\n'
        f'[snapshots]\nenabled = false\n')
    return root

import os
import pathlib
import subprocess
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))
ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def build_c_binaries():
    """Baut syp und syp-convert einmal pro Testlauf."""
    if os.environ.get("SYP_SKIP_BUILD"):
        return
    r = subprocess.run(["make", "-C", str(ROOT / "csrc"), "-j4"], capture_output=True, text=True)
    if r.returncode:
        pytest.exit(f"C-Build fehlgeschlagen:\n{r.stdout}\n{r.stderr}", returncode=2)

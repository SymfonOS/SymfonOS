"""Bootstrap-Stufen: Schritte werden nur nach Erfolg als erledigt markiert (fortsetzbarer CI-Bau)."""
import subprocess
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "distro/bootstrap/config.sh"


def bash(script, sym):
    return subprocess.run(["bash", "-c", f'. "{CONFIG}"\n{script}'], capture_output=True, text=True,
                          env={"SYM": str(sym), "PATH": "/usr/bin:/bin"})


def test_failed_step_is_not_marked(tmp_path):
    r = bash('f() { false; echo weiter; }\nstep kaputt "Test" f\necho NACH', tmp_path)
    assert r.returncode != 0
    assert "weiter" not in r.stdout and "NACH" not in r.stdout     # set -e greift im Schritt und danach
    assert not (tmp_path / ".stamps/kaputt").exists()


def test_failed_make_in_and_list_style(tmp_path):
    # Früheres Muster "make && make install; mark x" hätte hier markiert
    r = bash('f() { false; true; }\nstep x "Test" f', tmp_path)
    assert r.returncode != 0 and not (tmp_path / ".stamps/x").exists()


def test_step_runs_once_and_isolates_cwd(tmp_path):
    (tmp_path / "w").mkdir()
    script = f'f() {{ cd "{tmp_path}/w"; echo lauf >> "{tmp_path}/log"; }}\nstep ok "Test" f\nstep ok "Test" f\npwd'
    r = bash(script, tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "log").read_text() == "lauf\n"
    assert (tmp_path / ".stamps/ok").exists()
    assert not r.stdout.strip().endswith("/w")                      # Verzeichniswechsel bleibt im Schritt

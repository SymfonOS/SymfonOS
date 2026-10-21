"""Sicherheitsschicht: Krypto-Richtlinien, Firewall (auch zusammen mit dem Prüfungsmodus),
Secure-Boot-Schlüssel und Installation des Pakets symfonos-security."""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEC = ROOT / "distro/security"
UCP = SEC / "bin/update-crypto-policies"
SECTOOL = SEC / "bin/symfonos-security"
SB = SEC / "bin/symfonos-secureboot"
EXAM = ROOT / "exam/symfonos-exam"


def run(*cmd, env=None, check=True):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=check,
                          env=dict(os.environ, **(env or {})))


@pytest.fixture
def cp_root(tmp_path):
    share = tmp_path / "usr/share/crypto-policies"
    for p in ("DEFAULT", "FUTURE", "LEGACY"):
        shutil.copytree(SEC / "crypto" / p, share / p)
    return tmp_path


def test_crypto_policies_switch(cp_root):
    env = {"CRYPTO_POLICIES_ROOT": str(cp_root)}
    run(UCP, env=env)
    assert run(UCP, "--show", env=env).stdout.strip() == "DEFAULT"
    assert run(UCP, "--check", env=env).returncode == 0
    run(UCP, "--set", "future", env=env)
    assert run(UCP, "--show", env=env).stdout.strip() == "FUTURE"
    link = cp_root / "etc/crypto-policies/back-ends/opensslcnf.config"
    assert os.readlink(link) == "../../../usr/share/crypto-policies/FUTURE/opensslcnf.config"
    assert "SECLEVEL=3" in link.read_text()
    assert run(UCP, "--set", "WEAK", env=env, check=False).returncode == 1
    assert run(UCP, "--show", env=env).stdout.strip() == "FUTURE"    # unverändert


def test_crypto_policy_content():
    for p in ("DEFAULT", "FUTURE", "LEGACY"):
        o = (SEC / "crypto" / p / "opensslcnf.config").read_text()
        assert "MinProtocol = TLSv1.2" in o                          # TLS 1.0/1.1 in keiner Richtlinie
        assert "!RC4" in o and "!MD5" in o and "!aNULL" in o
        for f in ("openssh.config", "openssh-server.config"):
            s = (SEC / "crypto" / p / f).read_text()
            assert "mlkem768x25519-sha256" in s                      # Post-Quanten-Schlüsseltausch zuerst
            assert "cbc" not in s or p == "LEGACY"
            assert "ssh-rsa," not in s or p == "LEGACY"              # SHA-1-Signaturen nur in LEGACY
    fut = (SEC / "crypto/FUTURE/openssh-server.config").read_text()
    assert "RequiredRSASize 3072" in fut and "nistp256" not in fut.split("KexAlgorithms")[1].split("\n")[0]


def test_openssl_parses_all_policies(cp_root):
    if shutil.which("openssl") is None:
        pytest.skip("openssl fehlt")
    env = {"CRYPTO_POLICIES_ROOT": str(cp_root)}
    cnf = cp_root / "openssl.cnf"
    cnf.write_text((SEC / "crypto/openssl.cnf").read_text().replace("/etc/crypto-policies", f"{cp_root}/etc/crypto-policies"))
    for p in ("DEFAULT", "FUTURE", "LEGACY"):
        run(UCP, "--set", p, env=env)
        r = run("openssl", "ciphers", "-s", env={"OPENSSL_CONF": str(cnf)}, check=False)
        assert r.returncode == 0, r.stderr
        assert "TLS_AES_256_GCM_SHA384" in r.stdout


def test_firewall_rules(tmp_path):
    env = {"SYMFONOS_ROOT": str(tmp_path)}
    run(SECTOOL, "firewall", "allow", "22/tcp", "SSH", env=env)
    run(SECTOOL, "firewall", "allow", "6000-6010/udp", env=env)
    assert run(SECTOOL, "firewall", "allow", "70000/tcp", env=env, check=False).returncode == 1
    assert run(SECTOOL, "firewall", "allow", "22/icmp", env=env, check=False).returncode == 1
    assert run(SECTOOL, "firewall", "allow", 'x"; flush ruleset; #', env=env, check=False).returncode == 1
    rules = sorted(p.name for p in (tmp_path / "etc/nftables.d").iterdir())
    assert rules == ["tcp-22.nft", "udp-6000-6010.nft"]
    assert 'tcp dport 22 accept comment "SSH"' in (tmp_path / "etc/nftables.d/tcp-22.nft").read_text()
    run(SECTOOL, "firewall", "deny", "22/tcp", env=env)
    assert not (tmp_path / "etc/nftables.d/tcp-22.nft").exists()


def _nft_check(text, tmp_path):
    if shutil.which("nft") is None:
        pytest.skip("nft nicht installiert")
    f = tmp_path / "check.nft"
    f.write_text(text)
    r = subprocess.run(["nft", "-c", "-f", str(f)], capture_output=True, text=True)
    if r.returncode and ("Operation not permitted" in r.stderr or "cache initialization" in r.stderr):
        pytest.skip("nft -c braucht CAP_NET_ADMIN")
    return r


def test_base_firewall_coexists_with_exam_mode(tmp_path):
    """Basis-Firewall und Prüfungsmodus-Allowlist müssen gemeinsam ladbar sein, und ein Neuladen
    der Basis darf die Prüfungstabelle nicht löschen (kein "flush ruleset")."""
    conf = (SEC / "nftables.conf").read_text()
    assert "flush ruleset" not in [ln.strip() for ln in conf.splitlines()]
    assert "delete table inet symfonos\n" in conf
    d = tmp_path / "d"
    d.mkdir()
    (d / "tcp-22.nft").write_text('tcp dport 22 accept comment "SSH"\n')
    base = conf.replace("/etc/nftables.d", str(d))
    out = run(EXAM, "render", "--url", "https://127.0.0.1/quiz", "--allow", "127.0.0.2").stdout
    exam = out[out.index("table inet"):out.index("# Firefox")]
    r = _nft_check(base + "\n" + exam + "\n" + base, tmp_path)   # Basis, Prüfung, Basis erneut laden
    assert r.returncode == 0, r.stderr
    unit = (SEC / "systemd/nftables.service").read_text()
    assert "flush ruleset" not in unit


def test_exam_mode_not_broken_by_hardening():
    """Härtung darf den Prüfungsmodus nicht aushebeln oder blockieren."""
    blk = (SEC / "modprobe.d/50-symfonos-blacklist.conf").read_text().split()
    for mod in ("kvm", "kvm_intel", "kvm_amd", "fuse", "nf_tables", "usb_storage"):
        assert mod not in blk                                         # VM-Option, AppImage (seb-linux), Firewall
    sysctl = (SEC / "sysctl.d/50-symfonos-hardening.conf").read_text()
    assert "user.max_user_namespaces = 0" not in sysctl               # Browser-Sandboxen brauchen Namespaces
    assert "kernel.unprivileged_userns_clone = 0" not in sysctl
    kcfg = (ROOT / "distro/recipes/linux/symfonos.config").read_text()
    for opt in ("CONFIG_KVM=m", "CONFIG_FUSE_FS=m", "CONFIG_NF_TABLES=m", "CONFIG_USB_STORAGE=m",
                "CONFIG_SECURITY_SELINUX=y", "CONFIG_MODULE_SIG_FORCE=y"):
        assert opt in kcfg


def test_secureboot_auth_files(tmp_path):
    if shutil.which("openssl") is None:
        pytest.skip("openssl fehlt")
    (tmp_path / "boot/efi/EFI/BOOT").mkdir(parents=True)
    env = {"SYMFONOS_ROOT": str(tmp_path)}
    run("python3", SB, "init", "--no-microsoft", env=env)
    kd = tmp_path / "var/lib/symfonos/secureboot"
    assert oct(kd.stat().st_mode & 0o777) == "0o700"
    assert oct((kd / "db.key").stat().st_mode & 0o777) == "0o600"
    assert (tmp_path / "boot/efi/loader/keys/symfonos/PK.auth").exists()
    guids = {"PK": "8be4df61-93ca-11d2-aa0d-00e098032b8c", "KEK": "8be4df61-93ca-11d2-aa0d-00e098032b8c",
             "db": "d719b2cb-3d3a-4596-a3bc-dad00e67656f"}
    signer = {"PK": "PK", "KEK": "PK", "db": "KEK"}
    for var, g in guids.items():
        a = (kd / f"{var}.auth").read_bytes()
        ts = a[:16]
        ln, rev, typ = struct.unpack_from("<IHH", a, 16)
        assert (rev, typ) == (0x0200, 0x0EF1)
        p7, data = a[40:16 + ln], a[16 + ln:]
        assert data == (kd / f"{var}.esl").read_bytes()
        assert data[:16] == uuid.UUID("a5c059a1-94e4-4aa7-87b5-ab155c2bf072").bytes_le
        signed = var.encode("utf-16-le") + uuid.UUID(g).bytes_le + struct.pack("<I", 0x27) + ts + data
        body = bytes.fromhex("06092a864886f70d010702") + b"\xa0\x82" + len(p7).to_bytes(2, "big") + p7
        (tmp_path / "p7").write_bytes(b"\x30\x82" + len(body).to_bytes(2, "big") + body)
        (tmp_path / "signed").write_bytes(signed)
        r = run("openssl", "smime", "-verify", "-binary", "-inform", "DER", "-in", tmp_path / "p7",
                "-content", tmp_path / "signed", "-CAfile", kd / f"{signer[var]}.crt", "-noverify",
                "-out", os.devnull, check=False)
        assert "Verification successful" in r.stderr, (var, r.stderr)
    assert run("python3", SB, "init", env=env, check=False).returncode == 1   # nicht versehentlich überschreiben


@pytest.mark.skipif(os.environ.get("SYMFONOS_NET_TESTS") != "1", reason="Netztest (SYMFONOS_NET_TESTS=1)")
def test_secureboot_microsoft_certs(tmp_path):
    (tmp_path / "boot/efi/EFI").mkdir(parents=True)
    run("python3", SB, "init", env={"SYMFONOS_ROOT": str(tmp_path)})
    esl = (tmp_path / "var/lib/symfonos/secureboot/db.esl").read_bytes()
    assert esl.count(uuid.UUID("77fa9abd-0359-4d32-bd60-28f4e78f784b").bytes_le) == 5


def test_install_layout(tmp_path):
    run(SEC / "install.sh", tmp_path)
    for f in ("etc/pam.d/system-auth", "etc/pam.d/sudo", "etc/security/pwquality.conf.d/50-symfonos.conf",
              "etc/security/faillock.conf", "usr/lib/sysctl.d/50-symfonos-hardening.conf",
              "usr/lib/modprobe.d/50-symfonos-blacklist.conf", "etc/nftables.conf",
              "usr/lib/systemd/system/nftables.service", "etc/audit/rules.d/50-symfonos.rules",
              "etc/ssh/sshd_config.d/50-symfonos.conf", "etc/sudoers.d/00-symfonos",
              "usr/lib/systemd/system-preset/50-symfonos-security.preset",
              "usr/lib/systemd/system/symfonos-security-check.timer",
              "usr/lib/systemd/system/syp-security-update.timer",
              "usr/bin/symfonos-security", "usr/bin/symfonos-secureboot"):
        assert (tmp_path / f).exists(), f
    assert oct((tmp_path / "etc/sudoers.d/00-symfonos").stat().st_mode & 0o777) == "0o440"
    assert oct((tmp_path / "etc/audit/rules.d/50-symfonos.rules").stat().st_mode & 0o777) == "0o640"
    pam = (tmp_path / "etc/pam.d/system-auth").read_text()
    assert "pam_faillock.so preauth" in pam and "pam_pwquality.so" in pam and "yescrypt" in pam
    assert "nullok" not in pam
    preset = (tmp_path / "usr/lib/systemd/system-preset/50-symfonos-security.preset").read_text()
    assert "disable sshd.service" in preset and "enable nftables.service" in preset
    if shutil.which("visudo"):
        r = run("visudo", "-cf", tmp_path / "etc/sudoers.d/00-symfonos", check=False)
        assert r.returncode == 0, r.stdout + r.stderr


def test_audit_rules_immutable_last():
    rules = [ln for ln in (SEC / "audit/50-symfonos.rules").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    assert rules[-1] == "-e 2"                                       # Regeln bis zum Neustart gesperrt

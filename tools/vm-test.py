#!/usr/bin/env python3
"""VM-Test wie auf echter Hardware: Start über die Firmware, Installation im laufenden Live-System, Neustart von der Platte.

Die übrigen Tests (smoke-test.sh, install-test.sh, desktop-test.sh) starten den Kernel direkt (qemu -kernel) und
installieren aus einem chroot auf dem CI-Rechner. Dieser Test bedient die VM dagegen wie ein Mensch:

  1. ISO über die Firmware starten (SeaBIOS oder UEFI/OVMF): El-Torito-Start, GRUB vom Medium, Kernel, Initramfs.
     Im GRUB-Menü wird der erste Eintrag mit »e« bearbeitet und console=ttyS0 angehängt, damit der Test mitlesen kann.
  2. An der seriellen Konsole als »live« anmelden, sudo symfonos-install auf die leere Platte (/dev/vda);
     bei Verschlüsselung die LUKS-Passphrase an den Eingabeaufforderungen von cryptsetup eintippen.
  3. Ohne ISO neu starten: Firmware → installierter Bootloader (GRUB bzw. systemd-boot mit UKI) → Initramfs
     (Passphrase eintippen) → Anmeldeprompt; als Benutzer anmelden und den Systemzustand prüfen.

    tools/vm-test.py ISO --firmware bios|uefi --boot grub|systemd-boot [--encrypt] [--out DIR]

Die serielle Konsole des installierten Systems: bei GRUB wird console=ttyS0 vor dem Neustart in grub.cfg
eingetragen (Testvorbereitung, wie ein Admin eine serielle Konsole einrichtet); beim UKI (systemd-stub) kommt sie
ohne Änderung am System über SMBIOS Typ 11 (io.systemd.stub.kernel-cmdline-extra, nur ohne Secure Boot).
Rückgabewert 0 = bestanden. Bei einem Fehler liegen serielles Protokoll und Bildschirmfotos im Ausgabeordner.
"""
import argparse
import os
import re
import shutil
import socket
import subprocess
import sys
import time

USER_PW = "Blauer-Wal-7-Segelt"
LUKS_PW = "Gruene-Robbe-4-Taucht"
OVMF_CODE = ["/usr/share/OVMF/OVMF_CODE_4M.fd", "/usr/share/OVMF/OVMF_CODE.fd"]
OVMF_VARS = ["/usr/share/OVMF/OVMF_VARS_4M.fd", "/usr/share/OVMF/OVMF_VARS.fd"]
ANSI = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\r")
# QEMU-sendkey-Namen für Zeichen, die keine Buchstaben/Ziffern sind (US-Belegung, wie GRUB sie liest)
KEYS = {" ": "spc", "=": "equal", ",": "comma", ".": "dot", "/": "slash", "-": "minus", "_": "shift-minus",
        ":": "shift-semicolon", "@": "shift-2"}


class Fail(Exception):
    pass


def log(msg):
    print(f"\033[1;35m::\033[0m {msg}", flush=True)


class Port:
    """Serielle Schnittstelle der VM (QEMU-Socket) mit eigenem Puffer und Protokoll."""
    def __init__(self, path, log_path):
        self.path, self.sock, self.buf, self.pos = path, None, b"", 0
        self.raw = open(log_path, "wb")

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(self.path)
        self.sock.setblocking(False)


class VM:
    def __init__(self, out, name, args, timeout_scale, ports=2):
        self.out, self.name, self.scale = out, name, timeout_scale
        self.mon_path = os.path.join(out, f"{name}.mon.sock")
        # ttyS0: Kernel-Konsole; ttyS1: Anmeldung wie an einem Terminal (getrennt von Kernel-Meldungen)
        self.ports = [Port(os.path.join(out, f"{name}.ser{i}.sock"),
                           os.path.join(out, f"{name}-serial{'' if i == 0 else i}.log")) for i in range(ports)]
        for p in [self.mon_path] + [p.path for p in self.ports]:
            if os.path.exists(p):
                os.unlink(p)
        chars = []
        for i, p in enumerate(self.ports):
            chars += ["-chardev", f"socket,id=ser{i},path={p.path},server=on,wait=off", "-serial", f"chardev:ser{i}"]
        cmd = ["qemu-system-x86_64", *args, *chars,
               "-monitor", f"unix:{self.mon_path},server=on,wait=off",
               "-display", "none", "-no-reboot"]
        log(f"{name}: {' '.join(cmd)}")
        self.proc = subprocess.Popen(cmd, stdout=open(os.path.join(out, f"{name}-qemu.log"), "w"),
                                     stderr=subprocess.STDOUT)
        for _ in range(100):
            if all(os.path.exists(p) for p in [self.mon_path] + [p.path for p in self.ports]):
                break
            time.sleep(0.1)
        for p in self.ports:
            p.connect()
        self.cur = self.ports[0]
        self.shots = 0

    def use(self, i):
        """Folgende expect/send/run gelten der seriellen Schnittstelle i (0 = ttyS0, 1 = ttyS1)."""
        self.cur = self.ports[i]

    @property
    def buf(self):
        return self.cur.buf

    @property
    def pos(self):
        return self.cur.pos

    @pos.setter
    def pos(self, v):
        self.cur.pos = v

    # --- serielle Konsole: alle Schnittstellen leeren, damit keine den Gast blockiert ---
    def pump(self):
        for p in self.ports:
            while True:
                try:
                    d = p.sock.recv(65536)
                except BlockingIOError:
                    break
                if not d:
                    break
                p.raw.write(d)
                p.raw.flush()
                p.buf += ANSI.sub(b"", d)

    def alive(self):
        return self.proc.poll() is None

    def expect(self, patterns, timeout, on_wait=None, fatal=rb"Kernel panic|emergency mode|You are in emergency"):
        """Wartet, bis eines der Muster hinter der aktuellen Position steht; gibt (index, match) zurück."""
        pats = [re.compile(p) for p in patterns]
        end = time.time() + timeout * self.scale
        while time.time() < end:
            self.pump()
            rest = self.buf[self.pos:]
            for i, p in enumerate(pats):
                m = p.search(rest)
                if m:
                    self.pos += m.end()
                    return i, m
            if fatal and re.search(fatal, rest):
                raise Fail(f"{self.name}: {re.search(fatal, rest).group(0).decode(errors='replace')}")
            if not self.alive():
                raise Fail(f"{self.name}: QEMU hat sich beendet (Exit {self.proc.returncode})")
            if on_wait:
                on_wait()
            time.sleep(0.2)
        raise Fail(f"{self.name}: Zeitüberschreitung beim Warten auf {[p.pattern for p in pats]}")

    def send(self, text):
        self.cur.sock.setblocking(True)
        self.cur.sock.sendall(text.encode())
        self.cur.sock.setblocking(False)

    def run(self, cmd, timeout=120, answers=None):
        """Befehl in der angemeldeten Shell; gibt (Exit-Code, Ausgabe) zurück. answers: {Muster: Eingabe}."""
        start = len(self.buf)
        self.send(cmd + '; echo "__RC=$?__"\n')
        pats = [rb"__RC=(\d+)__"] + [k for k in (answers or {})]
        vals = list((answers or {}).values())
        while True:
            i, m = self.expect(pats, timeout)
            if i == 0:
                out = self.buf[start:self.pos].decode(errors="replace")
                return int(m.group(1)), out
            self.send(vals[i - 1])

    # --- Monitor: Tastatur und Bildschirm ---
    def monitor(self, cmd):
        s = socket.socket(socket.AF_UNIX)
        s.connect(self.mon_path)
        s.sendall(cmd.encode() + b"\n")
        time.sleep(0.15)
        s.close()

    def key(self, k):
        self.monitor(f"sendkey {k}")
        time.sleep(0.08)

    def type(self, text):
        for c in text:
            if c.isalnum():
                self.key(f"shift-{c.lower()}" if c.isupper() else c)
            else:
                self.key(KEYS[c])

    def shot(self, label):
        self.shots += 1
        ppm = os.path.join(self.out, f"{self.name}-{self.shots:02d}-{label}.ppm")
        try:
            self.monitor(f"screendump {ppm}")
            time.sleep(1)
            if shutil.which("convert") and os.path.exists(ppm):
                subprocess.run(["convert", ppm, ppm[:-4] + ".png"], check=False)
                os.unlink(ppm)
        except OSError:
            pass

    def stop(self):
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for p in self.ports:
            p.raw.close()

    def wait_exit(self, timeout):
        end = time.time() + timeout * self.scale
        while time.time() < end:
            self.pump()
            if not self.alive():
                return True
            time.sleep(0.5)
        return False


def login(vm, user, password, timeout):
    """Anmeldung an der seriellen Konsole, danach eine eindeutige Eingabeaufforderung."""
    vm.expect([rb"login: *$"], timeout)
    time.sleep(1)
    vm.send(user + "\n")
    i, _ = vm.expect([rb"[Pp]asswor[dt]: *$", rb"[$#] *$"], 60)
    if i == 0:
        vm.send(password + "\n")
        vm.expect([rb"[$#] *$"], 60, fatal=rb"Login incorrect|Anmeldung fehlgeschlagen|inkorrekt")
    # PS1 so setzen, dass das Echo des Befehls die Aufforderung nicht enthält
    vm.send("export TERM=dumb PS1='V''MT> '; stty -echo cols 200\n")
    vm.expect([rb"VMT> "], 30)


def system_state(vm, label, strict):
    rc, out = vm.run("systemctl is-system-running --wait", 300)
    state = out.strip().splitlines()[-2] if len(out.strip().splitlines()) >= 2 else out.strip()
    _, failed = vm.run("systemctl --failed --no-legend --plain --no-pager", 30)
    log(f"{label}: Systemzustand {state.strip()}")
    failed = "\n".join(u for u in failed.splitlines() if ".service" in u or ".mount" in u or ".socket" in u)
    if failed.strip():
        log(f"{label}: fehlgeschlagene Units:\n{failed}")
    if strict and "running" not in state:
        raise Fail(f"{label}: Systemzustand »{state.strip()}« statt »running«\n{failed}")


def linux_line(iso):
    """Zeile der linux-Anweisung im GRUB-Editor für den ersten Menüeintrag des ISO (0 = »setparams …«)."""
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(["xorriso", "-osirrox", "on", "-indev", iso, "-extract", "/boot/grub/grub.cfg", f"{d}/grub.cfg"],
                       capture_output=True, check=True)
        cfg = open(f"{d}/grub.cfg").read()
    body = cfg[cfg.index("{", cfg.index("menuentry")) + 1:]
    body = body[:body.index("}")]
    # der Editor zeigt den Text nach »{« bis zum Zeilenende als erste Zeile nach setparams (hier leer)
    return 1 + next(i for i, line in enumerate(body.split("\n")) if line.strip().startswith("linux"))


def grub_add_console(vm, wait, downs, extra):
    """Im GRUB-Menü des Mediums den ersten Eintrag bearbeiten und console=ttyS0 (und extra) anhängen."""
    # Pfeil hoch hält die Wartezeit des Menüs an (Eintrag 0 bleibt gewählt) und stört die Firmware nicht
    end = time.time() + wait * vm.scale
    while time.time() < end:
        vm.key("up")
        time.sleep(1)
    vm.shot("grub-menue")
    vm.key("e")
    time.sleep(1)
    for _ in range(downs):        # zur Zeile »linux /boot/vmlinuz …«
        vm.key("down")
    vm.key("end")
    vm.type(" console=ttyS0,115200" + extra)
    vm.shot("grub-bearbeitet")
    vm.key("ctrl-x")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("iso")
    ap.add_argument("--firmware", choices=["bios", "uefi"], required=True)
    ap.add_argument("--boot", choices=["grub", "systemd-boot"], required=True)
    ap.add_argument("--encrypt", action="store_true")
    ap.add_argument("--out", default="vm-test")
    ap.add_argument("--memory", default="4096")
    ap.add_argument("--grub-wait", type=float, default=20, help="Sekunden bis zum GRUB-Menü (KVM)")
    ap.add_argument("--strict", action="store_true", help="Live- und installiertes System müssen »running« sein")
    a = ap.parse_args()
    if a.boot == "systemd-boot" and a.firmware != "uefi":
        ap.error("systemd-boot braucht --firmware uefi")
    out = os.path.realpath(a.out)
    os.makedirs(out, exist_ok=True)
    kvm = os.access("/dev/kvm", os.W_OK)
    scale = 1 if kvm else 6       # ohne KVM (TCG) läuft alles etwa sechsmal langsamer
    name = f"{a.firmware}-{a.boot}{'-luks' if a.encrypt else ''}"
    log(f"VM-Test {name} ({'kvm' if kvm else 'tcg'})")

    disk = os.path.join(out, f"{name}-disk.qcow2")
    subprocess.run(["qemu-img", "create", "-q", "-f", "qcow2", disk, "20G"], check=True)
    base = ["-machine", f"q35,accel={'kvm' if kvm else 'tcg'}", "-cpu", "host" if kvm else "max",
            "-m", a.memory, "-smp", "2", "-vga", "std",
            "-drive", f"file={disk},format=qcow2,if=none,id=hd0", "-device", "virtio-blk-pci,drive=hd0,bootindex=2",
            "-netdev", "user,id=n0", "-device", "virtio-net-pci,netdev=n0"]
    if a.firmware == "uefi":
        code = next((p for p in OVMF_CODE if os.path.exists(p)), None)
        vars_src = next((p for p in OVMF_VARS if os.path.exists(p)), None)
        if not code or not vars_src:
            raise SystemExit("OVMF nicht gefunden (Paket ovmf)")
        vars_ = os.path.join(out, f"{name}-OVMF_VARS.fd")
        shutil.copyfile(vars_src, vars_)      # bleibt für den Neustart erhalten (Starteinträge im NVRAM)
        base += ["-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
                 "-drive", f"if=pflash,format=raw,unit=1,file={vars_}"]

    ok = False
    vm = None
    try:
        # --- 1. Live-System über die Firmware ---
        iso = ["-drive", f"file={os.path.realpath(a.iso)},format=raw,if=none,media=cdrom,readonly=on,id=cd0",
               "-device", "ide-cd,drive=cd0,bootindex=1"]
        vm = VM(out, f"{name}-live", base + iso, scale)
        # Der Live-Benutzer meldet sich nur an tty1 selbst an (leeres Passwort, seriell abgewiesen): für den Test
        # eine Root-Shell auf der seriellen Konsole statt des Anmeldeprompts – wie der angemeldete Mensch an tty1
        grub_add_console(vm, a.grub_wait, linux_line(os.path.realpath(a.iso)),
                         " systemd.debug_shell=ttyS0 systemd.mask=serial-getty@ttyS0.service")
        log("Live-System startet (GRUB vom Medium → Kernel → Initramfs)")
        vm.expect([rb"[$#] *$"], 600)
        time.sleep(2)
        vm.send("\nexport TERM=dumb PS1='V''MT> '; stty -echo cols 200\n")
        vm.expect([rb"VMT> "], 30)
        # Die Debug-Shell läuft unter SELinux als initrc_t; ein Mensch ist nach der Anmeldung unconfined_t.
        # Deshalb nur ein Test-Passwort setzen und an ttyS1 richtig anmelden (agetty → login → PAM).
        rc, outp = vm.run(f"echo 'live:{USER_PW}' | chpasswd && systemctl start serial-getty@ttyS1.service", 60)
        if rc:
            raise Fail(f"Anmeldung an ttyS1 nicht einrichtbar:\n{outp}")
        vm.use(1)
        login(vm, "live", USER_PW, 120)
        _, ctx = vm.run("id -Z", 30)
        log(f"Live-System: angemeldet als live ({ctx.strip().splitlines()[0] if ctx.strip() else '?'})")
        vm.shot("live-angemeldet")
        _, fw = vm.run("test -d /sys/firmware/efi && echo FW=uefi || echo FW=bios", 30)
        want = f"FW={a.firmware}"
        if want not in fw:
            got = "uefi" if "FW=uefi" in fw else "bios"
            raise Fail(f"Live-System meldet FW={got}, erwartet {want}")
        system_state(vm, "Live-System", a.strict)

        # --- 2. Installation im laufenden Live-System ---
        log(f"symfonos-install --boot {a.boot}{' (verschlüsselt)' if a.encrypt else ''}")
        opts = f"--disk /dev/vda --boot {a.boot} --user tester --hostname symfonos-test --yes"
        opts += "" if a.encrypt else " --no-encrypt"
        answers = {rb"(?i)passphrase[^\n]*: *$": LUKS_PW + "\n"} if a.encrypt else None
        rc, outp = vm.run(f"sudo env SYMFONOS_PASSWORD={USER_PW} symfonos-install {opts} 2>&1 | tail -n 40; "
                          "test ${PIPESTATUS[0]} = 0", 1800, answers)
        if rc:
            raise Fail(f"symfonos-install fehlgeschlagen:\n{outp[-3000:]}")
        if a.boot == "grub":
            # Testvorbereitung: serielle Konsole im installierten GRUB eintragen (der Test liest dort mit)
            dev = "/dev/mapper/vmtest" if a.encrypt else "/dev/vda3"
            rc, outp = vm.run(("sudo cryptsetup open /dev/vda3 vmtest; " if a.encrypt else "") +
                              f"sudo mount -o subvol=@ {dev} /mnt && "
                              "sudo sed -i -E 's@^([[:space:]]*linux[[:space:]].*)$@\\1 console=ttyS0,115200@' "
                              "/mnt/boot/grub/grub.cfg && sudo grep -c console=ttyS0 /mnt/boot/grub/grub.cfg; "
                              "r=$?; sudo umount /mnt; " + ("sudo cryptsetup close vmtest; " if a.encrypt else "") +
                              "test $r = 0", 120,
                              {rb"(?i)passphrase[^\n]*: *$": LUKS_PW + "\n"} if a.encrypt else None)
            if rc:
                raise Fail(f"grub.cfg ließ sich nicht anpassen:\n{outp[-2000:]}")
        if a.firmware == "uefi":
            _, entries = vm.run("sudo efibootmgr 2>&1 | head -n 20", 30)
            log("UEFI-Starteinträge:\n" + entries.strip())
        vm.send("sudo poweroff\n")
        if not vm.wait_exit(180):
            raise Fail("Live-System fährt nicht herunter")
        vm.stop()

        # --- 3. Start von der Platte ---
        extra = []
        if a.boot == "systemd-boot":
            extra = ["-smbios", "type=11,value=io.systemd.stub.kernel-cmdline-extra=console=ttyS0,115200"]
        vm = VM(out, f"{name}-installiert", base + extra, scale)
        log("Installiertes System startet über die Firmware")
        if a.encrypt:
            if a.boot == "grub":
                # GRUB entsperrt die Wurzel selbst (Bildschirm, nicht seriell), danach fragt das Initramfs erneut
                time.sleep(a.grub_wait * scale)
                vm.shot("grub-passphrase")
                vm.type(LUKS_PW)
                vm.key("ret")
            vm.expect([rb"(?i)passphrase[^\n]*: *$"], 600)
            vm.shot("initramfs-passphrase")
            time.sleep(1)
            vm.send(LUKS_PW + "\n")
            log("Passphrase im Initramfs eingegeben")
        login(vm, "tester", USER_PW, 600)
        vm.shot("installiert-angemeldet")
        _, info = vm.run("cat /proc/cmdline; findmnt -no SOURCE,FSTYPE,OPTIONS /; "
                         "test -d /sys/firmware/efi && bootctl status 2>/dev/null | sed -n '/Current Boot Loader/,/^$/p'", 60)
        log("Installiertes System:\n" + info.strip())
        if a.encrypt and "/dev/mapper/" not in info:
            raise Fail("Wurzel liegt nicht auf einem entsperrten LUKS-Gerät")
        system_state(vm, "Installiertes System", a.strict)
        ok = True
        log(f"VM-Test {name} bestanden")
    except Fail as e:
        print(f"::error title=VM-Test {name}::{str(e)[-3500:]}".replace("\n", "%0A"), flush=True)
        if vm:
            vm.shot("fehler")
    finally:
        if vm:
            vm.stop()
        if os.path.exists(disk):
            os.unlink(disk)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

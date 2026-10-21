# Paketformat `.syp` und Paketmanager `syp`

## Aufbau einer `.syp`-Datei
Unkomprimiertes POSIX-tar (PAX) mit exakt vier Einträgen in fester Reihenfolge:

| Eintrag | Inhalt |
|---|---|
| `.SYPINFO.json` | Metadaten (kanonisches JSON, sortierte Schlüssel) |
| `.FILES.json` | Dateiliste: `path`, `type` (file/dir/link), `mode`, `size`, `sha256`, `target` |
| `data.tar.zst` | Nutzdaten relativ zu `/`; uid/gid 0, mtime = `SOURCE_DATE_EPOCH` → reproduzierbar |
| `.SIGNATURE.json` | `{"key_id", "alg": "ed25519", "sig"}` über `"syp-v1\n" + sha256(info) + sha256(files) + sha256(data)` |

Beim Entpacken werden abgelehnt: absolute Pfade, `..`, Pfade hinter Symlinks, Geräte/FIFOs/Hardlinks, nicht in `.FILES.json` deklarierte Einträge. Jede Datei wird gegen ihre Prüfsumme kontrolliert.

## Metadaten (`.SYPINFO.json`)
```json
{
  "format": 1, "name": "krita", "version": "1:5.2.9-1", "arch": "x86_64",
  "summary": "...", "description": "...", "license": "GPL-3.0-or-later", "url": "...",
  "depends": ["qt6-base>=6.6", "so:libGL.so.1", "python | python-minimal"],
  "optdepends": ["krita-plugin-gmic"], "provides": ["so:libkritaui.so.18"],
  "conflicts": ["krita-git"], "replaces": [], "backup": ["etc/krita.conf"],
  "groups": [], "categories": ["Graphics", "RasterGraphics"],
  "scripts": {"post_install": "...", "pre_remove": "..."},
  "origin": {"format": "arch", "source_version": "5.2.9-1", "scripts_reviewed": true},
  "installed_size": 123456789, "build_date": 1760000000
}
```
- **Versionen**: `epoch:version-release`, Vergleich wie rpm/pacman (`1.0rc1 < 1.0 < 1.0.1`, `~` aus deb/rpm wird zu Vorabversion).
- **Abhängigkeiten**: `name`, `name<op>version` (`= >= <= < >`), Alternativen mit `|`, virtuelle Namen über `provides` (inkl. `so:<soname>`).
- **Skripte**: `pre/post_install`, `pre/post_upgrade`, `pre/post_remove` (sh). Umgebung: `SYP_NEW_VERSION`, `SYP_OLD_VERSION`. Skripte konvertierter Pakete laufen nur, wenn `scripts_reviewed` gesetzt ist oder `--trust-scripts` angegeben wird.

## Repository
```
<repo>/index.json.zst          signierter Index: {name, arch, generated, packages:[{info, file, sha256, size}]}
<repo>/index.json.zst.sig      Ed25519-Signatur
<repo>/pool/<a>/<paket>.syp
```
Vertrauen: `/etc/syp/keys/*.pub` (Paket `symfonos-keyring`). `syp sync` verwirft Indizes mit falscher Signatur. Jedes heruntergeladene Paket muss die Prüfsumme aus dem signierten Index **und** eine gültige eigene Signatur haben.

## Abhängigkeitsauflösung
Backtracking-Solver: Kandidaten sortiert nach installierter Version, Namensgleichheit, neuester Version und Repo-Priorität. Konflikte führen zum Zurücksetzen und zur nächsten Alternative. Ersetzt ein Upgrade ein Paket, werden danach alle Rückwärtsabhängigkeiten erneut geprüft und bei Bedarf mit aktualisiert. Fehlermeldungen nennen die fehlende Abhängigkeit und wer sie benötigt.

## Transaktion
1. Pakete laden, Index-Prüfsumme + Signatur prüfen
2. Topologisch sortieren, Dateikonflikte prüfen (gegen DB und unverwaltete Dateien)
3. snapper-Pre-Snapshot, Historieneintrag `running`
4. Entfernen → Entpacken in Staging → atomar platzieren (`rename`, Fallback copy+replace bei EXDEV) → alte Dateien entfernen → DB
5. Konfigdateien (`backup`): unverändert → ersetzen; lokal geändert → `.sypnew`; beim Entfernen geändert → `.sypsave`
6. Trigger einmalig (ldconfig, systemd, Icons, Schriften, MIME, depmod, mkinitcpio, GRUB)
7. Post-Snapshot, Historie `done`

## Updates und Rollbacks
- `syp upgrade`: sync + Solver mit `name>=installiert`; Downgrades nur per Rollback.
- `syp rollback [tx]`: stellt den Paketzustand vor `tx` aus dem Cache (Standard: 3 Versionen je Paket) oder dem Repo wieder her – als neue, selbst wieder umkehrbare Transaktion.
- `syp rollback --system [tx]`: Btrfs-Snapshot (snapper) – erfasst auch Konfigurationsänderungen außerhalb von syp.
- `syp doctor`: unterbrochene Transaktionen, verletzte Abhängigkeiten, Schlüssel, veraltete Repos, offene `.sypnew`.

## Bau-Rezept (`SYPBUILD.toml`)
Implementierung: `csrc/lib/build.c`; Beispiele unter `distro/recipes/`.

```toml
[package]
name = "openssl"
version = "3.5.2-1"
summary = "TLS/SSL und Kryptographie"
license = "Apache-2.0"                      # SPDX
depends = ["glibc", "zlib", "crypto-policies"]
makedepends = ["perl"]                      # nur zum Bauen, nicht im Paket
provides = ["so:libssl.so.3"]
backup = ["etc/ssl/openssl.cnf"]            # nie still überschreiben (.sypnew)
security = ["CVE-2025-9230"]                # behobene Lücken → syp audit, upgrade --security

[[source]]
url = "https://github.com/openssl/openssl/releases/download/openssl-3.5.2/openssl-3.5.2.tar.gz"
sha256 = "c53a47e5…"                        # Pflicht; "TODO" füllt `syp sums` bzw. die CI
[[source]]
path = "@/distro/security/crypto/openssl.cnf"   # "@/" = Wurzel dieses Repositorys

[build]
style = "autotools"                         # autotools | make | meson | cmake | python | perl | meta
configure_flags = "--openssldir=/etc/ssl"
post_install = 'install -m644 "$SRCDIR/openssl.cnf" "$DESTDIR/etc/ssl/openssl.cnf"'
# oder eigene Schritte: prepare / build / check / package (bash)

[options]
strip = true
hardening = "strict"                        # strict (Standard) | warn | off; warn/off nur mit Begründung
hardening_reason = ""

[scripts]
post_upgrade = "/usr/bin/update-crypto-policies"
```

Umgebung der Schritte: `SRCDIR`, `S` (entpackte Hauptquelle), `DESTDIR`, `RECIPEDIR`, `PKGNAME`, `PKGVER`,
`PKGREL`, `CFLAGS`/`LDFLAGS` (gehärtet), `MAKEFLAGS`, `NPROC`, `SOURCE_DATE_EPOCH`.

Nach `package` prüft `syp build` jede ELF-Datei (PIE, RELRO, BIND_NOW, NX-Stack, TEXTREL, RPATH) und
alle Dateirechte (beschreibbar für alle → Fehler, setuid/setgid → Übersicht). Das Ergebnis steht
im Paket unter `origin.hardening`.

Bauen: `syp build REZEPT -k SCHLÜSSEL` (`--chroot WURZEL` für einen isolierten Bau), alle Rezepte
in Abhängigkeitsreihenfolge mit `syp buildall distro/recipes --repo REPO`. Index ansehen:
`syp repo-show REPO --pub SCHLÜSSEL.pub`.

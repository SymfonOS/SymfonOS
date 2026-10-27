#!/usr/bin/env python3
"""Rezepte der Desktop-Stufe (Grafik, Wayland, Audio, Qt 6, KDE Frameworks, Plasma, Apps) erzeugen.

Vorlage sind die Bauanleitungen von BLFS 12.4 (systemd), passend zur LFS-12.4-Basis. Die Paketfamilien
(Xorg-Bibliotheken, Qt-Module, KDE Frameworks, Plasma) haben je eine gemeinsame Bauvorschrift; so bleibt
ein Versionssprung eine Änderung an einer Stelle:

    tools/desktop-recipes.py            # Rezepte unter distro/recipes/ schreiben bzw. aktualisieren
    tools/desktop-recipes.py --check    # nur prüfen, ob die Rezepte zum Generator passen (CI)

Erzeugte Rezepte tragen die Kopfzeile GENERATED; von Hand gepflegte Rezepte werden nie überschrieben.
Prüfsummen bleiben beim Neuschreiben erhalten (neue Quellen bekommen "TODO", die CI trägt sie ein).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECIPES = ROOT / "distro" / "recipes"
GENERATED = "# GENERATED von tools/desktop-recipes.py – Änderungen dort vornehmen"

QT = "6.9.2"
KF = "6.17.0"
PLASMA = "6.4.4"
GEAR = "25.08.0"
LLVM = "20.1.8"
KHRONOS = "1.4.321"

PKGS: list[dict] = []


def pkg(name, version, sources, summary, license, depends=(), makedepends=(), style=None, flags="",
        pre="", post="", build=None, package=None, provides=(), backup=(), dir=None, rel=1, options=None,
        scripts=None, arch=None, url=None, mf="", check=None):
    if isinstance(sources, (str, dict)):
        sources = [sources]
    PKGS.append(dict(name=name, version=f"{version}-{rel}", sources=list(sources), summary=summary,
                     license=license, depends=list(depends), makedepends=list(makedepends), style=style,
                     flags=flags, pre=pre, post=post, build=build, package=package, provides=list(provides),
                     backup=list(backup), dir=dir, options=options or {}, scripts=scripts or {}, arch=arch,
                     url=url, mf=mf, check=check))


def nox(url):
    """Quelle, die nicht entpackt wird (Patch, Zusatzdatei)."""
    return {"url": url, "extract": False}


BLFS_PATCH = "https://www.linuxfromscratch.org/patches/blfs/12.4/"

# Gemeinsame CMake-Schalter für KDE-Software (ECM legt die Qt-Pfade selbst fest)
KDE_FLAGS = "-DKDE_INSTALL_LIBEXECDIR=lib -DBUILD_QCH=OFF -DBUILD_PYTHON_BINDINGS=OFF -Wno-dev"

# ---------------------------------------------------------------------------------------------------
# Werkzeuge und Basisbibliotheken
# ---------------------------------------------------------------------------------------------------
pkg("nasm", "2.16.03", "https://www.nasm.us/pub/nasm/releasebuilds/2.16.03/nasm-2.16.03.tar.xz",
    "Netwide Assembler", "BSD-2-Clause", ["glibc"], style="autotools")
pkg("icu", "77.1", {"url": "https://github.com/unicode-org/icu/releases/download/release-77-1/icu4c-77_1-src.tgz",
                    "filename": "icu4c-77_1-src.tgz"},
    "Unicode-Bibliothek (ICU)", "Unicode-3.0", ["glibc", "gcc"], dir="icu",
    build='cd "$S/source"\n./configure --prefix=/usr\nmake',
    package='cd "$S/source"\nmake DESTDIR="$DESTDIR" install',
    provides=["so:libicuuc.so.77", "so:libicui18n.so.77", "so:libicudata.so.77"])
pkg("libxml2", "2.14.5", "https://download.gnome.org/sources/libxml2/2.14/libxml2-2.14.5.tar.xz",
    "XML-Bibliothek", "MIT", ["glibc", "zlib", "xz", "icu", "readline"], style="autotools",
    flags="--with-history --with-icu PYTHON=/usr/bin/python3",
    post='sed \'/libs=/s/xml2.*/xml2"/\' -i "$DESTDIR/usr/bin/xml2-config"', provides=["so:libxml2.so.16"])
pkg("libxslt", "1.1.43", "https://download.gnome.org/sources/libxslt/1.1/libxslt-1.1.43.tar.xz",
    "XSLT-Bibliothek", "MIT", ["libxml2", "libgcrypt"], style="autotools")
pkg("docbook-xml", "4.5", "https://archive.docbook.org/xml/4.5/docbook-xml-4.5.zip", "DocBook-XML-DTD 4.5",
    "MIT", ["libxml2"], arch="any",
    package=r'''cd "$SRCDIR"
d="$DESTDIR/usr/share/xml/docbook/xml-dtd-4.5"
install -d "$d" "$DESTDIR/etc/xml"
cp -af --no-preserve=ownership docbook.cat *.dtd ent/ *.mod "$d"
''', scripts={"post_install": "/usr/lib/syp/xmlcatalog-docbook", "post_upgrade": "/usr/lib/syp/xmlcatalog-docbook"},
    build=r'''cd "$SRCDIR"
install -Dm755 /dev/stdin "$SRCDIR/xmlcatalog-docbook" <<'EOF'
#!/bin/sh
# Trägt DocBook-XML 4.5 und die XSL-Stylesheets in die XML-Kataloge ein (idempotent)
set -e
C=/etc/xml/catalog D=/etc/xml/docbook
[ -e $D ] || xmlcatalog --noout --create $D
[ -e $C ] || xmlcatalog --noout --create $C
X=/usr/share/xml/docbook/xml-dtd-4.5
add() { xmlcatalog --noout --add "$1" "$2" "$3" "$4"; }
add public "-//OASIS//DTD DocBook XML V4.5//EN" "http://www.oasis-open.org/docbook/xml/4.5/docbookx.dtd" $D
add public "-//OASIS//DTD DocBook XML CALS Table Model V4.5//EN" "file://$X/calstblx.dtd" $D
add public "-//OASIS//DTD XML Exchange Table Model 19990315//EN" "file://$X/soextblx.dtd" $D
add public "-//OASIS//ELEMENTS DocBook XML Information Pool V4.5//EN" "file://$X/dbpoolx.mod" $D
add public "-//OASIS//ELEMENTS DocBook XML Document Hierarchy V4.5//EN" "file://$X/dbhierx.mod" $D
add public "-//OASIS//ELEMENTS DocBook XML HTML Tables V4.5//EN" "file://$X/htmltblx.mod" $D
add public "-//OASIS//ENTITIES DocBook XML Notations V4.5//EN" "file://$X/dbnotnx.mod" $D
add public "-//OASIS//ENTITIES DocBook XML Character Entities V4.5//EN" "file://$X/dbcentx.mod" $D
add public "-//OASIS//ENTITIES DocBook XML Additional General Entities V4.5//EN" "file://$X/dbgenent.mod" $D
add rewriteSystem "http://www.oasis-open.org/docbook/xml/4.5" "file://$X" $D
add rewriteURI "http://www.oasis-open.org/docbook/xml/4.5" "file://$X" $D
add delegatePublic "-//OASIS//ENTITIES DocBook XML" "file://$D" $C
add delegatePublic "-//OASIS//DTD DocBook XML" "file://$D" $C
add delegateSystem "http://www.oasis-open.org/docbook/" "file://$D" $C
add delegateURI "http://www.oasis-open.org/docbook/" "file://$D" $C
S=$(ls -d /usr/share/xml/docbook/xsl-stylesheets-nons-* 2>/dev/null | tail -1)
if [ -n "$S" ]; then
  for u in http://cdn.docbook.org/release/xsl-nons/current http://docbook.sourceforge.net/release/xsl/current; do
    add rewriteSystem "$u" "$S" $C; add rewriteURI "$u" "$S" $C
  done
fi
EOF
''', post=None)
# Skript zum Katalog-Eintrag gehört ins Paket
PKGS[-1]["package"] += 'install -Dm755 "$SRCDIR/xmlcatalog-docbook" "$DESTDIR/usr/lib/syp/xmlcatalog-docbook"\n'
pkg("docbook-xsl-nons", "1.79.2",
    ["https://github.com/docbook/xslt10-stylesheets/releases/download/release/1.79.2/docbook-xsl-nons-1.79.2.tar.bz2",
     nox(BLFS_PATCH + "docbook-xsl-nons-1.79.2-stack_fix-1.patch")],
    "DocBook-XSL-Stylesheets", "MIT", ["libxml2", "docbook-xml"], arch="any",
    pre='patch -Np1 -i ../docbook-xsl-nons-1.79.2-stack_fix-1.patch',
    build=":",
    package=r'''cd "$S"
d="$DESTDIR/usr/share/xml/docbook/xsl-stylesheets-nons-1.79.2"
install -d "$d"
cp -R VERSION assembly common eclipse epub epub3 extensions fo highlighting html htmlhelp images javahelp lib \
  manpages params profiling roundtrip slides template tests tools webhelp website xhtml xhtml-1_1 xhtml5 "$d"
ln -s VERSION "$d/VERSION.xsl"
''', scripts={"post_install": "/usr/lib/syp/xmlcatalog-docbook", "post_upgrade": "/usr/lib/syp/xmlcatalog-docbook"})
pkg("perl-uri", "5.32", "https://www.cpan.org/authors/id/O/OA/OALDERS/URI-5.32.tar.gz",
    "Perl-Modul URI", "Artistic-1.0-Perl OR GPL-1.0-or-later", ["perl"], style="perl", arch="any", rel=2)
pkg("python-mako", "1.3.10", "https://files.pythonhosted.org/packages/source/M/Mako/mako-1.3.10.tar.gz",
    "Mako-Templates (für Mesa)", "MIT", ["python", "python-markupsafe"], ["python-setuptools", "python-wheel"],
    style="python", arch="any")
pkg("libyaml", "0.2.5", "https://github.com/yaml/libyaml/releases/download/0.2.5/yaml-0.2.5.tar.gz",
    "YAML-Parser", "MIT", ["glibc"], style="autotools")
pkg("python-pyyaml", "6.0.2", "https://files.pythonhosted.org/packages/source/P/PyYAML/pyyaml-6.0.2.tar.gz",
    "YAML für Python", "MIT", ["python", "libyaml"], ["python-setuptools", "python-wheel", "python-cython"],
    style="python")
pkg("python-cython", "3.1.3", "https://github.com/cython/cython/releases/download/3.1.3/cython-3.1.3.tar.gz",
    "C-Erweiterungen für Python", "Apache-2.0", ["python"], ["python-setuptools", "python-wheel"],
    style="python")
pkg("python-ply", "3.11", "https://files.pythonhosted.org/packages/source/p/ply/ply-3.11.tar.gz",
    "Lex/Yacc für Python (Intel-Vulkan-Treiber)", "BSD-3-Clause", ["python"],
    ["python-setuptools", "python-wheel"], style="python", arch="any")
pkg("python-docutils", "0.21.2", "https://files.pythonhosted.org/packages/source/d/docutils/docutils-0.21.2.tar.gz",
    "Dokumentationswerkzeuge (Handbuchseiten)", "BSD-2-Clause AND GPL-3.0-or-later AND Python-2.0",
    ["python"], ["python-flit-core"], style="python", arch="any")
pkg("libgpg-error", "1.55", "https://www.gnupg.org/ftp/gcrypt/libgpg-error/libgpg-error-1.55.tar.bz2",
    "Fehlercodes der GnuPG-Bibliotheken", "LGPL-2.1-or-later", ["glibc"], style="autotools",
    provides=["so:libgpg-error.so.0"])
pkg("libgcrypt", "1.11.2", "https://www.gnupg.org/ftp/gcrypt/libgcrypt/libgcrypt-1.11.2.tar.bz2",
    "Kryptographie-Bibliothek (GnuPG)", "LGPL-2.1-or-later", ["libgpg-error"], style="autotools",
    provides=["so:libgcrypt.so.20"])
pkg("lmdb", "0.9.33", {"url": "https://git.openldap.org/openldap/openldap/-/archive/LMDB_0.9.33.tar.bz2",
                       "filename": "openldap-LMDB_0.9.33.tar.bz2"},
    "Lightning Memory-Mapped Database", "OLDAP-2.8", ["glibc"],
    build='cd "$S/libraries/liblmdb"\nmake CC=gcc XCFLAGS="$CFLAGS" LDFLAGS="$LDFLAGS"\nsed -i "s| liblmdb.a||" Makefile',
    package='cd "$S/libraries/liblmdb"\nmake prefix=/usr DESTDIR="$DESTDIR" install')
pkg("libqrencode", "4.1.1", {"url": "https://github.com/fukuchi/libqrencode/archive/v4.1.1/libqrencode-4.1.1.tar.gz"},
    "QR-Code-Bibliothek", "LGPL-2.1-or-later", ["glibc", "libpng"], ["autoconf", "automake", "libtool"],
    style="autotools", pre="sh autogen.sh")
pkg("libical", "3.0.20", "https://github.com/libical/libical/releases/download/v3.0.20/libical-3.0.20.tar.gz",
    "iCalendar-Bibliothek", "LGPL-2.1-only OR MPL-2.0", ["glibc", "icu", "glib2", "libxml2"], ["cmake"],
    style="cmake", flags="-DSHARED_ONLY=yes -DICAL_BUILD_DOCS=false -DGOBJECT_INTROSPECTION=false "
                         "-DICAL_GLIB_VAPI=false -DLIBICAL_BUILD_TESTING=false", mf="-j1")
pkg("boost", "1.89.0", "https://github.com/boostorg/boost/releases/download/boost-1.89.0/boost-1.89.0-b2-nodocs.tar.xz",
    "C++-Bibliotheken (Boost)", "BSL-1.0", ["glibc", "gcc", "bzip2", "zlib", "xz", "zstd", "icu"], ["which", "python"],
    build='cd "$S"\n./bootstrap.sh --prefix=/usr --with-python=python3\n'
          './b2 stage -j"$NPROC" threading=multi link=shared variant=release debug-symbols=off',
    package='cd "$S"\n./b2 install --prefix="$DESTDIR/usr" threading=multi link=shared variant=release debug-symbols=off')
pkg("duktape", "2.7.0", "https://duktape.org/duktape-2.7.0.tar.xz", "Eingebettete JavaScript-Engine (polkit)",
    "MIT", ["glibc"],
    build='cd "$S"\nsed -i "s/-Os/-O2/" Makefile.sharedlibrary\nmake -f Makefile.sharedlibrary INSTALL_PREFIX=/usr',
    # Das Makefile wertet DESTDIR selbst aus (aus der Umgebung); INSTALL_PREFIX bleibt /usr
    package='cd "$S"\nmake -f Makefile.sharedlibrary INSTALL_PREFIX=/usr DESTDIR="$DESTDIR" install\n'
            'test -f "$DESTDIR/usr/include/duktape.h"', rel=2)
pkg("libunistring", "1.3", "https://ftp.gnu.org/gnu/libunistring/libunistring-1.3.tar.xz",
    "Unicode-Zeichenketten (GNU)", "LGPL-3.0-or-later OR GPL-2.0-or-later", ["glibc"], style="autotools")
pkg("libtasn1", "4.20.0", "https://ftp.gnu.org/gnu/libtasn1/libtasn1-4.20.0.tar.gz", "ASN.1-Bibliothek",
    "LGPL-2.1-or-later", ["glibc"], style="autotools")
pkg("nettle", "3.10.2", "https://ftp.gnu.org/gnu/nettle/nettle-3.10.2.tar.gz", "Kryptographie-Bibliothek (Nettle)",
    "LGPL-3.0-or-later OR GPL-2.0-or-later", ["glibc", "gmp"], style="autotools")
pkg("gnutls", "3.8.10", "https://www.gnupg.org/ftp/gcrypt/gnutls/v3.8/gnutls-3.8.10.tar.xz", "TLS-Bibliothek (GnuTLS)",
    "LGPL-2.1-or-later", ["nettle", "libtasn1", "libunistring", "gmp", "zlib", "zstd", "brotli"],
    style="autotools", flags="--with-default-trust-store-file=/etc/ssl/certs/ca-certificates.crt "
                             "--with-included-unistring=no --without-p11-kit --disable-guile --disable-doc")
pkg("brotli", "1.1.0", {"url": "https://github.com/google/brotli/archive/v1.1.0/brotli-1.1.0.tar.gz"},
    "Brotli-Kompression", "MIT", ["glibc"], ["cmake"], style="cmake")
pkg("libnl", "3.11.0", "https://github.com/thom311/libnl/releases/download/libnl3_11_0/libnl-3.11.0.tar.gz",
    "Netlink-Bibliotheken", "LGPL-2.1-only", ["glibc"], style="autotools")
pkg("libpcap", "1.10.5", "https://www.tcpdump.org/release/libpcap-1.10.5.tar.gz", "Paketmitschnitt-Bibliothek",
    "BSD-3-Clause", ["glibc", "libnl", "dbus"], style="autotools",
    post='rm -f "$DESTDIR/usr/lib/libpcap.a"')
pkg("lm-sensors", "3.6.2", {"url": "https://github.com/hramrach/lm-sensors/archive/V3-6-2/lm-sensors-3-6-2.tar.gz"},
    "Hardware-Sensoren", "GPL-2.0-or-later AND LGPL-2.1-or-later", ["glibc", "perl"], ["which", "bison", "flex"],
    build='cd "$S"\nmake PREFIX=/usr BUILD_STATIC_LIB=0 MANDIR=/usr/share/man SBINDIR=/usr/bin CFLAGS="$CFLAGS" LDFLAGS="$LDFLAGS"',
    package='cd "$S"\nmake PREFIX=/usr BUILD_STATIC_LIB=0 MANDIR=/usr/share/man SBINDIR=/usr/bin DESTDIR="$DESTDIR" install')
pkg("fuse3", "3.17.4", "https://github.com/libfuse/libfuse/releases/download/fuse-3.17.4/fuse-3.17.4.tar.gz",
    "Dateisysteme im Benutzerraum (FUSE 3)", "GPL-2.0-only AND LGPL-2.1-only", ["glibc"], ["meson"],
    style="meson", pre="sed -i '/^udev/,$ s/^/#/' util/meson.build", flags="-Dexamples=false",
    post='chmod u+s "$DESTDIR/usr/bin/fusermount3"', options={"hardening_reason": "fusermount3 ist setuid (wie bei Fedora)"})
pkg("libseccomp", "2.6.0", "https://github.com/seccomp/libseccomp/releases/download/v2.6.0/libseccomp-2.6.0.tar.gz",
    "Seccomp-Filterbibliothek", "LGPL-2.1-only", ["glibc"], ["gperf"], style="autotools")
pkg("bubblewrap", "0.11.0", "https://github.com/containers/bubblewrap/releases/download/v0.11.0/bubblewrap-0.11.0.tar.xz",
    "Sandbox-Werkzeug (Portale, Flatpak)", "LGPL-2.0-or-later", ["glibc", "libcap", "libselinux"], ["meson"],
    style="meson", flags="-Dman=disabled -Dselinux=enabled")
pkg("hwdata", "0.398", {"url": "https://github.com/vcrhonek/hwdata/archive/v0.398/hwdata-0.398.tar.gz"},
    "Hardware-Kennungen (PCI, USB, Monitore)", "GPL-2.0-or-later", arch="any",
    build='cd "$S"\n./configure --prefix=/usr --disable-blacklist',
    package='cd "$S"\nmake DESTDIR="$DESTDIR" install')
pkg("iso-codes", "4.18.0", {"url": "https://deb.debian.org/debian/pool/main/i/iso-codes/iso-codes_4.18.0.orig.tar.xz"},
    "ISO-Länder-, Sprach- und Währungscodes", "LGPL-2.1-or-later", ["python"], ["meson", "gettext"], arch="any",
    # Der Debian-Tarball bringt je nach Version Meson oder Autotools mit
    build='cd "$S"\nif [ -f meson.build ]; then meson setup _build --prefix=/usr && ninja -C _build\n'
          'else ./configure --prefix=/usr && make; fi',
    package='cd "$S"\nif [ -f meson.build ]; then DESTDIR="$DESTDIR" ninja -C _build install\n'
            'else make DESTDIR="$DESTDIR" install; fi')
pkg("libsigsegv", "2.15", "https://ftp.gnu.org/gnu/libsigsegv/libsigsegv-2.15.tar.gz", "Seitenfehler-Behandlung",
    "GPL-2.0-or-later", ["glibc"], style="autotools", flags="--enable-shared")
pkg("libqalculate", "5.7.0", "https://github.com/Qalculate/libqalculate/releases/download/v5.7.0/libqalculate-5.7.0.tar.gz",
    "Rechenbibliothek (KRunner-Rechner)", "GPL-2.0-or-later", ["curl", "icu", "libxml2", "mpfr", "gmp", "readline"],
    ["intltool", "perl-xml-parser"], style="autotools")

# ---------------------------------------------------------------------------------------------------
# Bilder, Schriften, Text
# ---------------------------------------------------------------------------------------------------
pkg("libpng", "1.6.50", ["https://downloads.sourceforge.net/libpng/libpng-1.6.50.tar.xz",
                         nox("https://downloads.sourceforge.net/sourceforge/libpng-apng/libpng-1.6.47-apng.patch.gz")],
    "PNG-Bibliothek (mit APNG)", "libpng-2.0", ["zlib"], style="autotools",
    pre="gzip -cd ../libpng-1.6.47-apng.patch.gz | patch -p1", provides=["so:libpng16.so.16"])
pkg("libjpeg-turbo", "3.0.1", "https://downloads.sourceforge.net/libjpeg-turbo/libjpeg-turbo-3.0.1.tar.gz",
    "JPEG-Bibliothek", "IJG AND BSD-3-Clause AND Zlib", ["glibc"], ["cmake", "nasm"], style="cmake",
    flags="-DENABLE_STATIC=FALSE -DCMAKE_INSTALL_DEFAULT_LIBDIR=lib -DCMAKE_INSTALL_LIBDIR=/usr/lib -DCMAKE_SKIP_INSTALL_RPATH=ON",
    # libjpeg-turbo legt relative LIBDIR unter das Quellverzeichnis (src/lib) – deshalb absolut
    post='ls "$DESTDIR"/usr/lib*/pkgconfig/libjpeg.pc "$DESTDIR/usr/include/jpeglib.h"', rel=2,
    provides=["libjpeg", "so:libjpeg.so.62", "so:libturbojpeg.so.0"])  # ohne WITH_JPEG8 heißt sie .so.62
pkg("libtiff", "4.7.0", "https://download.osgeo.org/libtiff/tiff-4.7.0.tar.gz", "TIFF-Bibliothek", "libtiff",
    ["zlib", "xz", "zstd", "libjpeg-turbo", "libwebp"], ["cmake"], style="cmake",
    flags="-Dtiff-docs=OFF -Dtiff-tests=OFF -Dtiff-contrib=OFF", provides=["so:libtiff.so.6"])
pkg("libwebp", "1.6.0", "https://storage.googleapis.com/downloads.webmproject.org/releases/webp/libwebp-1.6.0.tar.gz",
    "WebP-Bibliothek", "BSD-3-Clause", ["libpng", "libjpeg-turbo"], style="autotools",
    flags="--enable-libwebpmux --enable-libwebpdemux --enable-libwebpdecoder --enable-libwebpextras --enable-swap-16bit-csp")
pkg("giflib", "5.2.2", ["https://sourceforge.net/projects/giflib/files/giflib-5.2.2.tar.gz",
                        nox(BLFS_PATCH + "giflib-5.2.2-upstream_fixes-1.patch"),
                        nox(BLFS_PATCH + "giflib-5.2.2-security_fixes-1.patch")],
    "GIF-Bibliothek", "MIT", ["glibc"],
    pre="patch -Np1 -i ../giflib-5.2.2-upstream_fixes-1.patch\npatch -Np1 -i ../giflib-5.2.2-security_fixes-1.patch\n"
        "cp pic/gifgrid.gif doc/giflib-logo.gif",
    build='cd "$S"\nmake CFLAGS="$CFLAGS -std=gnu99 -fPIC" LDFLAGS="$LDFLAGS" libgif.so gifbuild gifclrmp giffix giftext giftool',
    package='cd "$S"\nmake PREFIX=/usr DESTDIR="$DESTDIR" install-bin install-include install-lib\nrm -f "$DESTDIR/usr/lib/libgif.a"')
pkg("lcms2", "2.17", "https://github.com/mm2/Little-CMS/releases/download/lcms2.17/lcms2-2.17.tar.gz",
    "Farbmanagement (Little CMS)", "MIT", ["libjpeg-turbo", "libtiff"], style="autotools")
pkg("openjpeg", "2.5.3", {"url": "https://github.com/uclouvain/openjpeg/archive/v2.5.3/openjpeg-2.5.3.tar.gz"},
    "JPEG-2000-Bibliothek", "BSD-2-Clause", ["libpng", "libtiff", "lcms2"], ["cmake"], style="cmake",
    flags="-DBUILD_STATIC_LIBS=OFF")
pkg("graphite2", "1.3.14", "https://github.com/silnrsi/graphite/releases/download/1.3.14/graphite2-1.3.14.tgz",
    "Schriftsatz für komplexe Schriften", "LGPL-2.1-or-later OR MPL-2.0 OR GPL-2.0-or-later", ["gcc"], ["cmake"],
    style="cmake",
    pre="sed -i '/cmptest/d' tests/CMakeLists.txt\nsed -i '/cmake_policy(SET CMP0012 NEW)/d' CMakeLists.txt\n"
        "sed -i 's/PythonInterp/Python3/' CMakeLists.txt\n"
        "find . -name CMakeLists.txt | xargs sed -i 's/VERSION 2.8.0 FATAL_ERROR/VERSION 4.0.0/'\n"
        "sed -i '/Font.h/i #include <cstdint>' tests/featuremap/featuremaptest.cpp")
pkg("glib2", "2.84.4", ["https://download.gnome.org/sources/glib/2.84/glib-2.84.4.tar.xz",
                        nox(BLFS_PATCH + "glib-skip_warnings-1.patch")],
    "GLib-Kernbibliothek", "LGPL-2.1-or-later", ["glibc", "libffi", "pcre2", "zlib", "util-linux", "libselinux", "libelf"],
    ["meson", "python-packaging", "python-docutils", "libxslt"], style="meson",
    pre="patch -Np1 -i ../glib-skip_warnings-1.patch",
    flags="-Dintrospection=disabled -Dglib_debug=disabled -Dman-pages=disabled -Dsysprof=disabled -Dselinux=enabled -Dtests=false",
    provides=["glib", "so:libglib-2.0.so.0", "so:libgio-2.0.so.0", "so:libgobject-2.0.so.0"])
pkg("shared-mime-info", "2.4", {"url": "https://gitlab.freedesktop.org/xdg/shared-mime-info/-/archive/2.4/shared-mime-info-2.4.tar.gz"},
    "MIME-Datenbank", "GPL-2.0-or-later", ["glib2", "libxml2"], ["meson", "gettext"], style="meson",
    flags="-Dupdate-mimedb=false -Dbuild-tests=false -Dbuild-translations=true")
pkg("desktop-file-utils", "0.28", "https://www.freedesktop.org/software/desktop-file-utils/releases/desktop-file-utils-0.28.tar.xz",
    "Werkzeuge für .desktop-Dateien", "GPL-2.0-or-later", ["glib2"], ["meson"], style="meson")
# FreeType (Basis) bleibt ohne HarfBuzz, sonst entstünde ein Abhängigkeitszyklus
pkg("harfbuzz", "11.4.1", "https://github.com/harfbuzz/harfbuzz/releases/download/11.4.1/harfbuzz-11.4.1.tar.xz",
    "Text-Shaping", "MIT", ["glib2", "graphite2", "icu", "freetype"], ["meson"], style="meson",
    flags="-Dgraphite2=enabled -Dicu=enabled -Dfreetype=enabled -Dglib=enabled -Dgobject=enabled "
          "-Dintrospection=disabled -Dtests=disabled -Ddocs=disabled -Dcairo=disabled",
    # HarfBuzz baut C++ ohne Ausnahmen und linkt ohne libstdc++: -fexceptions und die libstdc++-Assertions
    # der Standardschalter erzeugten unaufgelöste Symbole (__gxx_personality_v0)
    options={"cflags": "-O2 -pipe -march=x86-64 -mtune=generic -fno-plt -fstack-protector-strong "
                       "-fstack-clash-protection -fcf-protection -D_FORTIFY_SOURCE=3"},
    provides=["so:libharfbuzz.so.0"])
pkg("fontconfig", "2.17.1", "https://gitlab.freedesktop.org/api/v4/projects/890/packages/generic/fontconfig/2.17.1/fontconfig-2.17.1.tar.xz",
    "Schriftkonfiguration", "HPND AND MIT", ["freetype", "expat", "util-linux"], ["gperf", "python"],
    style="autotools", flags="--disable-docs", backup=["etc/fonts/fonts.conf"], provides=["so:libfontconfig.so.1"])
pkg("fribidi", "1.0.16", "https://github.com/fribidi/fribidi/releases/download/v1.0.16/fribidi-1.0.16.tar.xz",
    "Bidirektionaler Text", "LGPL-2.1-or-later", ["glibc"], ["meson"], style="meson", flags="-Ddocs=false -Dtests=false")
pkg("pixman", "0.46.4", "https://www.cairographics.org/releases/pixman-0.46.4.tar.gz", "Pixel-Manipulation",
    "MIT", ["glibc"], ["meson"], style="meson", flags="-Dtests=disabled -Ddemos=disabled")
pkg("cairo", "1.18.4", "https://www.cairographics.org/releases/cairo-1.18.4.tar.xz", "2D-Grafikbibliothek",
    "LGPL-2.1-only OR MPL-1.1", ["libpng", "pixman", "fontconfig", "freetype", "glib2", "libx11", "libxext",
                                  "libxrender", "libxcb", "lzo", "zlib"], ["meson"], style="meson",
    flags="-Dtests=disabled -Dxlib-xcb=enabled", provides=["so:libcairo.so.2"])
pkg("pango", "1.56.4", "https://download.gnome.org/sources/pango/1.56/pango-1.56.4.tar.xz", "Textlayout",
    "LGPL-2.1-or-later", ["fontconfig", "freetype", "harfbuzz", "fribidi", "glib2", "cairo", "libxft"], ["meson"],
    style="meson", flags="-Dintrospection=disabled -Dbuild-testsuite=false -Dbuild-examples=false")
pkg("libsecret", "0.21.7", "https://download.gnome.org/sources/libsecret/0.21/libsecret-0.21.7.tar.xz",
    "Secret-Service-Bibliothek (KWallet)", "LGPL-2.1-or-later", ["glib2", "libgcrypt"], ["meson"], style="meson",
    flags="-Dgtk_doc=false -Dmanpage=false -Dvapi=false -Dintrospection=false -Dcrypto=libgcrypt "
          "-Dbash_completion=disabled")
pkg("gdk-pixbuf", "2.42.12", "https://download.gnome.org/sources/gdk-pixbuf/2.42/gdk-pixbuf-2.42.12.tar.xz",
    "Bildlade-Bibliothek (GdkPixbuf)", "LGPL-2.1-or-later", ["glib2", "libpng", "libjpeg-turbo", "libtiff", "shared-mime-info"],
    ["meson", "python-docutils"], style="meson",
    flags="-Dothers=enabled -Dintrospection=disabled -Dman=false -Dtests=false -Dinstalled_tests=false",
    scripts={"post_install": "gdk-pixbuf-query-loaders --update-cache", "post_upgrade": "gdk-pixbuf-query-loaders --update-cache"})
pkg("json-glib", "1.10.6", "https://download.gnome.org/sources/json-glib/1.10/json-glib-1.10.6.tar.xz",
    "JSON für GLib", "LGPL-2.1-or-later", ["glib2"], ["meson"], style="meson",
    flags="-Dintrospection=disabled -Dtests=false -Ddocumentation=disabled -Dman=false")
pkg("libgudev", "238", "https://download.gnome.org/sources/libgudev/238/libgudev-238.tar.xz", "GObject-Anbindung für udev",
    "LGPL-2.1-or-later", ["glib2", "systemd"], ["meson"], style="meson",
    flags="-Dintrospection=disabled -Dvapi=disabled -Dtests=disabled")
pkg("exiv2", "0.28.5", {"url": "https://github.com/Exiv2/exiv2/archive/v0.28.5/exiv2-0.28.5.tar.gz"},
    "Bild-Metadaten (Exif, IPTC, XMP)", "GPL-2.0-or-later", ["expat", "zlib", "brotli", "curl", "inih"], ["cmake"],
    style="cmake", flags="-DEXIV2_ENABLE_VIDEO=yes -DEXIV2_ENABLE_WEBREADY=yes -DEXIV2_ENABLE_CURL=yes "
                         "-DEXIV2_BUILD_SAMPLES=no -DEXIV2_BUILD_UNIT_TESTS=no -DCMAKE_SKIP_INSTALL_RPATH=ON")
pkg("inih", "61", {"url": "https://github.com/benhoyt/inih/archive/r61/inih-r61.tar.gz"}, "INI-Parser", "BSD-3-Clause",
    ["glibc"], ["meson"], style="meson")
pkg("utfcpp", "4.0.6", {"url": "https://github.com/nemtrif/utfcpp/archive/v4.0.6/utfcpp-4.0.6.tar.gz"},
    "UTF-8 für C++ (Header)", "BSL-1.0", [], ["cmake"], style="cmake", arch="any")
pkg("taglib", "2.1.1", "https://taglib.org/releases/taglib-2.1.1.tar.gz", "Audio-Metadaten", "LGPL-2.1-only OR MPL-1.1",
    ["zlib", "gcc"], ["cmake", "utfcpp"], style="cmake", flags="-DBUILD_SHARED_LIBS=ON")
pkg("zxing-cpp", "2.3.0", {"url": "https://github.com/zxing-cpp/zxing-cpp/archive/v2.3.0/zxing-cpp-2.3.0.tar.gz"},
    "Strichcode-Bibliothek", "Apache-2.0", ["gcc"], ["cmake"], style="cmake",
    flags="-DZXING_EXAMPLES=OFF -DZXING_BLACKBOX_TESTS=OFF -DZXING_UNIT_TESTS=OFF")
pkg("libraw", "0.21.4", "https://www.libraw.org/data/LibRaw-0.21.4.tar.gz", "RAW-Fotos (Kameras)",
    "LGPL-2.1-only OR CDDL-1.0", ["libjpeg-turbo", "lcms2", "zlib"], style="autotools",
    flags="--enable-jpeg --enable-jasper=no --enable-lcms --disable-examples")

# ---------------------------------------------------------------------------------------------------
# X11-Protokolle und -Bibliotheken (für Xwayland und X11-Programme)
# ---------------------------------------------------------------------------------------------------
XORG = "https://www.x.org/pub/individual/"
pkg("util-macros", "1.20.2", XORG + "util/util-macros-1.20.2.tar.xz", "Xorg-Autotools-Makros", "MIT", [],
    style="autotools", arch="any")
pkg("xorgproto", "2024.1", XORG + "proto/xorgproto-2024.1.tar.xz", "X11-Protokoll-Header", "MIT", [],
    ["meson", "util-macros"], style="meson", arch="any")
pkg("xcb-proto", "1.17.0", XORG + "proto/xcb-proto-1.17.0.tar.xz", "XCB-Protokollbeschreibungen", "MIT",
    ["python"], style="autotools", arch="any", flags="PYTHON=python3")
pkg("libxau", "1.0.12", XORG + "lib/libXau-1.0.12.tar.xz", "X11-Autorisierung", "MIT", ["xorgproto"], style="autotools")
pkg("libxdmcp", "1.1.5", XORG + "lib/libXdmcp-1.1.5.tar.xz", "X Display Manager Control Protocol", "MIT",
    ["xorgproto"], style="autotools")
pkg("libxcb", "1.17.0", XORG + "lib/libxcb-1.17.0.tar.xz", "X-Protokoll-C-Bibliothek (XCB)", "MIT",
    ["libxau", "libxdmcp"], ["xcb-proto", "python"], style="autotools", flags="--without-doxygen",
    options={"env": ["LC_ALL=C.UTF-8"]}, provides=["so:libxcb.so.1"])

# (Name, Version, Abhängigkeiten, Zusatzschalter) – Reihenfolge und Schalter nach BLFS »Xorg Libraries«
X7LIB = [
    ("xtrans", "1.6.0", [], ""),
    ("libX11", "1.8.12", ["libxcb", "xtrans"], ""),
    ("libXext", "1.3.6", ["libx11"], ""),
    ("libICE", "1.1.2", ["xtrans"], ""),
    ("libSM", "1.2.6", ["libice", "util-linux"], ""),
    ("libXScrnSaver", "1.2.4", ["libxext"], ""),
    ("libXt", "1.3.1", ["libsm", "libx11"], "--with-appdefaultdir=/etc/X11/app-defaults"),
    ("libXmu", "1.2.1", ["libxt", "libxext"], ""),
    ("libXpm", "3.5.17", ["libxt", "libxext"], "--disable-open-zfile"),
    ("libXaw", "1.0.16", ["libxmu", "libxpm"], ""),
    ("libXfixes", "6.0.1", ["libx11"], ""),
    ("libXcomposite", "0.4.6", ["libxfixes"], ""),
    ("libXrender", "0.9.12", ["libx11"], ""),
    ("libXcursor", "1.2.3", ["libxrender", "libxfixes"], ""),
    ("libXdamage", "1.1.6", ["libxfixes"], ""),
    ("libfontenc", "1.1.8", ["zlib", "xorgproto"], ""),
    ("libXfont2", "2.0.7", ["freetype", "libfontenc", "xtrans"], "--disable-devel-docs"),
    ("libXft", "2.3.9", ["libxrender", "fontconfig"], ""),
    ("libXi", "1.8.2", ["libxext", "libxfixes"], ""),
    ("libXinerama", "1.1.5", ["libxext"], ""),
    ("libXrandr", "1.5.4", ["libxext", "libxrender"], ""),
    ("libXres", "1.2.2", ["libxext"], ""),
    ("libXtst", "1.2.5", ["libxi"], ""),
    ("libXv", "1.0.13", ["libxext"], ""),
    ("libXxf86vm", "1.1.6", ["libxext"], ""),
    ("libxkbfile", "1.1.3", ["libx11"], ""),
    ("libxshmfence", "1.3.3", ["xorgproto"], ""),
    ("libXpresent", "1.0.1", ["libxfixes", "libxrandr"], ""),
]
for n, v, d, f in X7LIB:
    pkg(n.lower(), v, XORG + f"lib/{n}-{v}.tar.xz", f"X11-Bibliothek {n}", "MIT", d + ["xorgproto"],
        ["util-macros", "pkgconf"], style="autotools", flags=f, arch="any" if n == "xtrans" else None)
pkg("libpciaccess", "0.18.1", XORG + "lib/libpciaccess-0.18.1.tar.xz", "PCI-Zugriff", "MIT", ["zlib", "hwdata"],
    ["meson"], style="meson")
for n, v in [("xcb-util", "0.4.1"), ("xcb-util-image", "0.4.1"), ("xcb-util-keysyms", "0.4.1"),
             ("xcb-util-renderutil", "0.3.10"), ("xcb-util-wm", "0.4.2"), ("xcb-util-cursor", "0.1.5")]:
    d = ["libxcb"] + (["xcb-util"] if n != "xcb-util" else []) + (
        ["xcb-util-image", "xcb-util-renderutil"] if n == "xcb-util-cursor" else [])
    pkg(n, v, f"https://xcb.freedesktop.org/dist/{n}-{v}.tar.xz", f"XCB-Hilfsbibliothek {n}", "MIT", d,
        ["util-macros", "m4"], style="autotools")
pkg("libxcvt", "0.1.3", XORG + "lib/libxcvt-0.1.3.tar.xz", "VESA-CVT-Modi", "MIT", ["glibc"], ["meson"], style="meson")
pkg("xkeyboard-config", "2.45", XORG + "data/xkeyboard-config/xkeyboard-config-2.45.tar.xz", "Tastaturbelegungen (XKB)",
    "MIT", [], ["meson", "libxslt", "python", "gettext"], style="meson", arch="any")
pkg("xbitmaps", "1.1.3", XORG + "data/xbitmaps-1.1.3.tar.xz", "X11-Bitmaps", "MIT", [], ["util-macros"],
    style="autotools", arch="any")
pkg("font-util", "1.4.1", XORG + "font/font-util-1.4.1.tar.xz", "X11-Schrift-Werkzeuge", "MIT", [],
    ["util-macros"], style="autotools")
pkg("encodings", "1.1.0", XORG + "font/encodings-1.1.0.tar.xz", "X11-Zeichenkodierungen", "MIT", [],
    ["font-util", "mkfontscale"], style="autotools", arch="any")
for n, v, d in [("mkfontscale", "1.2.3", ["libfontenc", "freetype"]), ("xkbcomp", "1.4.7", ["libxkbfile", "libx11"]),
                ("xauth", "1.1.4", ["libxau", "libxext", "libxmu"]), ("xrdb", "1.2.2", ["libxmu"]),
                ("xprop", "1.2.8", ["libx11"]), ("xset", "1.2.5", ["libxmu"]), ("setxkbmap", "1.3.4", ["libxkbfile"]),
                ("xrandr", "1.5.3", ["libxrandr"]), ("xinput", "1.6.4", ["libxi", "libxrandr", "libxinerama"]),
                ("xdpyinfo", "1.4.0", ["libxext", "libxtst", "libxi"]), ("xmessage", "1.0.7", ["libxaw"])]:
    pkg(n, v, XORG + f"app/{n}-{v}.tar.xz", f"X11-Programm {n}", "MIT", d, ["util-macros"], style="autotools")

# ---------------------------------------------------------------------------------------------------
# Wayland, Grafiktreiber (Mesa mit LLVM), Eingabegeräte
# ---------------------------------------------------------------------------------------------------
pkg("wayland", "1.24.0", "https://gitlab.freedesktop.org/wayland/wayland/-/releases/1.24.0/downloads/wayland-1.24.0.tar.xz",
    "Wayland-Bibliotheken", "MIT", ["libffi", "expat", "libxml2"], ["meson"], style="meson",
    flags="-Ddocumentation=false -Dtests=false", provides=["so:libwayland-client.so.0", "so:libwayland-server.so.0"])
pkg("wayland-protocols", "1.45", "https://gitlab.freedesktop.org/wayland/wayland-protocols/-/releases/1.45/downloads/wayland-protocols-1.45.tar.xz",
    "Wayland-Protokollerweiterungen", "MIT", [], ["meson", "wayland"], style="meson", flags="-Dtests=false", arch="any")
pkg("libdrm", "2.4.125", "https://dri.freedesktop.org/libdrm/libdrm-2.4.125.tar.xz", "Direct-Rendering-Bibliothek",
    "MIT", ["libpciaccess", "systemd"], ["meson"], style="meson",
    flags="-Dudev=true -Dvalgrind=disabled -Dtests=false -Dman-pages=disabled", provides=["so:libdrm.so.2"])
pkg("libxkbcommon", "1.11.0", {"url": "https://github.com/lfs-book/libxkbcommon/archive/v1.11.0/libxkbcommon-1.11.0.tar.gz"},
    "Tastaturbelegungen (Bibliothek)", "MIT", ["xkeyboard-config", "libxcb", "wayland", "libxml2"],
    ["meson", "wayland-protocols", "bison"], style="meson", flags="-Denable-docs=false",
    provides=["so:libxkbcommon.so.0"])
pkg("llvm", LLVM, [f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{LLVM}/llvm-{LLVM}.src.tar.xz",
                  f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{LLVM}/cmake-{LLVM}.src.tar.xz",
                  f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{LLVM}/third-party-{LLVM}.src.tar.xz",
                  f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{LLVM}/clang-{LLVM}.src.tar.xz"],
    "LLVM-Compiler-Infrastruktur und Clang", "Apache-2.0 WITH LLVM-exception",
    ["glibc", "gcc", "libffi", "zlib", "zstd", "libxml2", "ncurses"], ["cmake", "ninja", "python"],
    dir=f"llvm-{LLVM}.src", style="cmake",
    pre=f'''mv ../cmake-{LLVM}.src ../cmake
mv ../third-party-{LLVM}.src ../third-party
mv ../clang-{LLVM}.src tools/clang
grep -rl '#!.*python' | xargs sed -i '1s/python$/python3/'
sed 's/utility/tool/' -i utils/FileCheck/CMakeLists.txt''', options={"env": ["CC=gcc", "CXX=g++"]},
    flags="-DCMAKE_SKIP_INSTALL_RPATH=ON -DLLVM_ENABLE_FFI=ON -DLLVM_BUILD_LLVM_DYLIB=ON -DLLVM_LINK_LLVM_DYLIB=ON "
          "-DLLVM_ENABLE_RTTI=ON -DLLVM_TARGETS_TO_BUILD='host;AMDGPU' -DLLVM_BINUTILS_INCDIR=/usr/include "
          "-DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_TESTS=OFF -DLLVM_INCLUDE_EXAMPLES=OFF "
          "-DLLVM_ENABLE_ZSTD=ON -DLLVM_PARALLEL_LINK_JOBS=2 -DCLANG_DEFAULT_PIE_ON_LINUX=ON "
          "-DCLANG_CONFIG_FILE_SYSTEM_DIR=/etc/clang -DLLVM_INSTALL_UTILS=ON -Wno-dev",
    provides=[f"so:libLLVM.so.{LLVM.split('.')[0]}.1", "clang"])
pkg("spirv-headers", f"{KHRONOS}.0",
    {"url": f"https://github.com/KhronosGroup/SPIRV-Headers/archive/vulkan-sdk-{KHRONOS}.0/SPIRV-Headers-vulkan-sdk-{KHRONOS}.0.tar.gz"},
    "SPIR-V-Header", "MIT", [], ["cmake"], style="cmake", arch="any")
pkg("spirv-tools", f"{KHRONOS}.0",
    {"url": f"https://github.com/KhronosGroup/SPIRV-Tools/archive/vulkan-sdk-{KHRONOS}.0/SPIRV-Tools-vulkan-sdk-{KHRONOS}.0.tar.gz"},
    "SPIR-V-Werkzeuge", "Apache-2.0", ["gcc"], ["cmake", "spirv-headers", "python"], style="cmake",
    flags="-DSPIRV_WERROR=OFF -DBUILD_SHARED_LIBS=ON -DSPIRV_TOOLS_BUILD_STATIC=OFF -DSPIRV-Headers_SOURCE_DIR=/usr "
          "-DSPIRV_SKIP_TESTS=ON")
pkg("spirv-llvm-translator", "20.1.5",
    {"url": "https://github.com/KhronosGroup/SPIRV-LLVM-Translator/archive/v20.1.5/SPIRV-LLVM-Translator-20.1.5.tar.gz"},
    "LLVM-IR ↔ SPIR-V", "Apache-2.0 WITH LLVM-exception", ["llvm", "spirv-tools"], ["cmake", "spirv-headers"],
    style="cmake", flags="-DBUILD_SHARED_LIBS=ON -DCMAKE_SKIP_INSTALL_RPATH=ON "
                         "-DLLVM_EXTERNAL_SPIRV_HEADERS_SOURCE_DIR=/usr -DLLVM_INCLUDE_TESTS=OFF")
pkg("libclc", LLVM, f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{LLVM}/libclc-{LLVM}.src.tar.xz",
    "OpenCL-C-Bibliothek (für Intel-Treiber in Mesa)", "Apache-2.0 WITH LLVM-exception", [],
    ["llvm", "spirv-llvm-translator", "cmake", "python"], style="cmake", arch="any",
    flags="-DLIBCLC_TARGETS_TO_BUILD='spirv-mesa3d-;spirv64-mesa3d-'")
pkg("glslang", "15.4.0", {"url": "https://github.com/KhronosGroup/glslang/archive/15.4.0/glslang-15.4.0.tar.gz"},
    "GLSL/HLSL-Compiler", "BSD-3-Clause", ["spirv-tools"], ["cmake", "python"], style="cmake",
    flags="-DALLOW_EXTERNAL_SPIRV_TOOLS=ON -DBUILD_SHARED_LIBS=ON -DGLSLANG_TESTS=OFF -DENABLE_OPT=ON")
pkg("vulkan-headers", KHRONOS, {"url": f"https://github.com/KhronosGroup/Vulkan-Headers/archive/v{KHRONOS}/Vulkan-Headers-{KHRONOS}.tar.gz"},
    "Vulkan-Header", "Apache-2.0", [], ["cmake"], style="cmake", arch="any")
pkg("vulkan-loader", KHRONOS, {"url": f"https://github.com/KhronosGroup/Vulkan-Loader/archive/v{KHRONOS}/Vulkan-Loader-{KHRONOS}.tar.gz"},
    "Vulkan-Lader", "Apache-2.0", ["libx11", "libxrandr", "wayland"], ["cmake", "vulkan-headers", "python"],
    style="cmake", flags="-DCMAKE_SKIP_INSTALL_RPATH=ON", provides=["so:libvulkan.so.1"])
pkg("libva", "2.22.0", {"url": "https://github.com/intel/libva/archive/2.22.0/libva-2.22.0.tar.gz"},
    "Video-Beschleunigung (VA-API)", "MIT", ["libdrm", "libx11", "libxext", "libxfixes", "wayland"], ["meson"],
    style="meson", flags="-Dwith_glx=no -Denable_docs=false")
pkg("mesa", "25.1.8", "https://mesa.freedesktop.org/archive/mesa-25.1.8.tar.xz",
    "OpenGL-, Vulkan- und Videotreiber (Mesa)", "MIT AND BSD-3-Clause AND SGI-B-2.0",
    ["libdrm", "wayland", "libx11", "libxext", "libxfixes", "libxshmfence", "libxxf86vm", "libxrandr", "libxcb",
     "expat", "zlib", "zstd", "libelf", "llvm", "libclc", "spirv-llvm-translator", "spirv-tools", "libva",
     "lm-sensors", "vulkan-loader"],
    ["meson", "python-mako", "python-pyyaml", "python-ply", "glslang", "wayland-protocols", "bison", "flex",
     "libxrender", "xorgproto"],
    style="meson",
    flags="-Dplatforms=x11,wayland "
          "-Dgallium-drivers=radeonsi,r600,r300,nouveau,iris,crocus,i915,virgl,llvmpipe,softpipe,svga,zink "
          "-Dvulkan-drivers=amd,intel,intel_hasvk,swrast,virtio -Dvulkan-layers=device-select,overlay "
          "-Dvideo-codecs=all -Dgallium-va=enabled -Dglvnd=disabled -Dvalgrind=disabled -Dlibunwind=disabled "
          "-Dlmsensors=enabled -Dbuild-tests=false -Dosmesa=false",
    provides=["opengl", "so:libGL.so.1", "so:libEGL.so.1", "so:libgbm.so.1", "so:libGLESv2.so.2"])
pkg("libepoxy", "1.5.10", "https://download.gnome.org/sources/libepoxy/1.5/libepoxy-1.5.10.tar.xz",
    "OpenGL-Funktionszeiger", "MIT", ["mesa"], ["meson"], style="meson", flags="-Ddocs=false -Dtests=false")
pkg("mtdev", "1.1.7", "https://bitmath.org/code/mtdev/mtdev-1.1.7.tar.bz2", "Multitouch-Protokoll", "MIT",
    ["glibc"], style="autotools")
pkg("libevdev", "1.13.4", "https://www.freedesktop.org/software/libevdev/libevdev-1.13.4.tar.xz",
    "evdev-Geräte", "MIT", ["glibc"], ["meson", "python"], style="meson",
    flags="-Ddocumentation=disabled -Dtests=disabled")
pkg("libwacom", "2.16.1", "https://github.com/linuxwacom/libwacom/releases/download/libwacom-2.16.1/libwacom-2.16.1.tar.xz",
    "Grafiktablett-Beschreibungen (Wacom, Huion, XP-Pen …)", "HPND", ["libevdev", "libgudev", "glib2", "libxml2"],
    ["meson"], style="meson", flags="-Dtests=disabled")
pkg("libinput", "1.29.0", {"url": "https://gitlab.freedesktop.org/libinput/libinput/-/archive/1.29.0/libinput-1.29.0.tar.gz"},
    "Eingabegeräte (Maus, Touchpad, Tablett)", "MIT", ["libevdev", "mtdev", "systemd", "libwacom"], ["meson"],
    style="meson", flags="-Ddebug-gui=false -Dtests=false -Dlibwacom=true -Dudev-dir=/usr/lib/udev -Ddocumentation=false",
    provides=["so:libinput.so.10"])
pkg("libdisplay-info", "0.3.0", "https://gitlab.freedesktop.org/emersion/libdisplay-info/-/releases/0.3.0/downloads/libdisplay-info-0.3.0.tar.xz",
    "EDID/DisplayID-Auswertung", "MIT", ["glibc"], ["meson", "hwdata"], style="meson")
pkg("xorg-server", "21.1.18", [XORG + "xserver/xorg-server-21.1.18.tar.xz",
                               nox(BLFS_PATCH + "xorg-server-21.1.18-tearfree_backport-1.patch")],
    "X.Org-Server (für X11-Eingabetreiber; die Sitzung läuft unter Wayland)", "MIT",
    ["libxcvt", "pixman", "libxfont2", "xkeyboard-config", "xkbcomp", "libepoxy", "mesa", "libdrm", "libxshmfence",
     "systemd", "libpciaccess", "libxau", "libxdmcp", "libxkbfile", "libxcb", "xcb-util", "xcb-util-image",
     "xcb-util-keysyms", "xcb-util-renderutil", "xcb-util-wm"],
    ["meson", "font-util", "xorgproto", "xtrans", "xbitmaps", "libxext", "libxrender", "libx11"], style="meson",
    pre="patch -Np1 -i ../xorg-server-21.1.18-tearfree_backport-1.patch",
    flags="-Dglamor=true -Dxkb_output_dir=/var/lib/xkb -Dsecure-rpc=false -Dsystemd_logind=true -Dsuid_wrapper=false "
          "-Dxephyr=false -Dxvfb=false -Dxnest=false -Ddocs=false -Ddevel-docs=false",
    post='install -d "$DESTDIR/etc/X11/xorg.conf.d"')
pkg("xf86-input-libinput", "1.5.0", XORG + "driver/xf86-input-libinput-1.5.0.tar.xz", "X11-Eingabetreiber (libinput)",
    "MIT", ["libinput", "xorg-server"], ["util-macros"], style="autotools")
pkg("xf86-input-evdev", "2.11.0", XORG + "driver/xf86-input-evdev-2.11.0.tar.xz", "X11-Eingabetreiber (evdev)",
    "MIT", ["libevdev", "mtdev", "xorg-server"], ["util-macros"], style="autotools")
pkg("xwayland", "24.1.8", XORG + "xserver/xwayland-24.1.8.tar.xz", "X11-Programme unter Wayland", "MIT",
    ["libxcvt", "pixman", "libxfont2", "libepoxy", "mesa", "wayland", "libdrm", "libxshmfence", "xkbcomp",
     "libxau", "libxdmcp", "libxcb", "openssl"],
    ["meson", "wayland-protocols", "font-util", "xorgproto", "xtrans", "libxkbfile"], style="meson",
    pre="sed -i '/install_man/,$d' meson.build",
    flags="-Dxkb_output_dir=/var/lib/xkb -Dsecure-rpc=false -Dxvfb=false -Ddocs=false -Ddevel-docs=false "
          "-Dlibdecor=false",
    # protocol.txt und Xserver.1 liefert auch xorg-server
    post='rm -f "$DESTDIR/usr/lib/xorg/protocol.txt" "$DESTDIR/usr/share/man/man1/Xserver.1"', rel=2)


# ---------------------------------------------------------------------------------------------------
# Audio und Video (PipeWire, ALSA, Codecs, FFmpeg)
# ---------------------------------------------------------------------------------------------------
pkg("alsa-lib", "1.2.14", ["https://www.alsa-project.org/files/pub/lib/alsa-lib-1.2.14.tar.bz2",
                           nox("https://www.alsa-project.org/files/pub/lib/alsa-ucm-conf-1.2.14.tar.bz2")],
    "ALSA-Bibliothek", "LGPL-2.1-or-later", ["glibc"], style="autotools", dir="alsa-lib-1.2.14",
    post='install -d "$DESTDIR/usr/share/alsa"\n'
         'tar -C "$DESTDIR/usr/share/alsa" --strip-components=1 -xf ../alsa-ucm-conf-1.2.14.tar.bz2',
    provides=["so:libasound.so.2"])
pkg("alsa-utils", "1.2.14", "https://www.alsa-project.org/files/pub/utils/alsa-utils-1.2.14.tar.bz2",
    "ALSA-Werkzeuge (alsamixer, aplay …)", "GPL-2.0-or-later", ["alsa-lib", "ncurses", "libsamplerate"],
    style="autotools", flags="--disable-alsaconf --disable-bat --disable-xmlto --with-curses=ncursesw "
                             "--with-systemdsystemunitdir=/usr/lib/systemd/system")
pkg("libogg", "1.3.6", "https://downloads.xiph.org/releases/ogg/libogg-1.3.6.tar.xz", "Ogg-Container", "BSD-3-Clause",
    ["glibc"], style="autotools")
pkg("libvorbis", "1.3.7", "https://downloads.xiph.org/releases/vorbis/libvorbis-1.3.7.tar.xz", "Vorbis-Audio",
    "BSD-3-Clause", ["libogg"], style="autotools")
pkg("flac", "1.5.0", "https://github.com/xiph/flac/releases/download/1.5.0/flac-1.5.0.tar.xz", "FLAC-Audio",
    "BSD-3-Clause AND GPL-2.0-or-later", ["libogg"], style="autotools",
    flags="--disable-thorough-tests --disable-doxygen-docs")
pkg("opus", "1.5.2", "https://downloads.xiph.org/releases/opus/opus-1.5.2.tar.gz", "Opus-Audio", "BSD-3-Clause",
    ["glibc"], style="autotools", flags="--disable-doc")
pkg("lame", "3.100", "https://downloads.sourceforge.net/lame/lame-3.100.tar.gz", "MP3-Encoder", "LGPL-2.0-or-later",
    ["ncurses"], ["nasm"], style="autotools",
    pre="sed -i -e 's/^\\(\\s*hardcode_libdir_flag_spec\\s*=\\).*/\\1/' configure",
    flags="--enable-mp3rtp --enable-nasm")
pkg("mpg123", "1.33.2", "https://downloads.sourceforge.net/mpg123/mpg123-1.33.2.tar.bz2", "MP3-Decoder",
    "LGPL-2.1-only", ["alsa-lib"], style="autotools", flags="--with-default-audio=alsa")
pkg("libsamplerate", "0.2.2", "https://github.com/libsndfile/libsamplerate/releases/download/0.2.2/libsamplerate-0.2.2.tar.xz",
    "Abtastratenwandlung", "BSD-2-Clause", ["glibc"], style="autotools")
pkg("libsndfile", "1.2.2", "https://github.com/libsndfile/libsndfile/releases/download/1.2.2/libsndfile-1.2.2.tar.xz",
    "Audiodateien lesen und schreiben", "LGPL-2.1-or-later", ["flac", "opus", "libvorbis", "lame", "mpg123", "alsa-lib"],
    style="autotools", pre="sed '/typedef enum/,/bool ;/d' -i src/ALAC/alac_encoder.c src/ALAC/alac_decoder.c")
pkg("speexdsp", "1.2.1", "https://downloads.xiph.org/releases/speex/speexdsp-1.2.1.tar.gz", "Sprachsignal-Verarbeitung",
    "BSD-3-Clause", ["glibc"], style="autotools")
pkg("sbc", "2.1", "https://www.kernel.org/pub/linux/bluetooth/sbc-2.1.tar.xz", "Bluetooth-Audio-Codec (SBC)",
    "GPL-2.0-or-later AND LGPL-2.1-or-later", ["glibc"], style="autotools", flags="--disable-tester")
LUA_PC = r'''V=5.4
R=5.4.8
prefix=/usr
INSTALL_BIN=${prefix}/bin
INSTALL_INC=${prefix}/include
INSTALL_LIB=${prefix}/lib
INSTALL_MAN=${prefix}/share/man/man1
INSTALL_LMOD=${prefix}/share/lua/${V}
INSTALL_CMOD=${prefix}/lib/lua/${V}
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
includedir=${prefix}/include

Name: Lua
Description: An Extensible Extension Language
Version: ${R}
Requires:
Libs: -L${libdir} -llua -lm -ldl
Cflags: -I${includedir}'''
pkg("lua", "5.4.8", ["https://www.lua.org/ftp/lua-5.4.8.tar.gz", nox(BLFS_PATCH + "lua-5.4.8-shared_library-1.patch")],
    "Lua-Skriptsprache", "MIT", ["readline"], dir="lua-5.4.8",
    pre="patch -Np1 -i ../lua-5.4.8-shared_library-1.patch",
    build='cd "$S"\nmake linux MYCFLAGS="$CFLAGS" MYLDFLAGS="$LDFLAGS"',
    package='cd "$S"\nmake INSTALL_TOP="$DESTDIR/usr" INSTALL_DATA="cp -d" INSTALL_MAN="$DESTDIR/usr/share/man/man1" '
            'TO_LIB="liblua.so liblua.so.5.4 liblua.so.5.4.8" install\n'
            "install -Dm644 /dev/stdin \"$DESTDIR/usr/lib/pkgconfig/lua.pc\" <<'LUAPC'\n" + LUA_PC + "\nLUAPC\n"
            'ln -s lua.pc "$DESTDIR/usr/lib/pkgconfig/lua5.4.pc"')
# GStreamer nur als Bibliothek (xdg-desktop-portal prüft Töne mit gstreamer-pbutils); keine optionalen Plugins
GST = "1.26.5"
pkg("gstreamer", GST, f"https://gstreamer.freedesktop.org/src/gstreamer/gstreamer-{GST}.tar.xz",
    "Multimedia-Framework (GStreamer)", "LGPL-2.1-or-later", ["glib2"], ["meson", "flex", "bison"], style="meson",
    flags="-Dauto_features=disabled -Dgst_debug=false -Dtests=disabled -Dexamples=disabled -Ddoc=disabled "
          "-Dintrospection=disabled -Dtools=enabled")
pkg("gst-plugins-base", GST, f"https://gstreamer.freedesktop.org/src/gst-plugins-base/gst-plugins-base-{GST}.tar.xz",
    "GStreamer-Grundbibliotheken (pbutils, audio, video)", "LGPL-2.1-or-later", ["gstreamer", "glib2", "zlib"],
    ["meson"], style="meson",
    flags="-Dauto_features=disabled -Dtests=disabled -Dexamples=disabled -Ddoc=disabled -Dintrospection=disabled "
          "-Dtools=enabled")
pkg("pipewire", "1.4.7", {"url": "https://gitlab.freedesktop.org/pipewire/pipewire/-/archive/1.4.7/pipewire-1.4.7.tar.bz2"},
    "Audio- und Videoserver (PipeWire, mit Pulse- und JACK-Ersatz)", "MIT",
    ["alsa-lib", "dbus", "systemd", "libsndfile", "opus", "sbc", "libcanberra", "readline", "ncurses", "libusb",
     "openssl", "libselinux"], ["meson", "python-docutils"], style="meson",
    flags="'-Dsession-managers=[]' -Dtests=disabled -Dexamples=disabled -Ddocs=disabled -Dman=disabled "
          "-Dbluez5=disabled -Dgstreamer=disabled -Dlibcamera=disabled -Droc=disabled -Dlv2=disabled "
          "-Dlibffado=disabled -Djack=disabled -Dpipewire-jack=enabled -Djack-devel=true "
          "-Dpipewire-alsa=enabled -Dlibpulse=disabled -Davahi=disabled -Dx11=disabled -Dx11-xfixes=disabled "
          "-Dsdl2=disabled -Dvulkan=disabled -Dselinux=enabled -Decho-cancel-webrtc=disabled",
    post=r'''# PipeWire ersetzt PulseAudio und JACK (wie bei Fedora): ALSA-Standardgerät und Benutzerdienste
install -d "$DESTDIR/etc/alsa/conf.d" "$DESTDIR/usr/lib/systemd/user-preset"
ln -s /usr/share/alsa/alsa.conf.d/50-pipewire.conf "$DESTDIR/etc/alsa/conf.d/50-pipewire.conf"
ln -s /usr/share/alsa/alsa.conf.d/99-pipewire-default.conf "$DESTDIR/etc/alsa/conf.d/99-pipewire-default.conf"
printf 'enable pipewire.socket\nenable pipewire-pulse.socket\nenable wireplumber.service\n' \
  > "$DESTDIR/usr/lib/systemd/user-preset/50-pipewire.preset"''',
    provides=["pulseaudio-server", "jack", "so:libpipewire-0.3.so.0", "so:libjack.so.0"])
pkg("wireplumber", "0.5.10", {"url": "https://gitlab.freedesktop.org/pipewire/wireplumber/-/archive/0.5.10/wireplumber-0.5.10.tar.bz2"},
    "Sitzungsverwaltung für PipeWire", "MIT", ["pipewire", "glib2", "systemd", "lua"], ["meson"], style="meson",
    flags="-Dsystem-lua=true -Dintrospection=disabled -Ddoc=disabled -Dtests=false")
pkg("pulseaudio", "17.0", "https://www.freedesktop.org/software/pulseaudio/releases/pulseaudio-17.0.tar.xz",
    "PulseAudio-Clientbibliothek (der Server ist PipeWire)", "LGPL-2.1-or-later",
    ["libsndfile", "dbus", "glib2", "systemd"], ["meson"], style="meson",
    flags="-Ddaemon=false -Ddoxygen=false -Dman=false -Dtests=false -Ddatabase=simple -Dbluez5=disabled "
          "-Dgtk=disabled -Dx11=disabled",
    provides=["libpulse", "so:libpulse.so.0"])
pkg("sound-theme-freedesktop", "0.8", "https://people.freedesktop.org/~mccann/dist/sound-theme-freedesktop-0.8.tar.bz2",
    "Systemklänge (freedesktop)", "GPL-2.0-or-later", [], ["intltool", "perl-xml-parser"], style="autotools", arch="any")
pkg("libcanberra", "0.30", ["https://0pointer.de/lennart/projects/libcanberra/libcanberra-0.30.tar.xz",
                            nox(BLFS_PATCH + "libcanberra-0.30-wayland-1.patch")],
    "Ereignisklänge", "LGPL-2.1-or-later", ["libvorbis", "alsa-lib", "sound-theme-freedesktop", "systemd"],
    style="autotools", dir="libcanberra-0.30", pre="patch -Np1 -i ../libcanberra-0.30-wayland-1.patch",
    flags="--disable-oss --disable-gtk --disable-gtk3 --disable-pulse --disable-gstreamer --disable-lynx")
pkg("libvpx", "1.15.2", {"url": "https://github.com/webmproject/libvpx/archive/v1.15.2/libvpx-1.15.2.tar.gz"},
    "VP8/VP9-Video", "BSD-3-Clause", ["glibc"], ["nasm", "which", "perl"],
    pre="find -type f | xargs touch\nsed -i 's/cp -p/cp/' build/make/Makefile",
    build='cd "$S"\nmkdir -p libvpx-build && cd libvpx-build\n../configure --prefix=/usr --enable-shared --disable-static '
          '--disable-examples --disable-unit-tests --disable-docs\nmake',
    package='cd "$S/libvpx-build"\nmake DESTDIR="$DESTDIR" install')
pkg("x264", "20250815", "https://anduin.linuxfromscratch.org/BLFS/x264/x264-20250815.tar.xz", "H.264-Encoder",
    "GPL-2.0-or-later", ["glibc"], ["nasm"],
    build='cd "$S"\n./configure --prefix=/usr --enable-shared --disable-cli --enable-pic\nmake',
    package='cd "$S"\nmake DESTDIR="$DESTDIR" install')
pkg("x265", "4.1", "https://bitbucket.org/multicoreware/x265_git/downloads/x265_4.1.tar.gz", "H.265-Encoder",
    "GPL-2.0-or-later", ["gcc"], ["cmake", "nasm", "ninja"],
    pre="sed -r '/cmake_policy.*(0025|0054)/d' -i source/CMakeLists.txt",
    build='cmake -S "$S/source" -B "$S/bld" -G Ninja -DCMAKE_INSTALL_PREFIX=/usr -DGIT_ARCHETYPE=1 '
          '-DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DENABLE_CLI=OFF -Wno-dev\nninja -C "$S/bld"',
    package='DESTDIR="$DESTDIR" ninja -C "$S/bld" install\nrm -f "$DESTDIR/usr/lib/libx265.a"')
pkg("libaom", "3.12.1", "https://storage.googleapis.com/aom-releases/libaom-3.12.1.tar.gz", "AV1-Video (Referenz)",
    "BSD-2-Clause", ["gcc"], ["cmake", "nasm", "perl", "ninja"],
    build='cmake -S "$S" -B "$S/aom-build" -G Ninja -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_BUILD_TYPE=Release '
          '-DBUILD_SHARED_LIBS=1 -DENABLE_DOCS=no -DENABLE_TESTS=0 -DENABLE_EXAMPLES=0\nninja -C "$S/aom-build"',
    package='DESTDIR="$DESTDIR" ninja -C "$S/aom-build" install\nrm -f "$DESTDIR/usr/lib/libaom.a"')
pkg("dav1d", "1.5.1", "https://downloads.videolan.org/pub/videolan/dav1d/1.5.1/dav1d-1.5.1.tar.xz",
    "AV1-Decoder (schnell)", "BSD-2-Clause", ["glibc"], ["meson", "nasm"], style="meson",
    flags="-Denable_tests=false -Denable_tools=false")
pkg("libass", "0.17.4", "https://github.com/libass/libass/releases/download/0.17.4/libass-0.17.4.tar.xz",
    "Untertitel-Darstellung", "ISC", ["freetype", "fribidi", "fontconfig", "harfbuzz"], ["nasm"], style="autotools")
pkg("ffmpeg", "7.1.1", ["https://ffmpeg.org/releases/ffmpeg-7.1.1.tar.xz",
                        nox(BLFS_PATCH + "ffmpeg-7.1.1-chromium_method-1.patch")],
    "Audio- und Video-Werkzeugkasten (FFmpeg)", "GPL-3.0-or-later",
    ["libaom", "dav1d", "libass", "freetype", "lame", "libvpx", "opus", "libvorbis", "x264", "x265", "alsa-lib",
     "libva", "libdrm", "libwebp", "openssl", "zlib", "bzip2", "xz", "pulseaudio", "libxcb",
     "vulkan-loader", "openjpeg", "speexdsp"],
    ["nasm", "vulkan-headers", "glslang"], dir="ffmpeg-7.1.1",
    pre="patch -Np1 -i ../ffmpeg-7.1.1-chromium_method-1.patch",
    build=r'''cd "$S"
./configure --prefix=/usr --enable-gpl --enable-version3 --disable-static --enable-shared --disable-debug \
  --enable-libaom --enable-libdav1d --enable-libass --enable-libfreetype --enable-libmp3lame --enable-libopus \
  --enable-libvorbis --enable-libvpx --enable-libx264 --enable-libx265 --enable-libwebp --enable-libopenjpeg \
  --enable-openssl --enable-vaapi --enable-libdrm --enable-vulkan --enable-libpulse --enable-libxcb \
  --disable-doc --ignore-tests=enhanced-flv-av1
make''',
    package='cd "$S"\nmake DESTDIR="$DESTDIR" install',
    provides=["so:libavcodec.so.61", "so:libavformat.so.61", "so:libavutil.so.59", "so:libswscale.so.8"])

# ---------------------------------------------------------------------------------------------------
# Systemdienste für den Desktop
# ---------------------------------------------------------------------------------------------------
pkg("polkit", "126", {"url": "https://github.com/polkit-org/polkit/archive/126/polkit-126.tar.gz"},
    "Berechtigungsdienst (polkit)", "LGPL-2.0-or-later", ["duktape", "glib2", "systemd", "linux-pam", "expat"],
    ["meson", "libxslt", "docbook-xsl-nons", "gettext"], style="meson",
    flags="-Dman=false -Dsession_tracking=logind -Dtests=false -Dintrospection=false "
          "-Dpolkitd_user=polkitd -Dpolkitd_uid=27 -Dexamples=false -Dgtk_doc=false -Dpam_include=system-auth",
    post='install -Dm644 /dev/stdin "$DESTDIR/usr/lib/sysusers.d/polkit.conf" <<\'SYS\'\n'
         'u polkitd 27 "PolicyKit Daemon Owner" /etc/polkit-1 /usr/bin/nologin\nSYS',
    provides=["so:libpolkit-gobject-1.so.0"])
pkg("upower", "1.90.9", {"url": "https://gitlab.freedesktop.org/upower/upower/-/archive/v1.90.9/upower-v1.90.9.tar.bz2"},
    "Energieverwaltung (Akku, Netzteil)", "GPL-2.0-or-later", ["libgudev", "libusb", "glib2", "systemd", "polkit"],
    ["meson", "gettext"], style="meson", flags="-Dgtk-doc=false -Dman=false -Dintrospection=disabled -Didevice=disabled")
pkg("accountsservice", "23.13.9", "https://www.freedesktop.org/software/accountsservice/accountsservice-23.13.9.tar.xz",
    "Benutzerkonten über D-Bus (Anmeldebild, Benutzer-KCM)", "GPL-3.0-or-later", ["polkit", "glib2", "systemd", "shadow"],
    ["meson", "gettext"], style="meson",
    # tests/ bindet das Hilfsprojekt mocklibc ein, das mit GCC 15 nicht kompiliert
    pre="sed -i \"/subdir('tests')/d\" meson.build",
    flags="-Dadmin_group=wheel -Dintrospection=false -Dvapi=false -Ddocbook=false -Dgtk_doc=false")
pkg("xdg-user-dirs", "0.18", "https://user-dirs.freedesktop.org/releases/xdg-user-dirs-0.18.tar.gz",
    "Standardordner (Dokumente, Bilder, Musik …)", "GPL-2.0-or-later", ["glibc"], style="autotools",
    flags="--disable-documentation", backup=["etc/xdg/user-dirs.conf", "etc/xdg/user-dirs.defaults"])
pkg("hicolor-icon-theme", "0.18", "https://icon-theme.freedesktop.org/releases/hicolor-icon-theme-0.18.tar.xz",
    "Grundgerüst für Symbolthemen", "GPL-2.0-only", [], ["meson"], style="meson", arch="any")
pkg("xdg-desktop-portal", "1.20.3", "https://github.com/flatpak/xdg-desktop-portal/releases/download/1.20.3/xdg-desktop-portal-1.20.3.tar.xz",
    "Portale für Desktop-Programme (Dateidialog, Bildschirmfreigabe)", "LGPL-2.1-or-later",
    ["fuse3", "gdk-pixbuf", "json-glib", "pipewire", "dbus", "glib2", "bubblewrap", "systemd", "gst-plugins-base"],
    ["meson", "python-docutils"], style="meson",
    flags="-Dtests=disabled -Ddocumentation=disabled -Dman-pages=disabled -Dflatpak-interfaces=disabled "
          "-Dgeoclue=disabled -Dsystemd=enabled -Dsandboxed-image-validation=enabled -Dsandboxed-sound-validation=disabled")

# ---------------------------------------------------------------------------------------------------
# Qt 6 (Module einzeln, in /usr mit der Verzeichnisaufteilung von Fedora/Arch)
# ---------------------------------------------------------------------------------------------------
QT_URL = f"https://download.qt.io/archive/qt/{QT.rsplit('.', 1)[0]}/{QT}/submodules/"
QT_LINKS = ('cd "$DESTDIR"\ninstall -d usr/bin\n'
            '[ -f "$S/_build/user_facing_tool_links.txt" ] && while read -r l; do [ -z "$l" ] || ln -sf $l; done '
            '< "$S/_build/user_facing_tool_links.txt"\ntrue')
QTBASE_FLAGS = ("-DINSTALL_BINDIR=lib/qt6/bin -DINSTALL_PUBLICBINDIR=usr/bin -DINSTALL_LIBEXECDIR=lib/qt6 "
                "-DINSTALL_DOCDIR=share/doc/qt6 -DINSTALL_ARCHDATADIR=lib/qt6 -DINSTALL_DATADIR=share/qt6 "
                "-DINSTALL_INCLUDEDIR=include/qt6 -DINSTALL_MKSPECSDIR=lib/qt6/mkspecs "
                "-DINSTALL_EXAMPLESDIR=share/doc/qt6/examples -DINSTALL_SYSCONFDIR=/etc/xdg "
                "-DFEATURE_journald=ON -DFEATURE_openssl_linked=ON -DFEATURE_system_sqlite=ON "
                "-DFEATURE_system_xcb_xinput=ON -DFEATURE_no_direct_extern_access=ON -DFEATURE_dbus_linked=ON "
                "-DFEATURE_cups=OFF -DFEATURE_libproxy=OFF -DFEATURE_vulkan=ON -DFEATURE_xcb=ON "
                "-DFEATURE_system_harfbuzz=ON -DFEATURE_system_freetype=ON -DFEATURE_system_pcre2=ON "
                "-DFEATURE_system_zlib=ON -DFEATURE_system_png=ON -DFEATURE_system_jpeg=ON -DFEATURE_icu=ON "
                "-DQT_BUILD_TESTS=OFF -DQT_BUILD_EXAMPLES=OFF")


def qt(mod, deps, summary, flags="", mk=(), provides=()):
    pkg(f"qt6-{mod.removeprefix('qt')}", QT, f"{QT_URL}{mod}-everywhere-src-{QT}.tar.xz", summary,
        "LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only", deps, ["cmake", "ninja", "pkgconf"] + list(mk),
        style="cmake", flags=("-DQT_BUILD_TESTS=OFF -DQT_BUILD_EXAMPLES=OFF " + flags).strip(), post=QT_LINKS,
        provides=provides)


qt("qtbase", ["glibc", "gcc", "zlib", "zstd", "pcre2", "icu", "glib2", "dbus", "openssl", "sqlite", "systemd",
              "harfbuzz", "freetype", "fontconfig", "libpng", "libjpeg-turbo", "brotli", "double-conversion",
              "md4c", "libb2", "mesa", "libdrm", "libinput", "mtdev", "libxkbcommon", "libx11", "libxcb",
              "xcb-util-wm", "xcb-util-image", "xcb-util-keysyms", "xcb-util-renderutil", "xcb-util-cursor",
              "libxi", "libxrender", "libxext", "libxkbcommon", "vulkan-loader"],
   "Qt 6 Basis (Core, Gui, Widgets, Network, DBus, Sql …)", QTBASE_FLAGS, mk=["vulkan-headers", "perl", "python"],
   provides=["qt6-base", "so:libQt6Core.so.6", "so:libQt6Gui.so.6", "so:libQt6Widgets.so.6"])
PKGS[-1]["flags"] = QTBASE_FLAGS  # qtbase legt die Verzeichnisse fest, die anderen Module erben sie
qt("qtshadertools", ["qt6-base"], "Qt 6 Shader-Werkzeuge")
qt("qtsvg", ["qt6-base"], "Qt 6 SVG")
qt("qtimageformats", ["qt6-base", "libwebp", "libtiff"], "Qt 6 Bildformate (WebP, TIFF …)")
qt("qtdeclarative", ["qt6-base", "qt6-svg"], "Qt 6 QML und Qt Quick", mk=["qt6-shadertools", "python"])
qt("qtwayland", ["qt6-base", "qt6-declarative", "wayland", "libxkbcommon"], "Qt 6 Wayland", mk=["wayland-protocols"])
qt("qt5compat", ["qt6-base", "qt6-declarative", "icu"], "Qt 6 Qt5-Kompatibilitätsmodule", mk=["qt6-shadertools"])
qt("qttools", ["qt6-base", "qt6-declarative"], "Qt 6 Werkzeuge (Designer, Linguist, qdbus)",
   "-DFEATURE_clang=OFF -DFEATURE_clangcpp=OFF -DFEATURE_assistant=OFF")
qt("qttranslations", ["qt6-base"], "Qt 6 Übersetzungen", mk=["qt6-tools"])
qt("qtpositioning", ["qt6-base", "qt6-declarative"], "Qt 6 Ortung")
qt("qtsensors", ["qt6-base", "qt6-declarative"], "Qt 6 Sensoren (Bildschirmdrehung)")
qt("qtwebsockets", ["qt6-base", "qt6-declarative"], "Qt 6 WebSockets")
qt("qtwebchannel", ["qt6-base", "qt6-declarative", "qt6-websockets"], "Qt 6 WebChannel")
qt("qtmultimedia", ["qt6-base", "qt6-declarative", "qt6-shadertools", "ffmpeg", "pulseaudio", "alsa-lib", "libva"],
   "Qt 6 Multimedia (FFmpeg-Backend)", "-DFEATURE_gstreamer=OFF -DFEATURE_ffmpeg=ON -DFEATURE_pulseaudio=ON")
qt("qtspeech", ["qt6-base", "qt6-declarative", "qt6-multimedia"], "Qt 6 Sprachausgabe",
   "-DFEATURE_flite=OFF -DFEATURE_speechd=OFF")
qt("qtquicktimeline", ["qt6-base", "qt6-declarative"], "Qt 6 Quick Timeline")
qt("qtnetworkauth", ["qt6-base"], "Qt 6 OAuth")
pkg("double-conversion", "3.3.1", {"url": "https://github.com/google/double-conversion/archive/v3.3.1/double-conversion-3.3.1.tar.gz"},
    "Gleitkomma-Konvertierung", "BSD-3-Clause", ["gcc"], ["cmake"], style="cmake", flags="-DBUILD_SHARED_LIBS=ON")
pkg("md4c", "0.5.2", {"url": "https://github.com/mity/md4c/archive/release-0.5.2/md4c-0.5.2.tar.gz"},
    "Markdown-Parser", "MIT", ["glibc"], ["cmake"], style="cmake")
pkg("libb2", "0.98.1", "https://github.com/BLAKE2/libb2/releases/download/v0.98.1/libb2-0.98.1.tar.gz",
    "BLAKE2-Hash", "CC0-1.0", ["glibc"], style="autotools", flags="--enable-native=no --enable-openmp=no")

# ---------------------------------------------------------------------------------------------------
# KDE Frameworks 6
# ---------------------------------------------------------------------------------------------------
KF_URL = f"https://download.kde.org/stable/frameworks/{KF.rsplit('.', 1)[0]}/"
KDE_MK = ["extra-cmake-modules", "qt6-tools", "cmake", "ninja", "pkgconf", "gettext"]

pkg("extra-cmake-modules", KF, [KF_URL + f"extra-cmake-modules-{KF}.tar.xz",
                                nox(BLFS_PATCH + f"extra-cmake-modules-{KF}-upstream_fix-1.patch")],
    "Zusätzliche CMake-Module (KDE)", "BSD-3-Clause", ["cmake"], ["qt6-base", "qt6-tools"], arch="any",
    dir=f"extra-cmake-modules-{KF}", style="cmake",
    pre=f"patch -Np1 -i ../extra-cmake-modules-{KF}-upstream_fix-1.patch\n"
        "sed -i '/\"lib64\"/s/64//' kde-modules/KDEInstallDirsCommon.cmake\n"
        "sed -e '/PACKAGE_INIT/i set(SAVE_PACKAGE_PREFIX_DIR \"${PACKAGE_PREFIX_DIR}\")' "
        "-e '/^include/a set(PACKAGE_PREFIX_DIR \"${SAVE_PACKAGE_PREFIX_DIR}\")' -i ECMConfig.cmake.in",
    flags="-DBUILD_WITH_QT6=ON -DBUILD_HTML_DOCS=OFF -DBUILD_MAN_DOCS=OFF -DBUILD_QTHELP_DOCS=OFF")


def kde(name, version, base, deps, summary, flags="", mk=(), license="LGPL-2.1-or-later", **kw):
    pkg(name, version, f"{base}{name}-{version}.tar.xz", summary, license, list(deps), KDE_MK + list(mk),
        style="cmake", flags=(KDE_FLAGS + " " + flags).strip(), **kw)


def kf(name, deps, summary, flags="", mk=(), **kw):
    kde(name, KF, KF_URL, ["qt6-base"] + list(deps), summary, flags, mk, **kw)


QB, QD, QW, QS = "qt6-base", "qt6-declarative", "qt6-wayland", "qt6-svg"
kf("breeze-icons", [QS], "Breeze-Symbole", "-DBINARY_ICONS_RESOURCE=OFF", mk=["python"], arch="any")
kf("attica", [], "Open Collaboration Services (KNewStuff)")
kf("karchive", ["zlib", "bzip2", "xz", "zstd", "openssl"], "Archive lesen und schreiben")
kf("kcodecs", [], "Text-Kodierungen", mk=["gperf"])
kf("kconfig", [QD], "Konfigurationssystem")
kf("kcoreaddons", [QD], "Kernfunktionen (KCoreAddons)")
kf("kdbusaddons", [], "D-Bus-Hilfen")
kf("kdnssd", [], "Netzwerkdienste finden (DNS-SD)")
kf("kguiaddons", [QW, "wayland"], "GUI-Hilfen", mk=["plasma-wayland-protocols", "wayland-protocols"])
kf("ki18n", [QD, "iso-codes"], "Übersetzungen (gettext)", mk=["python"])
kf("kidletime", [QW, "wayland", "libxcb", "libxext"], "Leerlauferkennung", mk=["plasma-wayland-protocols", "wayland-protocols"])
kf("kimageformats", ["karchive", "libraw", "libavif", "libjxl"], "Zusätzliche Bildformate (PSD, XCF, RAW, EXR …)",
   "-DKIMAGEFORMATS_HEIF=OFF")
kf("kitemmodels", [QD], "Datenmodelle")
kf("kitemviews", [], "Listen- und Baumansichten")
kf("kplotting", [], "Diagramme")
kf("kwidgetsaddons", [], "Zusätzliche Bedienelemente")
kf("kwindowsystem", [QD, QW, "libx11", "libxfixes", "xcb-util-keysyms"], "Fensterverwaltung",
   mk=["plasma-wayland-protocols", "wayland-protocols"])
kf("solid", [QD, "systemd", "upower", "util-linux"], "Hardware-Erkennung", mk=["flex", "bison"])
kf("sonnet", [QD, "hunspell"], "Rechtschreibprüfung")
kf("threadweaver", [], "Nebenläufigkeit")
kf("kauth", ["kcoreaddons", "kwindowsystem", "polkit-qt6"], "Rechteverwaltung (polkit)")
kf("kcompletion", ["kconfig", "kwidgetsaddons", "kcodecs"], "Autovervollständigung")
kf("kcrash", ["kcoreaddons", "kwindowsystem"], "Absturzbehandlung")
kf("kdoctools", ["karchive", "ki18n", "libxslt", "libxml2", "docbook-xml", "docbook-xsl-nons"], "DocBook-Handbücher",
   mk=["perl-uri"])
kf("kpty", ["kcoreaddons", "ki18n"], "Pseudo-Terminals")
kf("kunitconversion", ["ki18n", "kconfig"], "Einheitenumrechnung")
kf("kcolorscheme", ["kconfig", "kguiaddons", "ki18n"], "Farbschemata")
kf("kconfigwidgets", ["kcodecs", "kconfig", "kcolorscheme", "kcoreaddons", "kguiaddons", "ki18n", "kwidgetsaddons"],
   "Einstellungs-Bedienelemente")
kf("kservice", ["kconfig", "kcoreaddons", "ki18n", "kdbusaddons"], "Dienste und Programme finden", mk=["kdoctools", "flex", "bison"])
kf("kglobalaccel", ["kconfig", "kcoreaddons", "kdbusaddons", "kwindowsystem"], "Globale Tastenkürzel")
kf("kpackage", ["karchive", "kcoreaddons", "ki18n"], "Pakete für Plasma-Erweiterungen", mk=["kdoctools"])
kf("kdesu", ["kcoreaddons", "ki18n", "kpty", "kconfig"], "Programme mit anderen Rechten starten")
kf("kiconthemes", ["karchive", "kcolorscheme", "kconfigwidgets", "ki18n", "kwidgetsaddons", "breeze-icons", QS, QD],
   "Symbolthemen")
kf("knotifications", ["kconfig", "libcanberra", QD], "Benachrichtigungen")
kf("kjobwidgets", ["kcoreaddons", "kwidgetsaddons", "knotifications"], "Fortschrittsanzeigen")
kf("ktextwidgets", ["kcompletion", "kconfig", "kconfigwidgets", "ki18n", "kwidgetsaddons", "sonnet", "kcolorscheme",
                    "qt6-speech"],
   "Textbearbeitung")
kf("kxmlgui", ["kconfig", "kconfigwidgets", "kcoreaddons", "kglobalaccel", "kguiaddons", "ki18n", "kiconthemes",
               "kitemviews", "kwidgetsaddons"], "Menüs und Werkzeugleisten")
kf("kbookmarks", ["kconfig", "kconfigwidgets", "kcoreaddons", "kwidgetsaddons", "kxmlgui"], "Lesezeichen")
kf("kwallet", ["kconfig", "kcoreaddons", "kdbusaddons", "ki18n", "knotifications", "kwidgetsaddons", "kwindowsystem",
               "kcolorscheme", "kcrash", "libgcrypt", "libsecret", "qca"], "Passwortspeicher (KWallet)", mk=["kdoctools"])
kf("kded", ["kconfig", "kcoreaddons", "kcrash", "kdbusaddons", "kservice"], "KDE-Hintergrunddienst", mk=["kdoctools"])
kf("kio", ["karchive", "kauth", "kbookmarks", "kcompletion", "kconfig", "kcoreaddons", "kcrash", "kdbusaddons",
           "kguiaddons", "ki18n", "kiconthemes", "kitemviews", "kjobwidgets", "knotifications", "kservice",
           "ktextwidgets", "kwidgetsaddons", "kwindowsystem", "solid", "kded", "kwallet", "libxml2", "libxslt",
           "acl", "util-linux"], "Netzwerk- und Dateizugriff (KIO)", mk=["kdoctools"])
kf("kdeclarative", ["kconfig", "kglobalaccel", "kguiaddons", "ki18n", "kwidgetsaddons", QD], "QML-Erweiterungen")
kf("kcmutils", ["kconfigwidgets", "kcoreaddons", "kguiaddons", "ki18n", "kio", "kitemviews", "kxmlgui", "kdeclarative"],
   "Einstellungsmodule (KCM)")
kf("kirigami", [QD, QS, "qt6-shadertools"], "Kirigami-Bedienelemente")
kf("syndication", ["kcodecs"], "RSS/Atom")
kf("knewstuff", ["attica", "karchive", "kconfig", "kcoreaddons", "ki18n", "kpackage", "kwidgetsaddons", "kirigami",
                 "syndication", "kio"], "Erweiterungen herunterladen (KNewStuff)")
kf("frameworkintegration", ["kcolorscheme", "kconfig", "kiconthemes", "knewstuff", "knotifications", "kwidgetsaddons"],
   "Integration der Frameworks in den Desktop")
kf("kparts", ["kio", "kxmlgui", "kconfig", "kcoreaddons", "ki18n", "kiconthemes", "kjobwidgets", "kservice",
              "ktextwidgets", "kwidgetsaddons"], "Eingebettete Komponenten")
kf("syntax-highlighting", [QD], "Syntaxhervorhebung", mk=["perl"])
kf("ktexteditor", ["karchive", "kconfig", "kguiaddons", "ki18n", "kio", "kparts", "sonnet", "syntax-highlighting",
                   "kauth", "kcolorscheme", QD, "editorconfig"], "Texteditor-Komponente")
kf("kcontacts", ["kcoreaddons", "ki18n", "kconfig", "kcodecs", "iso-codes"], "Kontakte (vCard)")
kf("kpeople", ["kcoreaddons", "kwidgetsaddons", "ki18n", "kitemviews", "kcontacts", QD], "Personen-Metakontakte")
kf("kfilemetadata", ["karchive", "kcoreaddons", "kconfig", "ki18n", "kcodecs", "taglib", "exiv2", "ffmpeg", "attr"],
   "Datei-Metadaten")
kf("baloo", ["kfilemetadata", "kcoreaddons", "kconfig", "kdbusaddons", "ki18n", "kidletime", "solid", "kcrash", "kio",
             "lmdb", QD], "Dateisuche (Baloo)")
kf("krunner", ["kconfig", "kcoreaddons", "ki18n", "kitemmodels", "kwindowsystem"], "Schnellstart-Suche (KRunner)")
kf("prison", [QD, "qt6-multimedia", "libqrencode", "zxing-cpp"], "Strich- und QR-Codes")
kf("qqc2-desktop-style", ["kirigami", "kcolorscheme", "kconfig", "kiconthemes", "sonnet", QD], "Qt-Quick-Stil des Desktops")
kf("kholidays", [QD], "Feiertage")
kf("purpose", ["kconfig", "kcoreaddons", "ki18n", "kio", "kirigami", "knotifications", "kservice", "prison"],
   "Teilen-Funktion")
kf("kcalendarcore", ["libical", QD], "Kalenderdaten (iCalendar)")
kf("kquickcharts", [QD, "qt6-shadertools"], "Diagramme für Qt Quick")
kf("knotifyconfig", ["kcompletion", "kconfig", "ki18n", "kio", "knotifications", "libcanberra"],
   "Einstellungen für Benachrichtigungen")
kf("kdav", ["kcoreaddons", "ki18n", "kio"], "WebDAV-Kalender und -Kontakte")
kf("kstatusnotifieritem", ["kwindowsystem"], "Symbole im Systemabschnitt der Kontrollleiste")
kf("ksvg", ["karchive", "kconfig", "kcolorscheme", "kcoreaddons", "kguiaddons", "kirigami", QS], "SVG-Themen")
kf("ktexttemplate", [QD], "Textvorlagen")
kf("kuserfeedback", [QD], "Freiwillige Nutzungsstatistik (standardmäßig aus)", mk=["qt6-tools"])

# Abhängigkeiten außerhalb der Frameworks, die Frameworks und Plasma benötigen
pkg("plasma-wayland-protocols", "1.18.0",
    "https://download.kde.org/stable/plasma-wayland-protocols/plasma-wayland-protocols-1.18.0.tar.xz",
    "Wayland-Protokolle von Plasma", "LGPL-2.1-or-later", [], ["extra-cmake-modules", "cmake"], style="cmake", arch="any")
pkg("polkit-qt6", "0.200.0", "https://download.kde.org/stable/polkit-qt-1/polkit-qt-1-0.200.0.tar.xz",
    "Qt-Anbindung für polkit", "LGPL-2.1-or-later", ["polkit", QB], ["cmake", "ninja"], style="cmake",
    flags="-DQT_MAJOR_VERSION=6 -Wno-dev")
pkg("qca", "2.3.10", "https://download.kde.org/stable/qca/2.3.10/qca-2.3.10.tar.xz", "Qt-Kryptographie (QCA)",
    "LGPL-2.1-or-later", [QB, "qt6-5compat", "openssl", "libgcrypt", "ca-certificates"], ["cmake", "ninja"], style="cmake",
    pre="sed -i 's@cert.pem@certs/ca-bundle.crt@' CMakeLists.txt",
    flags="-DQT6=ON -DBUILD_WITH_QT6=ON -DQCA_INSTALL_IN_QT_PREFIX=ON -DQCA_MAN_INSTALL_DIR:PATH=/usr/share/man "
          "-DBUILD_TESTS=OFF -DQCA_FEATURE_INSTALL_DIR=/usr/lib/qt6/mkspecs/features")
pkg("qcoro", "0.12.0", {"url": "https://github.com/danvratil/qcoro/archive/v0.12.0/qcoro-0.12.0.tar.gz"},
    "C++-Koroutinen für Qt", "MIT", [QB, QD, "qt6-websockets"], ["cmake", "ninja"], style="cmake",
    flags="-DQCORO_BUILD_EXAMPLES=OFF -DBUILD_SHARED_LIBS=ON -DUSE_QT_VERSION=6")
pkg("phonon", "4.12.0", "https://download.kde.org/stable/phonon/4.12.0/phonon-4.12.0.tar.xz", "Multimedia-Schnittstelle (Phonon)",
    "LGPL-2.1-or-later", [QB, "qt6-5compat", "glib2", "pulseaudio"], KDE_MK, style="cmake",
    flags="-DPHONON_BUILD_QT5=OFF -DPHONON_BUILD_QT6=ON -Wno-dev")
pkg("pulseaudio-qt", "1.7.0", "https://download.kde.org/stable/pulseaudio-qt/pulseaudio-qt-1.7.0.tar.xz",
    "Qt-Anbindung für PulseAudio/PipeWire", "LGPL-2.1-or-later", [QB, "pulseaudio"], KDE_MK, style="cmake",
    flags=KDE_FLAGS + " -DCMAKE_SKIP_INSTALL_RPATH=ON")
pkg("kirigami-addons", "1.9.0", "https://download.kde.org/stable/kirigami-addons/kirigami-addons-1.9.0.tar.xz",
    "Zusätzliche Kirigami-Bedienelemente", "LGPL-2.1-or-later",
    ["kirigami", "ki18n", "kconfig", "kcoreaddons", "kglobalaccel", "kguiaddons", QD], KDE_MK, style="cmake",
    flags=KDE_FLAGS)
pkg("hunspell", "1.7.2", "https://github.com/hunspell/hunspell/releases/download/v1.7.2/hunspell-1.7.2.tar.gz",
    "Rechtschreibprüfung (Hunspell)", "MPL-1.1 OR GPL-2.0-or-later OR LGPL-2.1-or-later", ["gcc", "readline", "ncurses"],
    style="autotools", flags="--with-ui --with-readline")
pkg("editorconfig", "0.12.9", {"url": "https://github.com/editorconfig/editorconfig-core-c/archive/v0.12.9/editorconfig-core-c-0.12.9.tar.gz"},
    "EditorConfig-Bibliothek", "BSD-2-Clause", ["pcre2"], ["cmake"], style="cmake", flags="-DBUILD_DOCUMENTATION=OFF")
pkg("libavif", "1.3.0", {"url": "https://github.com/AOMediaCodec/libavif/archive/v1.3.0/libavif-1.3.0.tar.gz"},
    "AVIF-Bilder", "BSD-2-Clause", ["libaom", "dav1d", "libpng", "libjpeg-turbo"], ["cmake", "ninja"], style="cmake",
    flags="-DAVIF_CODEC_AOM=SYSTEM -DAVIF_CODEC_DAV1D=SYSTEM -DAVIF_BUILD_APPS=OFF -DAVIF_LIBYUV=OFF")
pkg("highway", "1.3.0", {"url": "https://github.com/google/highway/archive/1.3.0/highway-1.3.0.tar.gz"},
    "SIMD-Bibliothek (für JPEG XL)", "Apache-2.0 OR BSD-3-Clause", ["gcc"], ["cmake", "ninja"], style="cmake",
    flags="-DBUILD_SHARED_LIBS=ON -DHWY_ENABLE_TESTS=OFF -DHWY_ENABLE_EXAMPLES=OFF -DHWY_ENABLE_CONTRIB=ON")
pkg("libjxl", "0.11.1", {"url": "https://github.com/libjxl/libjxl/archive/v0.11.1/libjxl-0.11.1.tar.gz"},
    "JPEG-XL-Bilder", "BSD-3-Clause", ["highway", "brotli", "lcms2", "libpng", "libjpeg-turbo", "giflib", "libwebp"],
    ["cmake", "ninja", "python"], style="cmake",
    flags="-DJPEGXL_ENABLE_TOOLS=ON -DJPEGXL_ENABLE_PLUGINS=OFF -DJPEGXL_ENABLE_BENCHMARK=OFF -DJPEGXL_ENABLE_EXAMPLES=OFF "
          "-DJPEGXL_ENABLE_MANPAGES=OFF -DJPEGXL_ENABLE_SJPEG=OFF -DJPEGXL_ENABLE_SKCMS=OFF -DJPEGXL_FORCE_SYSTEM_BROTLI=ON "
          "-DJPEGXL_FORCE_SYSTEM_HWY=ON -DJPEGXL_FORCE_SYSTEM_LCMS2=ON -DJPEGXL_ENABLE_JPEGLI=OFF -DBUILD_TESTING=OFF "
          "-DJPEGXL_ENABLE_OPENEXR=OFF -DJPEGXL_ENABLE_DOXYGEN=OFF")

# ---------------------------------------------------------------------------------------------------
# Plasma 6
# ---------------------------------------------------------------------------------------------------
PL_URL = f"https://download.kde.org/stable/plasma/{PLASMA}/"


def pl(name, deps, summary, flags="", mk=(), license="GPL-2.0-or-later", **kw):
    kde(name, PLASMA, PL_URL, [QB] + list(deps), summary, flags, mk, license, **kw)


KF_GUI = ["kconfig", "kcoreaddons", "ki18n", "kwidgetsaddons", "kiconthemes", "kxmlgui", "kio", "kdbusaddons",
          "kcrash", "knotifications", "kwindowsystem", "kservice", "kglobalaccel", "kcmutils", "kirigami", QD]
pl("kdecoration", ["ki18n", "kcoreaddons"], "Fensterdekorationen (Bibliothek)")
pl("libkscreen", [QW, "wayland", "libxcb", "kconfig"], "Bildschirmverwaltung (Bibliothek)",
   mk=["plasma-wayland-protocols", "wayland-protocols"])
pl("libksysguard", ["kauth", "kconfig", "kcoreaddons", "ki18n", "kiconthemes", "kio", "kpackage", "kservice",
                    "kwindowsystem", "kglobalaccel", "knewstuff", "libnl", "lm-sensors", "libpcap", "zlib", "libcap",
                    "qt6-websockets"], "Systemüberwachung (Bibliothek)")
pl("breeze", ["kdecoration", "kcolorscheme", "kconfig", "kcoreaddons", "kguiaddons", "ki18n", "kiconthemes",
              "kwindowsystem", "kcmutils", "kirigami", "frameworkintegration"], "Breeze-Stil und -Dekoration",
   "-DBUILD_QT5=OFF -DBUILD_QT6=ON")
pl("layer-shell-qt", [QW, "wayland"], "Wayland-Layer-Shell für Qt", mk=["wayland-protocols"])
pl("plasma-activities", ["kcoreaddons", "kconfig", "kwindowsystem", QD], "Aktivitäten (Bibliothek)", mk=["boost"])
pl("libplasma", ["karchive", "kconfig", "kcoreaddons", "kglobalaccel", "kguiaddons", "ki18n", "kiconthemes", "kio",
                 "kirigami", "knotifications", "kpackage", "ksvg", "kwindowsystem", "plasma-activities", "kcmutils",
                 "kdeclarative", QW, "mesa"], "Plasma-Bibliothek", mk=["plasma-wayland-protocols", "kdoctools"])
pl("kscreenlocker", ["kcrash", "kglobalaccel", "ki18n", "kidletime", "knotifications", "kservice", "kwindowsystem",
                     "kxmlgui", "libplasma", "layer-shell-qt", "linux-pam", "kcmutils", "ksvg", "libxcursor"],
   "Bildschirmsperre")
pl("kinfocenter", KF_GUI + ["kdeclarative", "libplasma", "pciutils", "mesa"], "Systeminformationen")
pl("kglobalacceld", ["kglobalaccel", "kconfig", "kcrash", "kdbusaddons", "kwindowsystem", "kio", "kservice",
                     "kjobwidgets", "xcb-util-keysyms", "libxkbcommon"], "Dienst für globale Tastenkürzel")
pl("kwayland", [QW, "wayland"], "Qt-Anbindung für Wayland (Plasma)", mk=["plasma-wayland-protocols", "wayland-protocols"],
   license="LGPL-2.1-or-later")
pl("aurorae", ["kdecoration", "libplasma", "kcmutils", "knewstuff", "ksvg", QD], "Themen-Fensterdekoration")
pl("kwin", KF_GUI + ["kdecoration", "kwayland", "libplasma", "kscreenlocker", "breeze", "kauth", "kcolorscheme",
                     "kconfigwidgets", "kguiaddons", "kidletime", "knewstuff", "kpackage", "ksvg", "plasma-activities",
                     "kglobalacceld", "libdisplay-info", "libinput", "libxkbcommon", "mesa", "libdrm", "lcms2",
                     "libepoxy", "pipewire", "libcanberra", "xwayland", "libxcvt", "hwdata", "qt6-sensors",
                     "qt6-5compat", "qt6-shadertools", "wayland", "libxcb", "xcb-util-keysyms", "xcb-util-wm",
                     "xcb-util-cursor", "libxi", "libx11", "systemd", "libcap"],
   "Fenstermanager und Wayland-Compositor (KWin)", mk=["plasma-wayland-protocols", "wayland-protocols", "kdoctools"],
   flags="-DKWIN_BUILD_X11=OFF")
pl("plasma5support", ["kconfig", "kcoreaddons", "ki18n", "kio", "kguiaddons", "kservice", "knotifications", "solid",
                      "kidletime", "libksysguard", "plasma-activities", "kholidays", "libplasma"],
   "Kompatibilität für ältere Plasma-Datenquellen")
pl("plasma-activities-stats", ["plasma-activities", "kconfig"], "Nutzungsstatistik der Aktivitäten (lokal)")
pl("kpipewire", ["pipewire", "ffmpeg", "libva", "libdrm", "mesa", "kcoreaddons", "ki18n", QD, "libepoxy"],
   "PipeWire-Bildschirmaufnahme (Plasma)", mk=["plasma-wayland-protocols", "wayland-protocols"])
pl("plasma-workspace", KF_GUI + ["libplasma", "kscreenlocker", "kwin", "layer-shell-qt", "plasma5support", "kpipewire",
                                 "libkscreen", "libksysguard", "plasma-activities", "plasma-activities-stats",
                                 "krunner", "ktexteditor", "baloo", "kfilemetadata", "kholidays", "prison",
                                 "kuserfeedback", "kpeople", "knewstuff", "kparts", "kstatusnotifieritem",
                                 "kunitconversion", "kidletime", "kdeclarative", "knotifyconfig", "kquickcharts",
                                 "ksvg", "kwallet", "phonon", "qt6-5compat", "qt6-positioning", "qt6-svg",
                                 "libcanberra", "libxcursor", "libxtst", "libsm", "libice", "libxft", "libxrender",
                                 "xrdb", "xprop", "xset", "iso-codes", "icu", "zlib", "qcoro", "kirigami-addons",
                                 "accountsservice"],
   "Plasma-Arbeitsbereich (Kontrollleiste, Anmeldung, Sitzung)", mk=["plasma-wayland-protocols", "wayland-protocols",
                                                                      "kdoctools"],
   flags="-DINSTALL_SDDM_WAYLAND_SESSION=ON -DGLIBC_LOCALE_GEN=OFF")
pl("kmenuedit", ["kxmlgui", "kio", "ki18n", "kiconthemes", "kglobalaccel", "sonnet", "kdbusaddons"], "Menü-Editor",
   mk=["kdoctools"])
pl("kscreen", ["libkscreen", "kcmutils", "kdeclarative", "ki18n", "kxmlgui", "layer-shell-qt", "libplasma",
               "plasma5support", "qt6-sensors", "ksvg", "kglobalaccel"], "Bildschirmeinstellungen")
pl("kwallet-pam", ["kwallet", "linux-pam", "libgcrypt", "socat"], "KWallet beim Anmelden öffnen")
pl("kwrited", ["kpty", "knotifications", "kdbusaddons", "kcoreaddons", "ki18n"], "Terminal-Nachrichten (wall)")
pl("milou", ["krunner", "libplasma", "kitemmodels", "ki18n", QD], "Suchoberfläche")
pl("plasma-pa", ["pulseaudio-qt", "kcoreaddons", "kdeclarative", "kglobalaccel", "ki18n", "knotifications",
                 "kstatusnotifieritem", "libcanberra", "libplasma", "pulseaudio", "ksvg", "kcmutils", "kdbusaddons"],
   "Lautstärkeregelung", mk=["kdoctools"])
pl("polkit-kde-agent-1", ["polkit-qt6", "kcoreaddons", "kcrash", "kdbusaddons", "ki18n", "kwidgetsaddons",
                          "kwindowsystem", "kiconthemes", "knotifications", QD], "Passwortabfrage für polkit")
pl("powerdevil", ["plasma-workspace", "kauth", "kcmutils", "kidletime", "knotifyconfig", "solid", "libkscreen", "upower",
                  "plasma-activities", "kirigami", "kglobalaccel", "kcrash", "libplasma"], "Energieverwaltung",
   mk=["kdoctools"])
pl("plasma-desktop", ["plasma-workspace", "kcmutils", "attica", "knewstuff", "kpeople", "libxkbfile",
                      "xkeyboard-config", "libwacom", "libinput", "accountsservice", "kirigami-addons", "kglobalacceld",
                      "plasma-activities-stats", "baloo", "krunner", "kitemmodels", "qqc2-desktop-style", "ksvg",
                      "libcanberra", "kpipewire", "xf86-input-libinput", "xf86-input-evdev", "xorg-server",
                      "libxi", "libxcursor", "libx11", "polkit-kde-agent-1", "breeze", "kmenuedit", "kscreen",
                      "powerdevil", "plasma-pa", "kde-cli-tools", "systemsettings", "kactivitymanagerd",
                      "plasma-integration", "xdg-desktop-portal-kde", "milou", "kinfocenter"],
   "Plasma-Desktop", mk=["kdoctools", "wayland-protocols", "plasma-wayland-protocols", "boost"])
# Die Plasma-Komponenten werden vor plasma-desktop gebaut; plasma-desktop zieht sie als Laufzeitabhängigkeit nach
pl("ksshaskpass", ["kwallet", "kcoreaddons", "ki18n", "kwidgetsaddons"], "SSH-Passwortabfrage über KWallet",
   mk=["kdoctools"])
pl("sddm-kcm", ["kauth", "kcmutils", "kconfig", "kcoreaddons", "ki18n", "kio", "knewstuff", "kxmlgui", "kirigami"],
   "Einstellungsmodul für SDDM")
pl("kactivitymanagerd", ["plasma-activities", "kio", "kglobalaccel", "kxmlgui", "kcrash", "kconfig", "kcoreaddons",
                         "ki18n", "kwindowsystem"], "Aktivitäten-Dienst", mk=["boost"])
pl("plasma-integration", ["breeze", "frameworkintegration", "kio", "kiconthemes", "kxmlgui", "kwayland",
                          "qqc2-desktop-style", "kstatusnotifieritem", QW, "kguiaddons", "kcolorscheme"],
   "Integration von Qt-Programmen in Plasma", "-DBUILD_QT5=OFF -DBUILD_QT6=ON",
   mk=["plasma-wayland-protocols", "wayland-protocols"], license="LGPL-2.1-or-later")
pl("xdg-desktop-portal-kde", ["kwayland", "kio", "kirigami", "kglobalaccel", "knotifications", "kstatusnotifieritem",
                              "kwindowsystem", "libplasma", QW, "libxkbcommon", "xdg-desktop-portal", "kiconthemes",
                              "kcoreaddons", "ki18n", "kservice", "kwidgetsaddons"],
   "Portale für Plasma (Dateidialog, Bildschirmfreigabe)", mk=["plasma-wayland-protocols", "wayland-protocols"],
   license="LGPL-2.0-or-later")
pl("kde-cli-tools", ["kcmutils", "kconfig", "kdesu", "ki18n", "kiconthemes", "kio", "kservice", "kwindowsystem",
                     "plasma-activities", "plasma-workspace", "kparts"], "Kommandozeilenwerkzeuge (kde-open …)",
   mk=["kdoctools"])
pl("systemsettings", KF_GUI + ["kitemmodels", "kitemviews", "krunner", "plasma-activities", "plasma-workspace"],
   "Systemeinstellungen", mk=["kdoctools"])
pl("plasma-systemmonitor", ["libksysguard", "kquickcharts", "kirigami-addons", "kitemmodels", "knewstuff",
                            "kdeclarative", "qqc2-desktop-style", "kglobalaccel", "kio", QD], "Systemmonitor")
pl("qqc2-breeze-style", ["kirigami", "kcolorscheme", "kconfig", "kguiaddons", "kiconthemes", "kquickcharts", QD],
   "Breeze-Stil für Qt Quick", license="LGPL-2.1-or-later")
pl("ksystemstats", ["libksysguard", "kcoreaddons", "ki18n", "kio", "solid", "libnl", "lm-sensors", "kdbusaddons",
                    "kcrash"], "Systemstatistik-Dienst")
pl("kdeplasma-addons", ["plasma-workspace", "libplasma", "kholidays", "knewstuff", "kunitconversion", "krunner",
                        "libqalculate", "kirigami-addons", "purpose", "icu", "qt6-5compat"],
   "Zusätzliche Plasma-Miniprogramme")
pl("ocean-sound-theme", [], "Ocean-Systemklänge", arch="any", license="CC-BY-SA-4.0")

pkg("socat", "1.8.0.3", "http://www.dest-unreach.org/socat/download/socat-1.8.0.3.tar.gz", "Datenkanäle (für pam_kwallet)",
    "GPL-2.0-only", ["openssl", "readline"], style="autotools")

# ---------------------------------------------------------------------------------------------------
# Anmeldung (SDDM mit Wayland-Begrüßung über KWin)
# ---------------------------------------------------------------------------------------------------
pkg("sddm", "0.21.0", {"url": "https://github.com/sddm/sddm/archive/v0.21.0/sddm-0.21.0.tar.gz"},
    "Anmeldebildschirm (SDDM)", "GPL-2.0-or-later",
    [QB, QD, "linux-pam", "systemd", "libxcb", "libxau", "upower", "xauth", "xkeyboard-config"],
    ["extra-cmake-modules", "qt6-tools", "cmake", "ninja", "python-docutils"], style="cmake",
    flags="-DRUNTIME_DIR=/run/sddm -DBUILD_MAN_PAGES=OFF -DBUILD_WITH_QT6=ON -DDATA_INSTALL_DIR=/usr/share/sddm "
          "-DDBUS_CONFIG_FILENAME=sddm_org.freedesktop.DisplayManager.conf -DSYSTEMD_TMPFILES_DIR=/usr/lib/tmpfiles.d "
          "-DSYSTEMD_SYSUSERS_DIR=/usr/lib/sysusers.d -DUID_MIN=1000 -DUID_MAX=60513 -DNO_SYSTEMD=OFF "
          "-DCMAKE_INSTALL_SYSCONFDIR=/etc",
    post=r'''# PAM wie bei Fedora (system-auth), nicht die Vorgaben von SDDM (system-login)
install -d "$DESTDIR/etc/pam.d"
cat > "$DESTDIR/etc/pam.d/sddm" <<'PAM'
auth       requisite    pam_nologin.so
auth       include      system-auth
-auth      optional     pam_kwallet5.so
account    required     pam_access.so
account    include      system-auth
password   include      system-auth
session    required     pam_selinux.so close
session    required     pam_loginuid.so
session    include      system-auth
session    required     pam_selinux.so open
-session   optional     pam_kwallet5.so auto_start
PAM
cat > "$DESTDIR/etc/pam.d/sddm-autologin" <<'PAM'
auth       requisite    pam_nologin.so
auth       required     pam_permit.so
account    include      system-auth
password   include      system-auth
session    required     pam_selinux.so close
session    required     pam_loginuid.so
session    include      system-auth
session    required     pam_selinux.so open
PAM
cat > "$DESTDIR/etc/pam.d/sddm-greeter" <<'PAM'
auth       required     pam_env.so
auth       required     pam_permit.so
account    required     pam_permit.so
password   required     pam_deny.so
session    required     pam_unix.so
-session   optional     pam_systemd.so
PAM
[ -f "$DESTDIR/usr/lib/sysusers.d/sddm.conf" ] || install -Dm644 /dev/stdin "$DESTDIR/usr/lib/sysusers.d/sddm.conf" <<'SYS'
u sddm - "SDDM Greeter" /var/lib/sddm /usr/bin/nologin
SYS
[ -f "$DESTDIR/usr/lib/tmpfiles.d/sddm.conf" ] || install -Dm644 /dev/stdin "$DESTDIR/usr/lib/tmpfiles.d/sddm.conf" <<'TMP'
d /var/lib/sddm 0750 sddm sddm -
TMP''', backup=["etc/pam.d/sddm", "etc/pam.d/sddm-autologin", "etc/pam.d/sddm-greeter"])

# ---------------------------------------------------------------------------------------------------
# Programme (KDE Gear)
# ---------------------------------------------------------------------------------------------------
GEAR_URL = f"https://download.kde.org/stable/release-service/{GEAR}/src/"


def gear(name, deps, summary, flags="", mk=(), license="GPL-2.0-or-later", **kw):
    kde(name, GEAR, GEAR_URL, [QB] + list(deps), summary, flags, mk, license, **kw)


gear("konsole", KF_GUI + ["kbookmarks", "kconfigwidgets", "kguiaddons", "knewstuff", "knotifyconfig", "kparts", "kpty",
                          "ktextwidgets", "icu", "qt6-5compat"], "Terminal", mk=["kdoctools"])
gear("dolphin", KF_GUI + ["kbookmarks", "kcompletion", "kfilemetadata", "baloo", "kparts", "knewstuff", "kuserfeedback",
                          "solid", "phonon", "plasma-activities", "ktextwidgets", "kcolorscheme"], "Dateiverwaltung",
     mk=["kdoctools"])
gear("kate", KF_GUI + ["ktexteditor", "kitemmodels", "knewstuff", "kuserfeedback", "plasma-activities", "kparts",
                       "syntax-highlighting", "ktextwidgets"], "Texteditor (Kate und KWrite)", mk=["kdoctools"],
     license="LGPL-2.0-or-later")
gear("ark", KF_GUI + ["libarchive", "kparts", "kpty", "kfilemetadata", "libzip", "xz", "zstd"], "Archivverwaltung",
     mk=["kdoctools"])
gear("kcalc", KF_GUI + ["gmp", "mpfr", "kguiaddons", "knotifications"], "Taschenrechner", mk=["kdoctools"])
gear("gwenview", KF_GUI + ["exiv2", "kimageannotator", "lcms2", "baloo", "kparts", "purpose", "libtiff", "libpng",
                           "libjpeg-turbo", "plasma-activities", "kimageformats", "qt6-imageformats", "wayland",
                           "qt6-wayland"],
     "Bildbetrachter", mk=["kdoctools", "wayland-protocols", "plasma-wayland-protocols"])
gear("kcharselect", KF_GUI + ["kbookmarks"], "Sonderzeichen auswählen", mk=["kdoctools"])
pkg("libzip", "1.11.4", "https://libzip.org/download/libzip-1.11.4.tar.xz", "ZIP-Bibliothek", "BSD-3-Clause",
    ["zlib", "bzip2", "xz", "zstd", "openssl"], ["cmake", "ninja"], style="cmake",
    flags="-DBUILD_EXAMPLES=OFF -DBUILD_REGRESS=OFF -DBUILD_DOC=OFF -DENABLE_GNUTLS=OFF -DENABLE_MBEDTLS=OFF")
pkg("kcolorpicker", "0.3.1", {"url": "https://github.com/ksnip/kColorPicker/archive/v0.3.1/kColorPicker-0.3.1.tar.gz"},
    "Farbwähler (Qt)", "LGPL-3.0-or-later", [QB], ["cmake", "ninja"], style="cmake",
    flags="-DBUILD_WITH_QT6=ON -DBUILD_SHARED_LIBS=ON")
pkg("kimageannotator", "0.7.1", {"url": "https://github.com/ksnip/kImageAnnotator/archive/v0.7.1/kImageAnnotator-0.7.1.tar.gz"},
    "Bildanmerkungen (Qt)", "LGPL-3.0-or-later", [QB, QS, "kcolorpicker"], ["cmake", "ninja", "qt6-tools"],
    style="cmake", flags="-DBUILD_WITH_QT6=ON -DBUILD_SHARED_LIBS=ON")

# ---------------------------------------------------------------------------------------------------
# Schriften
# ---------------------------------------------------------------------------------------------------
FONT_PKG = '''cd "$SRCDIR"
find . -type f \\( -name '*.ttf' -o -name '*.ttc' -o -name '*.otf' \\) {filter} -print0 |
  while IFS= read -r -d '' f; do install -Dm644 "$f" "$DESTDIR/usr/share/fonts/{dir}/$(basename "$f")"; done
'''
pkg("inter-font", "4.1", {"url": "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"},
    "Schrift Inter (Oberfläche)", "OFL-1.1", ["fontconfig"], arch="any", build=":",
    package=FONT_PKG.format(filter="-not -path '*/web/*' -not -path '*/extras/*'", dir="inter"))
pkg("jetbrains-mono-font", "2.304", {"url": "https://github.com/JetBrains/JetBrainsMono/releases/download/v2.304/JetBrainsMono-2.304.zip"},
    "Schrift JetBrains Mono (Terminal, Code)", "OFL-1.1", ["fontconfig"], arch="any", build=":",
    package=FONT_PKG.format(filter="-path '*/fonts/ttf/*'", dir="jetbrains-mono"))
pkg("dejavu-fonts", "2.37", "https://github.com/dejavu-fonts/dejavu-fonts/releases/download/version_2_37/dejavu-fonts-ttf-2.37.tar.bz2",
    "Schriften DejaVu (große Zeichenabdeckung)", "Bitstream-Vera", ["fontconfig"], arch="any", build=":",
    package=FONT_PKG.format(filter="", dir="dejavu") +
            'install -d "$DESTDIR/usr/share/fontconfig/conf.avail"\n'
            'cp dejavu-fonts-ttf-2.37/fontconfig/*.conf "$DESTDIR/usr/share/fontconfig/conf.avail/"')
pkg("noto-color-emoji", "2.047", {"url": "https://github.com/googlefonts/noto-emoji/raw/v2.047/fonts/NotoColorEmoji.ttf",
                                  "filename": "NotoColorEmoji-2.047.ttf"},
    "Farbige Emoji (Noto)", "OFL-1.1", ["fontconfig"], arch="any", build=":",
    package='install -Dm644 "$SRCDIR/NotoColorEmoji-2.047.ttf" "$DESTDIR/usr/share/fonts/noto/NotoColorEmoji.ttf"')

# ---------------------------------------------------------------------------------------------------
# symfonOS: Desktop-Voreinstellungen und Metapaket
# ---------------------------------------------------------------------------------------------------
pkg("symfonos-desktop-config", "1.0", {"path": "@/desktop"},
    "symfonOS-Desktop: Farbschemata, globales Design, SDDM (Wayland), PAM, Voreinstellungen", "GPL-3.0-or-later",
    ["plasma-workspace", "sddm", "inter-font", "jetbrains-mono-font", "breeze-icons"], arch="any", build=":",
    package=r'''cd "$SRCDIR/desktop"
install -d "$DESTDIR/usr/share/color-schemes" "$DESTDIR/usr/share/plasma/look-and-feel" "$DESTDIR/etc/xdg/plasma-workspace/env"
cp color-schemes/*.colors "$DESTDIR/usr/share/color-schemes/"
cp -r look-and-feel/. "$DESTDIR/usr/share/plasma/look-and-feel/"
install -m644 xdg/kdeglobals xdg/kwinrc "$DESTDIR/etc/xdg/"
install -m644 xdg/plasma-workspace-env-symfonos.sh "$DESTDIR/etc/xdg/plasma-workspace/env/"
install -Dm644 branding/symfonos.svg "$DESTDIR/usr/share/icons/hicolor/scalable/apps/symfonos.svg"
install -Dm644 sddm/10-symfonos.conf "$DESTDIR/usr/lib/sddm/sddm.conf.d/10-symfonos.conf"
for f in pam.d/*; do install -Dm644 "$f" "$DESTDIR/etc/$f"; done
install -Dm644 /dev/stdin "$DESTDIR/usr/lib/systemd/system-preset/40-symfonos-desktop.preset" <<'PRE'
enable sddm.service
enable accounts-daemon.service
enable upower.service
PRE
install -d "$DESTDIR/etc/systemd/system"
ln -s /usr/lib/systemd/system/graphical.target "$DESTDIR/etc/systemd/system/default.target"
''', backup=["etc/xdg/kdeglobals", "etc/xdg/kwinrc", "etc/pam.d/kde"])

DESKTOP = ["symfonos-base", "symfonos-desktop-config", "plasma-desktop", "plasma-workspace", "kwin", "xwayland",
           "sddm", "sddm-kcm", "breeze", "breeze-icons", "qqc2-breeze-style", "qqc2-desktop-style",
           "plasma-integration", "xdg-desktop-portal-kde", "kscreen", "powerdevil", "plasma-pa", "kinfocenter",
           "systemsettings", "kde-cli-tools", "kmenuedit", "kwallet-pam", "ksshaskpass", "kwrited", "milou",
           "polkit-kde-agent-1", "plasma-systemmonitor", "ksystemstats", "kdeplasma-addons", "ocean-sound-theme",
           "kactivitymanagerd", "kglobalacceld", "kimageformats", "qt6-imageformats", "qt6-translations",
           "qt6-wayland", "mesa", "vulkan-loader", "libva", "pipewire", "wireplumber", "alsa-utils", "ffmpeg",
           "xdg-user-dirs", "konsole", "dolphin", "kate", "ark", "gwenview", "kcalc", "kcharselect",
           "inter-font", "jetbrains-mono-font", "dejavu-fonts", "noto-color-emoji", "hunspell", "upower",
           "accountsservice", "polkit"]
pkg("symfonos-desktop", "2025.10", [], "symfonOS-Desktop (KDE Plasma 6, Wayland, PipeWire)", "GPL-3.0-or-later",
    DESKTOP, arch="any", style="meta")

# ---------------------------------------------------------------------------------------------------
# Rezepte schreiben
# ---------------------------------------------------------------------------------------------------


def q(s: str) -> str:
    """TOML-Zeichenkette: einzeilig mit "…", mehrzeilig als '''…''' (Literal, keine Escapes)."""
    if "\n" in s or '"' in s or "\\" in s:
        if "'''" in s:
            raise ValueError("''' in Skript")
        return "'''\n" + s.strip("\n") + "\n'''"
    return '"' + s + '"'


def arr(xs) -> str:
    return "[" + ", ".join(q(x) for x in xs) + "]"


def old_sums(path: Path) -> dict:
    if not path.exists():
        return {}
    sums, url = {}, None
    for line in path.read_text().splitlines():
        m = re.match(r'\s*url\s*=\s*"([^"]+)"', line)
        if m:
            url = m.group(1)
        m = re.match(r'\s*sha256\s*=\s*"([^"]+)"', line)
        if m and url:
            sums[url] = m.group(1)
    return sums


def render(p: dict, sums: dict) -> str:
    L = ["# symfonOS-Rezept – Format: siehe docs/PAKETFORMAT.md", GENERATED, "[package]",
         f"name = {q(p['name'])}", f"version = {q(p['version'])}"]
    if p["arch"]:
        L.append(f"arch = {q(p['arch'])}")
    L += [f"summary = {q(p['summary'])}", f"license = {q(p['license'])}"]
    urls = [s["url"] if isinstance(s, dict) and "url" in s else s for s in p["sources"] if not isinstance(s, dict) or "url" in s]
    if p["url"] or urls:
        L.append(f"url = {q(p['url'] or urls[0].rsplit('/', 1)[0] + '/')}")
    deps = [d for d in dict.fromkeys(p["depends"]) if d != p["name"]]
    mk = [d for d in dict.fromkeys(p["makedepends"]) if d != p["name"] and d not in deps]
    if deps:
        L.append(f"depends = {arr(deps)}")
    if mk:
        L.append(f"makedepends = {arr(mk)}")
    if p["provides"]:
        L.append(f"provides = {arr(p['provides'])}")
    if p["backup"]:
        L.append(f"backup = {arr(p['backup'])}")
    for s in p["sources"]:
        s = {"url": s} if isinstance(s, str) else dict(s)
        L.append("")
        L.append("[[source]]")
        if "url" in s:
            L.append(f"url = {q(s['url'])}")
            L.append(f"sha256 = {q(sums.get(s['url'], 'TODO'))}")
            if "filename" in s:
                L.append(f"filename = {q(s['filename'])}")
            if s.get("extract") is False:
                L.append("extract = false")
        else:
            L.append(f"path = {q(s['path'])}")
    L += ["", "[build]"]
    if p["dir"]:
        L.append(f"dir = {q(p['dir'])}")
    if p["style"]:
        L.append(f"style = {q(p['style'])}")
        if p["flags"]:
            L.append(f"configure_flags = {q(p['flags'])}")
        if p["mf"]:
            L.append(f"make_flags = {q(p['mf'])}")
        if p["pre"]:
            L.append(f"pre_configure = {q(p['pre'])}")
        if p["post"]:
            L.append(f"post_install = {q(p['post'])}")
    elif p["pre"]:
        L.append("prepare = " + q('cd "$S"\n' + p["pre"]))
    for step in ("build", "package"):
        if p[step] is not None:
            L.append(f"{step} = {q(p[step])}")
    if p["style"] is None and p["build"] is None:
        raise SystemExit(f"{p['name']}: weder style noch build")
    opts = dict(p["options"])
    if opts:
        L += ["", "[options]"]
        for k, v in opts.items():
            L.append(f"{k} = {arr(v) if isinstance(v, list) else q(v) if isinstance(v, str) else str(v).lower()}")
    if p["scripts"]:
        L += ["", "[scripts]"] + [f"{k} = {q(v)}" for k, v in p["scripts"].items()]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="nur prüfen (Exit 1, wenn ein Rezept abweicht)")
    ap.add_argument("--list", action="store_true", help="Paketnamen ausgeben")
    a = ap.parse_args()
    names = [p["name"] for p in PKGS]
    dup = {n for n in names if names.count(n) > 1}
    if dup:
        raise SystemExit(f"doppelt: {sorted(dup)}")
    if a.list:
        print("\n".join(names))
        return 0
    known = {d.name for d in RECIPES.iterdir() if d.is_dir()} | set(names)
    provided = {x.split("=")[0] for p in PKGS for x in p["provides"]}
    for f in RECIPES.glob("*/SYPBUILD.toml"):
        m = re.search(r"^provides = \[(.*)\]", f.read_text(), re.M)
        if m:
            provided |= set(re.findall(r'"([^"]+)"', m.group(1)))
    missing = sorted({d for p in PKGS for d in p["depends"] + p["makedepends"]
                      if d not in known and d not in provided})
    if missing:
        raise SystemExit(f"unbekannte Abhängigkeiten: {missing}")
    changed = 0
    for p in PKGS:
        path = RECIPES / p["name"] / "SYPBUILD.toml"
        if path.exists() and GENERATED not in path.read_text():
            print(f"übersprungen (von Hand gepflegt): {p['name']}", file=sys.stderr)
            continue
        text = render(p, old_sums(path))
        if path.exists() and path.read_text() == text:
            continue
        changed += 1
        if a.check:
            print(f"veraltet: {path.relative_to(ROOT)}")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    print(f"{len(PKGS)} Rezepte, {changed} {'abweichend' if a.check else 'geschrieben'}", file=sys.stderr)
    return 1 if a.check and changed else 0


if __name__ == "__main__":
    sys.exit(main())

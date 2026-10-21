#!/usr/bin/env bash
# Stufe 2 (LFS Kap. 6): temporäre Werkzeuge per Cross-Compiler nach $SYM
# shellcheck source-path=SCRIPTDIR source=config.sh
. "$(dirname "$0")/config.sh"
GCCV=$(version_of gcc)
_std() {
  local r=$1; shift
  unpack "$r"
  if declare -F "pre_$r" >/dev/null; then "pre_$r"; fi
  local guess; guess=$( [[ -x build-aux/config.guess ]] && echo build-aux/config.guess || echo ./config.guess )
  ./configure --prefix=/usr --host=$TGT --build="$($guess)" "$@"
  make
  make DESTDIR="$SYM" install
}
std() { step "tmp-$1" "$1 (temporär)" _std "$@"; }  # std <rezept> [configure-Optionen…]
std m4
_tmp_ncurses() {
  unpack ncurses
  mkdir -p build; cd build; ../configure AWK=gawk; make -C include; make -C progs tic; cd ..
  ./configure --prefix=/usr --host=$TGT --build="$(./config.guess)" --mandir=/usr/share/man \
  --with-manpage-format=normal --with-shared --without-normal --with-cxx-shared --without-debug \
  --without-ada --disable-stripping AWK=gawk
  make; make DESTDIR="$SYM" TIC_PATH="$(pwd)/build/progs/tic" install
  ln -sf libncursesw.so "$SYM/usr/lib/libncurses.so"
  sed -e 's/^#if.*XOPEN.*$/#if 1/' -i "$SYM/usr/include/curses.h"
}
step tmp-ncurses "ncurses (temporär)" _tmp_ncurses
_tmp_bash() {
  unpack bash
  ./configure --prefix=/usr --build="$(sh support/config.guess)" --host=$TGT --without-bash-malloc
  make; make DESTDIR="$SYM" install; ln -sf bash "$SYM/usr/bin/sh"
}
step tmp-bash "bash (temporär)" _tmp_bash
std coreutils --enable-install-program=hostname --enable-no-install-program=kill,uptime
[[ -e "$SYM/usr/bin/chroot" ]] && mv "$SYM/usr/bin/chroot" "$SYM/usr/sbin/chroot"
std diffutils gl_cv_func_strcasecmp_works=y
_tmp_file() {
  unpack file
  mkdir -p build; cd build
  ../configure --disable-bzlib --disable-libseccomp --disable-xzlib --disable-zlib; make; cd ..
  ./configure --prefix=/usr --host=$TGT --build="$(./config.guess)"
  make FILE_COMPILE="$(pwd)/build/src/file"; make DESTDIR="$SYM" install
  rm -f "$SYM/usr/lib/libmagic.la"
}
step tmp-file "file (temporär)" _tmp_file
std findutils --localstatedir=/var/lib/locate
pre_gawk() { sed -i 's/extras//' Makefile.in; }
std gawk
std grep
std gzip
std make
std patch
std sed
std tar
std xz --disable-static --docdir=/usr/share/doc/xz
rm -f "$SYM/usr/lib/liblzma.la"
_tmp_binutils() {
  unpack binutils; mkdir -p build; cd build
  ../configure --prefix=/usr --build="$(../config.guess)" --host=$TGT --disable-nls --enable-shared \
  --enable-gprofng=no --disable-werror --enable-64-bit-bfd --enable-new-dtags --enable-default-hash-style=gnu
  make; make DESTDIR="$SYM" install
  rm -f "$SYM"/usr/lib/lib{bfd,ctf,ctf-nobfd,opcodes,sframe}.{a,la}
}
step tmp-binutils "binutils (Durchgang 2)" _tmp_binutils
_tmp_gcc() {
  unpack gcc
  for p in mpfr gmp mpc; do f=$(src_path $p); tar -xf "$f"; mv "$p-$(version_of $p)" "$p"; done
  sed -e '/m64=/s/lib64/lib/' -i.orig gcc/config/i386/t-linux64
  sed '/thread_header =/s/@.*@/gthr-posix.h/' -i libgcc/Makefile.in libstdc++-v3/include/Makefile.in
  mkdir -p build; cd build
  ../configure --build="$(../config.guess)" --host=$TGT --target=$TGT LDFLAGS_FOR_TARGET="-L$PWD/$TGT/libgcc" \
  --prefix=/usr --with-build-sysroot="$SYM" --enable-default-pie --enable-default-ssp --disable-nls \
  --disable-multilib --disable-libatomic --disable-libgomp --disable-libquadmath --disable-libsanitizer \
  --disable-libssp --disable-libvtv --enable-languages=c,c++
  make; make DESTDIR="$SYM" install
  ln -sf gcc "$SYM/usr/bin/cc"
}
step tmp-gcc "gcc (Durchgang 2)" _tmp_gcc
log "Temporäre Werkzeuge fertig (gcc $GCCV)"

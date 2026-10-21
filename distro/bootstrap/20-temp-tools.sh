#!/usr/bin/env bash
# Stufe 2 (LFS Kap. 6): temporäre Werkzeuge per Cross-Compiler nach $SYM
# shellcheck source=config.sh
. "$(dirname "$0")/config.sh"
GCCV=$(version_of gcc)
std() {  # std <rezept> [configure-Optionen…]
  local r=$1; shift
  stamp "tmp-$r" && return 0
  log "$r (temporär)"; unpack "$r"
  if declare -F "pre_$r" >/dev/null; then "pre_$r"; fi
  local guess; guess=$( [[ -x build-aux/config.guess ]] && echo build-aux/config.guess || echo ./config.guess )
  ./configure --prefix=/usr --host=$TGT --build="$($guess)" "$@"
  make && make DESTDIR="$SYM" install
  mark "tmp-$r"
}
std m4
stamp tmp-ncurses || { log "ncurses (temporär)"; unpack ncurses
  mkdir -p build; pushd build; ../configure AWK=gawk; make -C include; make -C progs tic; popd
  ./configure --prefix=/usr --host=$TGT --build="$(./config.guess)" --mandir=/usr/share/man \
    --with-manpage-format=normal --with-shared --without-normal --with-cxx-shared --without-debug \
    --without-ada --disable-stripping AWK=gawk
  make && make DESTDIR="$SYM" TIC_PATH="$(pwd)/build/progs/tic" install
  ln -sf libncursesw.so "$SYM/usr/lib/libncurses.so"
  sed -e 's/^#if.*XOPEN.*$/#if 1/' -i "$SYM/usr/include/curses.h"; mark tmp-ncurses; }
stamp tmp-bash || { log "bash (temporär)"; unpack bash
  ./configure --prefix=/usr --build="$(sh support/config.guess)" --host=$TGT --without-bash-malloc
  make && make DESTDIR="$SYM" install; ln -sf bash "$SYM/usr/bin/sh"; mark tmp-bash; }
std coreutils --enable-install-program=hostname --enable-no-install-program=kill,uptime
[[ -e "$SYM/usr/bin/chroot" ]] && mv "$SYM/usr/bin/chroot" "$SYM/usr/sbin/chroot"
std diffutils gl_cv_func_strcasecmp_works=y
stamp tmp-file || { log "file (temporär)"; unpack file
  mkdir -p build; pushd build
  ../configure --disable-bzlib --disable-libseccomp --disable-xzlib --disable-zlib; make; popd
  ./configure --prefix=/usr --host=$TGT --build="$(./config.guess)"
  make FILE_COMPILE="$(pwd)/build/src/file" && make DESTDIR="$SYM" install
  rm -f "$SYM/usr/lib/libmagic.la"; mark tmp-file; }
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
stamp tmp-binutils || { log "binutils (Durchgang 2)"; unpack binutils; mkdir -p build && cd build
  ../configure --prefix=/usr --build="$(../config.guess)" --host=$TGT --disable-nls --enable-shared \
    --enable-gprofng=no --disable-werror --enable-64-bit-bfd --enable-new-dtags --enable-default-hash-style=gnu
  make && make DESTDIR="$SYM" install
  rm -f "$SYM"/usr/lib/lib{bfd,ctf,ctf-nobfd,opcodes,sframe}.{a,la}; mark tmp-binutils; }
stamp tmp-gcc || { log "gcc (Durchgang 2)"; unpack gcc
  for p in mpfr gmp mpc; do f=$(src_path $p); tar -xf "$f"; mv "$p-$(version_of $p)" "$p"; done
  sed -e '/m64=/s/lib64/lib/' -i.orig gcc/config/i386/t-linux64
  sed '/thread_header =/s/@.*@/gthr-posix.h/' -i libgcc/Makefile.in libstdc++-v3/include/Makefile.in
  mkdir -p build && cd build
  ../configure --build="$(../config.guess)" --host=$TGT --target=$TGT LDFLAGS_FOR_TARGET="-L$PWD/$TGT/libgcc" \
    --prefix=/usr --with-build-sysroot="$SYM" --enable-default-pie --enable-default-ssp --disable-nls \
    --disable-multilib --disable-libatomic --disable-libgomp --disable-libquadmath --disable-libsanitizer \
    --disable-libssp --disable-libvtv --enable-languages=c,c++
  make && make DESTDIR="$SYM" install
  ln -sf gcc "$SYM/usr/bin/cc"; mark tmp-gcc; }
log "Temporäre Werkzeuge fertig (gcc $GCCV)"

#!/usr/bin/env bash
# Stufe 1 (LFS Kap. 5): Cross-Werkzeugkette nach $SYM/tools
# shellcheck source=config.sh
. "$(dirname "$0")/config.sh"
GCCV=$(version_of gcc); GLIBCV=$(version_of glibc)

stamp binutils-pass1 || { log "binutils (Durchgang 1)"; unpack binutils; mkdir -p build && cd build
  ../configure --prefix="$SYM/tools" --with-sysroot="$SYM" --target=$TGT --disable-nls \
    --enable-gprofng=no --disable-werror --enable-new-dtags --enable-default-hash-style=gnu
  make && make install; mark binutils-pass1; }

prep_gcc() {
  unpack gcc
  for p in mpfr gmp mpc; do
    local f; f=$(src_path $p); tar -xf "$f"; mv -v "$p-$(version_of $p)" "$p"
  done
  sed -e '/m64=/s/lib64/lib/' -i.orig gcc/config/i386/t-linux64
}

stamp gcc-pass1 || { log "gcc (Durchgang 1)"; prep_gcc; mkdir -p build && cd build
  ../configure --target=$TGT --prefix="$SYM/tools" --with-glibc-version="$GLIBCV" --with-sysroot="$SYM" \
    --with-newlib --without-headers --enable-default-pie --enable-default-ssp --disable-nls --disable-shared \
    --disable-multilib --disable-threads --disable-libatomic --disable-libgomp --disable-libquadmath \
    --disable-libssp --disable-libvtv --disable-libstdcxx --enable-languages=c,c++
  make && make install; cd ..
  cat gcc/limitx.h gcc/glimits.h gcc/limity.h > "$(dirname "$($TGT-gcc -print-libgcc-file-name)")/include/limits.h"
  mark gcc-pass1; }

stamp linux-headers || { log "Linux-API-Header"; unpack linux-api-headers
  make mrproper && make headers
  find usr/include -type f ! -name '*.h' -delete
  cp -r usr/include "$SYM/usr"; mark linux-headers; }

stamp glibc || { log "glibc"; unpack glibc
  patch -Np1 -i "$(src_path glibc 2)"
  mkdir -p build && cd build
  echo "rootsbindir=/usr/sbin" > configparms
  ../configure --prefix=/usr --host=$TGT --build="$(../scripts/config.guess)" --disable-nscd \
    libc_cv_slibdir=/usr/lib --enable-kernel=5.4
  make && make DESTDIR="$SYM" install
  sed '/RTLDLIST=/s@/usr@@g' -i "$SYM/usr/bin/ldd"
  echo 'int main(){}' | $TGT-gcc -xc - -o /tmp/symfonos-dummy
  readelf -l /tmp/symfonos-dummy | grep -q '/lib64/ld-linux-x86-64.so.2' || { echo "glibc-Prüfung fehlgeschlagen" >&2; exit 1; }
  rm -f /tmp/symfonos-dummy; mark glibc; }

stamp libstdcxx || { log "libstdc++"; unpack gcc; mkdir -p build && cd build
  ../libstdc++-v3/configure --host=$TGT --build="$(../config.guess)" --prefix=/usr --disable-multilib \
    --disable-nls --disable-libstdcxx-pch --with-gxx-include-dir="/tools/$TGT/include/c++/$GCCV"
  make && make DESTDIR="$SYM" install
  rm -f "$SYM"/usr/lib/lib{stdc++{,exp,fs},supc++}.la; mark libstdcxx; }
log "Cross-Werkzeugkette fertig"

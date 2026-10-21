#!/usr/bin/env bash
# Stufe 3 (LFS Kap. 7): in die neue Wurzel wechseln und letzte temporäre Werkzeuge bauen
# shellcheck source=config.sh
. "$(dirname "$0")/config.sh"
mount_vfs() {
  mkdir -p "$SYM"/{dev,proc,sys,run}
  mountpoint -q "$SYM/dev" || mount --bind /dev "$SYM/dev"
  mountpoint -q "$SYM/dev/pts" || mount -t devpts devpts -o gid=5,mode=0620 "$SYM/dev/pts"
  mountpoint -q "$SYM/proc" || mount -t proc proc "$SYM/proc"
  mountpoint -q "$SYM/sys" || mount -t sysfs sysfs "$SYM/sys"
  mountpoint -q "$SYM/run" || mount -t tmpfs tmpfs "$SYM/run"
}
umount_vfs() { for m in run sys proc dev/pts dev; do mountpoint -q "$SYM/$m" && umount -l "$SYM/$m"; done; true; }
trap umount_vfs EXIT
in_chroot() {  # in_chroot <skript>
  chroot "$SYM" /usr/bin/env -i HOME=/root TERM="${TERM:-xterm}" PS1='(symfonos) \u:\w\$ ' \
    PATH=/usr/bin:/usr/sbin MAKEFLAGS="$MAKEFLAGS" /bin/bash -euo pipefail -c "$1"
}
chown -R root:root "$SYM"/{usr,lib64,var,etc,tools} 2>/dev/null || true
mount_vfs
if ! stamp chroot-base; then
  log "Grundstruktur und Systemdateien"
  mkdir -p "$SYM"/{boot,home,mnt,opt,srv,root,tmp,etc/{opt,sysconfig},usr/{local,share/{man,doc,info}},var/{cache,lib,local,log,mail,opt,spool,tmp}}
  chmod 0750 "$SYM/root"; chmod 1777 "$SYM/tmp" "$SYM/var/tmp"
  ln -sfn /run "$SYM/var/run"; ln -sfn /run/lock "$SYM/var/lock"
  ln -sf /proc/self/mounts "$SYM/etc/mtab"
  printf 'root:x:0:0:root:/root:/bin/bash\nnobody:x:65534:65534:Unprivileged User:/dev/null:/usr/bin/false\n' > "$SYM/etc/passwd"
  printf 'root:x:0:\ntty:x:5:\nnogroup:x:65534:\n' > "$SYM/etc/group"
  printf '127.0.0.1 localhost symfonos\n::1 localhost\n' > "$SYM/etc/hosts"
  mark chroot-base
fi
# Quellen stehen in $SYM/sources (aus Stufe 1/2); Hilfsfunktion im Chroot:
CH_UNPACK='u(){ f=$(ls /sources/$1-[0-9]*.tar.* | head -1); rm -rf /build/$1-*/; tar -xf "$f" -C /build; cd /build/$1-*/; }'
for r in gettext bison perl python texinfo util-linux; do "$SYP" fetch "$RECIPES/$r/SYPBUILD.toml" -o "$SRC" >/dev/null; done
P=$(version_of perl); PM=${P%.*}
stamp ch-gettext || { log "gettext (temporär)"; in_chroot "$CH_UNPACK; u gettext; ./configure --disable-shared; make; cp gettext-tools/src/{msgfmt,msgmerge,xgettext} /usr/bin"; mark ch-gettext; }
stamp ch-bison || { log "bison (temporär)"; in_chroot "$CH_UNPACK; u bison; ./configure --prefix=/usr; make; make install"; mark ch-bison; }
stamp ch-perl || { log "perl (temporär)"; in_chroot "$CH_UNPACK; u perl; sh Configure -des -D prefix=/usr -D vendorprefix=/usr -D useshrplib \
  -D privlib=/usr/lib/perl5/$PM/core_perl -D archlib=/usr/lib/perl5/$PM/core_perl -D sitelib=/usr/lib/perl5/$PM/site_perl \
  -D sitearch=/usr/lib/perl5/$PM/site_perl -D vendorlib=/usr/lib/perl5/$PM/vendor_perl -D vendorarch=/usr/lib/perl5/$PM/vendor_perl; make; make install"; mark ch-perl; }
stamp ch-python || { log "Python (temporär)"; in_chroot "$CH_UNPACK; f=\$(ls /sources/Python-*.tar.xz | head -1); tar -xf \$f -C /build; cd /build/Python-*/; ./configure --prefix=/usr --enable-shared --without-ensurepip; make; make install"; mark ch-python; }
stamp ch-texinfo || { log "texinfo (temporär)"; in_chroot "$CH_UNPACK; u texinfo; ./configure --prefix=/usr; make; make install"; mark ch-texinfo; }
stamp ch-util-linux || { log "util-linux (temporär)"; in_chroot "$CH_UNPACK; u util-linux; mkdir -p /var/lib/hwclock; \
  ./configure --libdir=/usr/lib --runstatedir=/run --disable-chfn-chsh --disable-login --disable-nologin --disable-su \
  --disable-setpriv --disable-runuser --disable-pylibmount --disable-static --disable-liblastlog2 --without-python \
  ADJTIME_PATH=/var/lib/hwclock/adjtime; make; make install"; mark ch-util-linux; }
stamp ch-cleanup || { log "Aufräumen"; rm -rf "$SYM"/usr/share/{info,man,doc}/* "$SYM/build"/*
  find "$SYM"/usr/{lib,libexec} -name '*.la' -delete 2>/dev/null || true
  rm -rf "$SYM/tools"; mark ch-cleanup; }
log "Chroot-Werkzeuge fertig – jetzt übernimmt syp (Stufe 4)"

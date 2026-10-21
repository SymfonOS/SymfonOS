#!/usr/bin/env bash
# Läuft im Ziel-System (Calamares shellprocess, chroot) nach dem Entpacken.
set -euo pipefail

# 1) Branding und Live-Reste
ln -sf ../usr/share/symfonos/os-release /etc/os-release
rm -f /etc/sudoers.d/10-live /etc/sddm.conf.d/10-live-autologin.conf \
      /usr/share/applications/symfonos-install.desktop /etc/xdg/autostart/symfonos-welcome.desktop \
      /etc/systemd/system/multi-user.target.wants/symfonos-live-setup.service \
      /etc/systemd/system/symfonos-live-setup.service /usr/local/bin/symfonos-live-setup \
      /etc/systemd/system/systemd-firstboot.service
rm -f /etc/mkinitcpio.conf.d/archiso.conf
userdel -r liveuser 2>/dev/null || true

# 2) Standard-Initramfs-Presets statt archiso
for k in linux-zen linux-lts; do
  [[ -e /usr/lib/modules ]] || break
  cat > "/etc/mkinitcpio.d/$k.preset" <<PRESET
ALL_kver='/boot/vmlinuz-$k'
PRESETS=('default' 'fallback')
default_image='/boot/initramfs-$k.img'
fallback_image='/boot/initramfs-$k-fallback.img'
fallback_options='-S autodetect'
PRESET
done

# 3) snapper für das bereits gemountete @snapshots-Subvolume konfigurieren
install -Dm644 /usr/share/symfonos/snapper-root.conf /etc/snapper/configs/root
sed -i 's/^SNAPPER_CONFIGS=.*/SNAPPER_CONFIGS="root"/' /etc/conf.d/snapper 2>/dev/null \
  || echo 'SNAPPER_CONFIGS="root"' > /etc/conf.d/snapper
chmod 750 /.snapshots

# 4) Firewall: eingehend blockieren, KDE Connect erlauben
if command -v ufw >/dev/null; then
  ufw --force default deny incoming >/dev/null
  ufw --force default allow outgoing >/dev/null
  ufw allow 1714:1764/udp >/dev/null && ufw allow 1714:1764/tcp >/dev/null
  sed -i 's/^ENABLED=.*/ENABLED=yes/' /etc/ufw/ufw.conf
fi

# 5) Paketverwaltung an syp übergeben
syp import-pacman --retire
syp -y --no-scripts remove calamares mkinitcpio-archiso || true
syp -y autoremove || true

# 6) Lokales Repo des Live-Mediums austragen, Benutzerdienste global aktivieren
systemctl --global enable symfonos-update-notify.timer symfonos-design-auto.timer pipewire-pulse.socket || true

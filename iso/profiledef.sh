#!/usr/bin/env bash
# archiso-Profil für symfonOS (getestet mit archiso >= 83)
# shellcheck disable=SC2034

iso_name="symfonos"
iso_label="SYMFONOS_$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y%m)"
iso_publisher="symfonOS <https://github.com/thomasrohm23-bit/symfonOS>"
iso_application="symfonOS Live- und Installationsmedium"
iso_version="$(date --date="@${SOURCE_DATE_EPOCH:-$(date +%s)}" +%Y.%m.%d)"
install_dir="symfonos"
buildmodes=('iso')
bootmodes=('bios.syslinux' 'uefi.systemd-boot')
pacman_conf="pacman.conf"
airootfs_image_type="squashfs"
airootfs_image_tool_options=('-comp' 'xz' '-Xbcj' 'x86' '-b' '1M' '-Xdict-size' '1M')
bootstrap_tarball_compression=('zstd' '-c' '-T0' '--auto-threads=logical' '--long' '-19')
file_permissions=(
  ["/etc/sudoers.d/10-live"]="0:0:440"
  ["/usr/local/bin/symfonos-live-setup"]="0:0:755"
  ["/usr/local/bin/pacman"]="0:0:755"
)

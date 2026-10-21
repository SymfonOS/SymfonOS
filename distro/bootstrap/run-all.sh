#!/usr/bin/env bash
# Kompletter Bootstrap: Host -> Cross-Werkzeugkette -> temporäre Werkzeuge -> Chroot -> syp-Pakete -> ISO.
# Fortsetzbar: erledigte Schritte sind in $SYM/.stamps markiert, gebaute Pakete liegen im Repo.
#   SYM=/mnt/symfonos SYP_KEY=k.key SYP_PUB=k.pub ./run-all.sh
set -euo pipefail
D=$(cd "$(dirname "$0")" && pwd)
"$D/00-prepare.sh"
"$D/10-cross-toolchain.sh"
"$D/20-temp-tools.sh"
"$D/30-chroot-tools.sh"
"$D/40-system.sh"
"$D/50-image.sh"

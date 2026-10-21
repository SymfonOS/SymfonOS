#!/usr/bin/env bash
# Einmalig beim ersten Start des installierten Systems.
set -uo pipefail
symfonos-hwsetup || true
if [[ ! -e /var/lib/symfonos-assist/models/model.gguf ]] && curl -sfI --max-time 5 https://huggingface.co >/dev/null; then
  symfonos-assist-model auto || true
fi
syp sync || true
systemctl disable symfonos-firstboot.service

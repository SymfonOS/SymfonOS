#!/bin/sh
# Pakete auf dem CI-Rechner (Ubuntu) installieren. Die Ubuntu-Spiegel fallen zeitweise aus (apt Exit 100):
# Paketlisten neu einlesen und bis zu viermal versuchen, mit wachsender Pause.
set -u
for i in 1 2 3 4; do
  sudo apt-get update -q -o Acquire::Retries=3 || true
  sudo apt-get install -y -q -o Acquire::Retries=3 "$@" && exit 0
  [ "$i" = 4 ] && break
  echo "apt-install: Versuch $i fehlgeschlagen, neuer Versuch in $((i * 60)) s" >&2
  sleep $((i * 60))
done
echo "::error title=apt::Installation fehlgeschlagen: $*"
exit 100

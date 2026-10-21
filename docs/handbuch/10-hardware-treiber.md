# Hardware, Treiber und Firmware

- Firmware (`linux-firmware`, `sof-firmware`, `alsa-firmware`) und CPU-Microcode (`intel-ucode`, `amd-ucode`) sind vorinstalliert.
- AMD- und Intel-Grafik laufen mit Mesa (Vulkan: `vulkan-radeon`, `vulkan-intel`).
- **NVIDIA**: `symfonos-hwsetup` erkennt die Karte beim ersten Start und installiert `nvidia-open-dkms` (ab Turing/RTX 20) bzw. empfiehlt `nouveau` für ältere Karten. Erneut ausführen: `sudo symfonos-hwsetup`.
- Drucker: CUPS mit IPP Everywhere (treiberlos); Scanner über sane-airscan.
- Fingerabdruck: fprintd (Systemeinstellungen › Benutzer).
- Fehlende Firmware im Log („Direct firmware load failed“): `sudo syp upgrade`.

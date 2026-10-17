#!/usr/bin/env bash
# Downloads and extracts (no root needed) the Arm bare-metal compiler and the Arm system emulator used
# by quality/run_freertos.sh into build/rtos-tools. On CI they are installed with apt instead.
set -euo pipefail
cd "$(dirname "$0")/.."
root="${WIREFORGE_RTOS_TOOLS:-$PWD/build/rtos-tools}"
debs="${root}/debs"
mkdir -p "${debs}"
cd "${debs}"
apt-get download gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi \
  libnewlib-dev libstdc++-arm-none-eabi-newlib libstdc++-arm-none-eabi-dev \
  qemu-system-arm qemu-system-data qemu-system-common
for deb in *.deb; do
  dpkg-deb -x "${deb}" "${root}"
done
echo "tools in ${root} (add ${root}/usr/bin to PATH, ${root}/usr/lib/x86_64-linux-gnu to LD_LIBRARY_PATH)"

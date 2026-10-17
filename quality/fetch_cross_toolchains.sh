#!/usr/bin/env bash
# Downloads and extracts (no root needed) the cross compilers and qemu used by
# quality/run_portability.sh into build/toolchains. On CI they are installed with apt instead.
set -euo pipefail
cd "$(dirname "$0")/.."
root="${WIREFORGE_CROSS_ROOT:-$PWD/build/toolchains}"
debs="${root}/debs"
mkdir -p "${debs}"
cd "${debs}"
apt-get download qemu-user-static
for triple in s390x-linux-gnu i686-linux-gnu; do
  mapfile -t packages < <(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
    --no-breaks --no-replaces --no-enhances "g++-11-${triple}" | grep -E '^[a-z]' |
    grep -E "${triple}|${triple%%-*}-cross|i386-cross|cross-base" | sort -u)
  apt-get download "${packages[@]}"
done
for deb in *.deb; do
  dpkg-deb -x "${deb}" "${root}"
done
echo "toolchains in ${root}"

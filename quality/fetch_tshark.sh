#!/usr/bin/env bash
# Downloads tshark of an Ubuntu release (no root needed) into build/tshark-<release> and writes the
# wrapper build/tshark-<release>/tshark, which runs it with that release's own C library, so any
# x86-64 Linux host can run it. Packages come from the signed Ubuntu archive.
#   quality/fetch_tshark.sh [jammy|noble|resolute]   (Wireshark 3.6 / 4.2 / 4.6; default jammy)
# tests/python/test_wireshark.py uses the wrapper when WIREFORGE_TSHARK points to it.
set -euo pipefail
cd "$(dirname "$0")/.."
release="${1:-jammy}"
keyring=/usr/share/keyrings/ubuntu-archive-keyring.gpg
root="$PWD/build/tshark-${release}"
state="${root}/apt"
mkdir -p "${state}/etc/apt/sources.list.d" "${state}/etc/apt/preferences.d" \
  "${state}/etc/apt/apt.conf.d" "${state}/lists/partial" "${state}/cache/archives/partial" \
  "${root}/debs" "${root}/sysroot"
touch "${state}/status"
echo "deb [signed-by=${keyring}] http://archive.ubuntu.com/ubuntu ${release} main universe" \
  > "${state}/etc/apt/sources.list"
cat > "${state}/apt.conf" <<EOF
Dir "${state}/";
Dir::State "${state}";
Dir::State::lists "${state}/lists";
Dir::State::status "${state}/status";
Dir::Cache "${state}/cache";
Dir::Etc "${state}/etc/apt";
APT::Architecture "amd64";
Acquire::Languages "none";
EOF
export APT_CONFIG="${state}/apt.conf"
apt-get update -qq
mapfile -t packages < <(apt-cache depends --recurse --no-recommends --no-suggests --no-conflicts \
  --no-breaks --no-replaces --no-enhances tshark | grep -E '^[a-z0-9]' | sort -u)
(cd "${root}/debs" && apt-get download -qq "${packages[@]}")
for deb in "${root}"/debs/*.deb; do
  dpkg-deb -x "${deb}" "${root}/sysroot"
done
data="${root}/sysroot/usr/share/wireshark"
# Up to Wireshark 3.6, Debian keeps init.lua in /etc/wireshark and links it from the data directory.
if [[ -f "${root}/sysroot/etc/wireshark/init.lua" ]]; then
  ln -sf "${root}/sysroot/etc/wireshark/init.lua" "${data}/init.lua"
fi
lib="${root}/sysroot/usr/lib/x86_64-linux-gnu:${root}/sysroot/lib/x86_64-linux-gnu"
loader="$(find "${root}/sysroot" -name ld-linux-x86-64.so.2 -path '*x86_64-linux-gnu*' | head -1)"
cat > "${root}/tshark" <<EOF
#!/bin/sh
export WIRESHARK_DATA_DIR="${data}"
exec "${loader}" --library-path "${lib}" "${root}/sysroot/usr/bin/tshark" "\$@"
EOF
chmod +x "${root}/tshark"
"${root}/tshark" --version | head -1
echo "WIREFORGE_TSHARK=${root}/tshark"

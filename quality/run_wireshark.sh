#!/usr/bin/env bash
# Runs the Wireshark dissector tests against tshark 3.6 (Lua 5.2), 4.2 (Lua 5.2) and 4.6 (Lua 5.4).
# Every release is fetched once into build/tshark-<release> (quality/fetch_tshark.sh).
set -euo pipefail
cd "$(dirname "$0")/.."
python="${PYTHON:-python3}"
for release in ${WIREFORGE_TSHARK_RELEASES:-jammy noble resolute}; do
  wrapper="build/tshark-${release}/tshark"
  [[ -x "${wrapper}" ]] || quality/fetch_tshark.sh "${release}"
  echo "== $("${wrapper}" --version | head -1) =="
  WIREFORGE_TSHARK="$PWD/${wrapper}" "${python}" -m pytest -q tests/python/test_wireshark.py
done

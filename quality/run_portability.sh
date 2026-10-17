#!/usr/bin/env bash
# Portability and analysis matrix beyond the main build:
#  - Clang -Weverything (documented exclusions) on runtime and generated code, self-tests run;
#  - plain char unsigned (-funsigned-char, the ARM default) on the host;
#  - clang static analyzer (scan-build) over runtime, generated code and self-tests;
#  - cross builds with the full warning set for 64-bit big-endian s390x, 32-bit big-endian powerpc,
#    32-bit i686 and armhf and 64-bit aarch64, every test suite (runtime, generated GoogleTest
#    suites, self-tests) run under qemu.
# Cross compilers and qemu come from PATH (CI: apt) or from build/toolchains
# (quality/fetch_cross_toolchains.sh); WIREFORGE_SKIP_CROSS=1 skips the cross builds explicitly.
set -euo pipefail
cd "$(dirname "$0")/.."

# WIREFORGE_PORTABILITY_PART=analysis runs only the host builds, =cross only the cross builds
# (CI runs them as separate jobs).
part="${WIREFORGE_PORTABILITY_PART:-all}"
if [[ "${part}" != "cross" ]]; then
  echo "-- clang -Weverything"
  cmake --preset clang-everything > /dev/null
  cmake --build --preset clang-everything
  ctest --preset clang-everything

  echo "-- plain char unsigned, as on ARM"
  cmake --preset unsigned-char > /dev/null
  cmake --build --preset unsigned-char
  ctest --preset unsigned-char

  echo "-- scan-build"
  scan="$(command -v scan-build || command -v scan-build-18 || command -v scan-build-14)"
  rm -rf build/scan-build
  "${scan}" cmake -S . -B build/scan-build -G Ninja -DCMAKE_BUILD_TYPE=Debug \
    -DWIREFORGE_BUILD_TESTS=OFF > /dev/null
  "${scan}" --status-bugs -o build/scan-build/report cmake --build build/scan-build
fi

if [[ "${part}" == "analysis" ]]; then
  exit 0
fi
if [[ "${WIREFORGE_SKIP_CROSS:-0}" == "1" ]]; then
  echo "-- cross builds SKIPPED (WIREFORGE_SKIP_CROSS=1)"
  exit 0
fi
root=()
if [[ -d build/toolchains/usr/bin ]]; then
  root=(-DWIREFORGE_CROSS_ROOT="$PWD/build/toolchains")
  # Binutils extracted without installation need their shared libraries.
  export LD_LIBRARY_PATH="$PWD/build/toolchains/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
gtest=()
if [[ -d build/dev/_deps/googletest-src ]]; then
  gtest=(-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST="$PWD/build/dev/_deps/googletest-src")
fi
# Every cell of the architecture matrix: 32 and 64 bit, little and big endian, x86, ARM and others.
# WIREFORGE_CROSS_TRIPLES selects some of them (CI runs one per job).
triples="${WIREFORGE_CROSS_TRIPLES:-s390x-linux-gnu i686-linux-gnu aarch64-linux-gnu arm-linux-gnueabihf powerpc-linux-gnu}"
describe() {
  case "$1" in
    s390x-*) echo "64-bit, big endian" ;;
    powerpc-*) echo "32-bit, big endian" ;;
    i686-* | arm-*) echo "32-bit, little endian" ;;
    aarch64-* | x86_64-*) echo "64-bit, little endian" ;;
    *) echo "unknown" ;;
  esac
}
for triple in ${triples}; do
  echo "-- cross ${triple} ($(describe "${triple}"))"
  build="build/cross-${triple}"
  cmake -S . -B "${build}" -G Ninja -DCMAKE_BUILD_TYPE=Debug \
    -DCMAKE_TOOLCHAIN_FILE="$PWD/cmake/toolchains/cross.cmake" \
    -DWIREFORGE_CROSS_TRIPLE="${triple}" "${root[@]}" "${gtest[@]}" > /dev/null
  cmake --build "${build}"
  ctest --test-dir "${build}" --output-on-failure | tail -n 3
done

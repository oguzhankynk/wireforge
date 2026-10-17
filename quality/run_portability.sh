#!/usr/bin/env bash
# Portability and analysis matrix beyond the main build:
#  - Clang -Weverything (documented exclusions) on runtime and generated code, self-tests run;
#  - clang static analyzer (scan-build) over runtime, generated code and self-tests;
#  - cross builds with the full warning set: big-endian s390x and 32-bit i686, every test suite
#    (runtime, generated GoogleTest suites, self-tests) run under qemu.
# Cross compilers and qemu come from PATH (CI: apt) or from build/toolchains
# (quality/fetch_cross_toolchains.sh); WIREFORGE_SKIP_CROSS=1 skips the cross builds explicitly.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "-- clang -Weverything"
cmake --preset clang-everything > /dev/null
cmake --build --preset clang-everything
ctest --preset clang-everything

echo "-- scan-build"
scan="$(command -v scan-build || command -v scan-build-18 || command -v scan-build-14)"
rm -rf build/scan-build
"${scan}" cmake -S . -B build/scan-build -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  -DWIREFORGE_BUILD_TESTS=OFF > /dev/null
"${scan}" --status-bugs -o build/scan-build/report cmake --build build/scan-build

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
for triple in s390x-linux-gnu i686-linux-gnu; do
  echo "-- cross ${triple}"
  build="build/cross-${triple}"
  cmake -S . -B "${build}" -G Ninja -DCMAKE_BUILD_TYPE=Debug \
    -DCMAKE_TOOLCHAIN_FILE="$PWD/cmake/toolchains/cross.cmake" \
    -DWIREFORGE_CROSS_TRIPLE="${triple}" "${root[@]}" "${gtest[@]}" > /dev/null
  cmake --build "${build}"
  ctest --test-dir "${build}" --output-on-failure | tail -n 3
done

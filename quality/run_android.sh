#!/usr/bin/env bash
# Builds the runtime tests and the tests of the generated code of the examples with the Android NDK
# (Clang, Bionic, libc++) for the 64-bit and 32-bit ARM ABIs and for x86_64, with the full warning
# set as errors. The binaries are linked but not run: a binary linked against Bionic does not run
# on a glibc host, not even under qemu-user (the statically linked ones abort for want of the
# Android TLS layout), and no emulator image is started in CI.
# ANDROID_NDK_HOME (or ANDROID_NDK_LATEST_HOME) names the NDK; WIREFORGE_ANDROID_ABIS the ABIs.
set -euo pipefail
cd "$(dirname "$0")/.."
ndk="${ANDROID_NDK_HOME:-${ANDROID_NDK_LATEST_HOME:-}}"
if [[ -z "${ndk}" || ! -f "${ndk}/build/cmake/android.toolchain.cmake" ]]; then
  echo "error: set ANDROID_NDK_HOME to an Android NDK" >&2
  exit 2
fi
gtest=()
if [[ -d build/dev/_deps/googletest-src ]]; then
  gtest=(-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST="$PWD/build/dev/_deps/googletest-src")
fi
for abi in ${WIREFORGE_ANDROID_ABIS:-arm64-v8a armeabi-v7a x86_64}; do
  echo "-- android ${abi}"
  build="build/android-${abi}"
  cmake -S . -B "${build}" -G Ninja -DCMAKE_BUILD_TYPE=Debug \
    -DCMAKE_TOOLCHAIN_FILE="${ndk}/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="${abi}" -DANDROID_PLATFORM=android-24 -DANDROID_STL=c++_static \
    "${gtest[@]}" > /dev/null
  cmake --build "${build}"
  count="$(find "${build}/tests" -maxdepth 2 -type f -perm -u+x -name '*_test' | wc -l)"
  echo "-- android ${abi}: ${count} test executables linked"
done

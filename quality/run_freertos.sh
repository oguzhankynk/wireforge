#!/usr/bin/env bash
# Builds the FreeRTOS test harness (tests/rtos) for a Cortex-M4F with arm-none-eabi-gcc and runs it
# on QEMU's mps2-an386 machine: the generated self-tests of three examples and the runtime used by
# several tasks with a FreeRTOS mutex as its lock. The exit code of the emulator is the result.
# Needs arm-none-eabi-g++, newlib and qemu-system-arm (CI: apt; no root: quality/fetch_rtos_tools.sh
# and WIREFORGE_RTOS_TOOLS).
set -euo pipefail
cd "$(dirname "$0")/.."
tools="${WIREFORGE_RTOS_TOOLS:-}"
if [[ -z "${tools}" && -d build/rtos-tools/usr/bin ]]; then
  tools="$PWD/build/rtos-tools"
fi
if [[ -n "${tools}" ]]; then
  export WIREFORGE_RTOS_TOOLS="${tools}"
  export PATH="${tools}/usr/bin:${PATH}"
  export LD_LIBRARY_PATH="${tools}/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
  export QEMU_DATA=(-L "${tools}/usr/share/qemu")
fi
build=build/rtos
cmake -S tests/rtos -B "${build}" -G Ninja -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_TOOLCHAIN_FILE="$PWD/cmake/toolchains/arm-none-eabi.cmake" > /dev/null
cmake --build "${build}"
arm-none-eabi-size "${build}/rtos_harness" || true
echo "-- qemu-system-arm -M mps2-an386"
set +e
timeout "${WIREFORGE_RTOS_TIMEOUT:-120}" qemu-system-arm -M mps2-an386 -cpu cortex-m4 -nographic \
  -semihosting-config enable=on,target=native -kernel "${build}/rtos_harness" \
  ${QEMU_DATA:+"${QEMU_DATA[@]}"} > "${build}/run.log" 2>&1
status=$?
set -e
cat "${build}/run.log"
if [[ "${status}" -ne 0 ]]; then
  echo "rtos harness failed with status ${status}" >&2
  exit "${status}"
fi
echo "rtos harness: passed"

#!/usr/bin/env bash
# The quickstart of docs/GUIDE.md, timed, in a fresh virtual environment with the built wheel:
# import an ICD, add an interface, validate, analyze, generate and run the tests, then build an
# application through the CMake package. Fails when anything fails or it takes 15 minutes or more.
set -euo pipefail
cd "$(dirname "$0")/.."
repo="$PWD"
work="${repo}/build/quickstart"
start=$(date +%s)
rm -rf "${work}"
mkdir -p "${work}"
python3 -m venv "${work}/venv"
"${work}/venv/bin/pip" install --quiet --upgrade pip
"${work}/venv/bin/pip" wheel --quiet --no-deps --wheel-dir "${work}/dist" "${repo}"
"${work}/venv/bin/pip" install --quiet "${work}/dist/"wireforge-*.whl
export PATH="${work}/venv/bin:${PATH}"
cmake_args=()
if [[ -d "${repo}/build/dev/_deps/googletest-src" ]]; then
  cmake_args=("--cmake-arg=-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST=${repo}/build/dev/_deps/googletest-src")
fi

cd "${work}"
python -c 'from wireforge import resources; import sys
assert "site-packages" in str(resources.runtime_include()), "runtime headers come from the wheel"
assert "site-packages" in str(resources.schema_dir())'
wireforge import-icd "${repo}/examples/icd/telemetry_icd.csv" demo --name telemetry --namespace demo::imu --endian little
wireforge upsert demo interfaces imu_link '{ version: "1.0.0", direction: rx, header: { fields: [{ name: msgId, type: u16, role: message_id }, { name: length, type: u8, role: payload_length }] }, framing: { kind: length_prefixed, max_frame_size: 64 }, messages: [ImuSample, Heartbeat] }'
wireforge validate demo
wireforge analyze demo > icd.md
wireforge test demo -o generated --generator Ninja "${cmake_args[@]}" | tail -n 3
cmake -S "${repo}/examples/cmake_consumer" -B consumer -G Ninja -DWireForge_DIR="$(wireforge cmake-dir)" > /dev/null
cmake --build consumer > /dev/null
ctest --test-dir consumer --output-on-failure | tail -n 1

# The same application against an installed prefix (`cmake --install`) instead of the wheel.
cmake -S "${repo}" -B install-build -G Ninja -DWIREFORGE_BUILD_TESTS=OFF -DWIREFORGE_BUILD_SELFTESTS=OFF > /dev/null
cmake --install install-build --prefix "${work}/prefix" > /dev/null
cmake -S "${repo}/examples/cmake_consumer" -B consumer-prefix -G Ninja -DCMAKE_PREFIX_PATH="${work}/prefix" > /dev/null
cmake --build consumer-prefix > /dev/null
ctest --test-dir consumer-prefix --output-on-failure | tail -n 1

elapsed=$(( $(date +%s) - start ))
echo "quickstart: ${elapsed} s"
if (( elapsed >= 900 )); then
  echo "quickstart took 15 minutes or more" >&2
  exit 1
fi

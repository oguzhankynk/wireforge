#!/usr/bin/env bash
# Builds the libFuzzer targets (runtime: checksum, frame decoder; every example: deserialize
# round trips and frame streams) with Clang and runs each for WIREFORGE_FUZZ_SECONDS seconds,
# WIREFORGE_FUZZ_JOBS targets at a time (default: the number of CPUs). WIREFORGE_FUZZ_SHARD=k/n runs only
# every n-th target starting with the k-th (1 <= k <= n), so n jobs share the targets.
# Seeds: tests/regressions/fuzz/<target>/ plus the golden payloads and frames of the examples.
# A crash fails the run; its input is left in build/fuzz/artifacts/.
set -euo pipefail
cd "$(dirname "$0")/.."
seconds="${WIREFORGE_FUZZ_SECONDS:-20}"
jobs="${WIREFORGE_FUZZ_JOBS:-$(nproc)}"
shard="${WIREFORGE_FUZZ_SHARD:-1/1}"
if [[ ! "${shard}" =~ ^([1-9][0-9]*)/([1-9][0-9]*)$ ]] || (( BASH_REMATCH[1] > BASH_REMATCH[2] )); then
  echo "WIREFORGE_FUZZ_SHARD must be k/n with 1 <= k <= n, not ${shard}" >&2
  exit 2
fi
shard_index="${BASH_REMATCH[1]}"
shard_count="${BASH_REMATCH[2]}"
work="build/fuzz"
mkdir -p "${work}/artifacts"

# Every target is built first and queued as "<executable> <extra seed dir or ->"; then the queue
# runs in parallel.
queue="${work}/queue"
: > "${queue}"
queue_target() {
  echo "$1 ${2:--}" >> "${queue}"
}

run_target() {
  local executable="$1" extra="$2" name
  name="$(basename "${executable}")"
  local corpus="${work}/corpus/${name}"
  mkdir -p "${corpus}"
  if [[ -d "tests/regressions/fuzz/${name}" ]]; then
    cp -r "tests/regressions/fuzz/${name}/." "${corpus}/"
  fi
  if [[ "${extra}" != "-" && -d "${extra}" ]]; then
    cp -r "${extra}/." "${corpus}/"
  fi
  local log="${work}/${name}.log"
  # Older ASan runtimes crash at start-up with high mmap ASLR entropy (WSL2); fuzzing without
  # ASLR is harmless.
  if ! setarch "$(uname -m)" -R "${executable}" -max_total_time="${seconds}" -print_final_stats=1 \
      -artifact_prefix="${work}/artifacts/${name}-" "${corpus}" > "${log}" 2>&1; then
    echo "-- ${name}: FAILED"
    tail -n 40 "${log}"
    return 1
  fi
  echo "-- ${name}: $(grep -E "^stat::number_of_executed_units" "${log}")"
}

cmake --preset fuzz > /dev/null
cmake --build --preset fuzz --target checksum_fuzz frame_decoder_fuzz
queue_target "${work}/tests/fuzz/checksum_fuzz"
queue_target "${work}/tests/fuzz/frame_decoder_fuzz"

gtest=()
if [[ -d build/dev/_deps/googletest-src ]]; then
  gtest=(-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST="$PWD/build/dev/_deps/googletest-src")
fi
quality/examples.sh | while read -r example name plugin_file; do
  plugins=()
  [[ "${plugin_file}" == "-" ]] || plugins=(--plugins "${plugin_file}")
  PYTHONPATH=codegen python3 -m wireforge.generate "${example}" -o "${work}/projects" --tests \
    "${plugins[@]}"
  seeds="${work}/seeds/${name}"
  PYTHONPATH=codegen python3 -m wireforge.differential "${example}" --fuzz-seeds "${seeds}" \
    "${plugins[@]}" > /dev/null
  project="${work}/projects/${name}/generated_tests"
  CXX=clang++ cmake -S "${project}" -B "${project}/build/fuzz" -G Ninja -DCMAKE_BUILD_TYPE=Debug \
    -DWIREFORGE_FUZZ=ON "${gtest[@]}" > /dev/null
  targets="$(PYTHONPATH=codegen python3 -c 'import sys; from pathlib import Path
from wireforge.loader import load_project
from wireforge.test_project import executables
print(" ".join(executables(load_project(Path(sys.argv[1])).project, "fuzz")))' "${example}")"
  for target in ${targets}; do
    cmake --build "${project}/build/fuzz" --target "${target}"
    queue_target "${project}/build/fuzz/${target}" "${seeds}"
  done
done
awk -v k="${shard_index}" -v n="${shard_count}" '(NR - k) % n == 0' "${queue}" > "${queue}.shard"
mv "${queue}.shard" "${queue}"
count="$(wc -l < "${queue}")"
echo "-- running ${count} targets (shard ${shard}) for ${seconds} s each, ${jobs} at a time"
export -f run_target
export work seconds
if ! xargs -P "${jobs}" -L 1 bash -c 'run_target "$0" "$1"' < "${queue}"; then
  echo "fuzz: at least one target failed"
  exit 1
fi
echo "fuzz: no crashes"

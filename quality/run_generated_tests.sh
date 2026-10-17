#!/usr/bin/env bash
# Generates the GoogleTest project of every example and builds it standalone with its own CMake
# files: zero warnings, all tests pass, line, branch and function coverage of the generated code at
# the thresholds (line: WIREFORGE_GENERATED_COVERAGE_MIN_LINE, branch and function: 100).
# Then compares the generated C++ with the reference codec on random, boundary and stored inputs
# (tests/regressions/<project>/<profile>/), per profile. Finally the zip the server offers for
# download is built and tested like a user would. The result documents of every example (test
# results, coverage of the generated code and the runtime, stack use) are written to
# build/evidence-results/.
set -euo pipefail
cd "$(dirname "$0")/.."
out="build/generated-projects"
rm -rf build/evidence-results
threshold="${WIREFORGE_GENERATED_COVERAGE_MIN_LINE:-100}"
values="${WIREFORGE_DIFFERENTIAL_VALUES:-100}"
gtest=()
# Reuse the GoogleTest sources fetched by the main build instead of downloading them again.
if [[ -d build/dev/_deps/googletest-src ]]; then
  gtest=(-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST="$PWD/build/dev/_deps/googletest-src")
fi
quality/examples.sh | while read -r example name plugin_file; do
  plugins=()
  [[ "${plugin_file}" == "-" ]] || plugins=(--plugins "${plugin_file}")
  PYTHONPATH=codegen python3 -m wireforge.generate "${example}" -o "${out}" --tests \
    --coverage-threshold "${threshold}" "${plugins[@]}"
  project="${out}/${name}/generated_tests"
  build="${project}/build/coverage"
  echo "-- ${project}"
  # The runtime headers are measured too; their threshold is left open here because the generated
  # tests alone do not reach every runtime statement. `wireforge docs` below reports the gaps.
  cmake -S "${project}" -B "${build}" -G Ninja -DCMAKE_BUILD_TYPE=Debug -DWIREFORGE_COVERAGE=ON \
    -DWIREFORGE_COVERAGE_RUNTIME=ON -DWIREFORGE_COVERAGE_RUNTIME_MIN_LINE=0 \
    "${gtest[@]}" > /dev/null
  cmake --build "${build}"
  find "${build}" -name '*.gcda' -delete
  cmake --build "${build}" --target coverage
  PYTHONPATH=codegen python3 -m wireforge.cli docs "${example}" \
    -o "build/evidence-results/docs_${name}" --results "${build}"
  for harness in "${build}/${name}"_diff*; do
    suffix="${harness##*/${name}_diff}"
    profile=()
    regressions="tests/regressions/${name}/default"
    if [[ -n "${suffix}" ]]; then
      profile=(--profile "${suffix#_}")
      regressions="tests/regressions/${name}/${suffix#_}"
    fi
    PYTHONPATH=codegen python3 -m wireforge.differential "${example}" --harness "${harness}" \
      --values "${values}" --regressions "${regressions}" "${profile[@]}" "${plugins[@]}"
  done
done

echo "-- server download (zip of example 02)"
zip_dir="build/server-zip"
rm -rf "${zip_dir}"
mkdir -p "${zip_dir}"
PYTHONPATH=codegen python3 - "${zip_dir}" <<'PYTHON'
import io
import sys
import zipfile
from pathlib import Path

from wireforge import service

result, data = service.generate_archive(Path("examples/02_multi_interface_avionics"))
if not result.ok:
    sys.exit(result.text)
zipfile.ZipFile(io.BytesIO(data)).extractall(sys.argv[1])
PYTHON
project="${zip_dir}/avionics_demo/include/avionics_demo/generated_tests"
cmake -S "${project}" -B "${zip_dir}/build" -G Ninja -DCMAKE_BUILD_TYPE=Debug "${gtest[@]}" > /dev/null
cmake --build "${zip_dir}/build"
ctest --test-dir "${zip_dir}/build" --output-on-failure | tail -n 3

echo "-- MCP agent scenario (ICD text -> messages -> validate -> generate -> tests)"
rm -rf build/agent-scenario
PYTHONPATH=codegen python3 quality/agent_scenario.py build/agent-scenario

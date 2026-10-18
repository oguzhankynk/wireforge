#!/usr/bin/env bash
# The C99 code of every model that generated C supports: the GoogleTest project of each model is
# generated with `--language c`, built standalone with its own CMake files (the C code with the
# warning set of C code as errors) and run; line, branch and function coverage of the generated
# `model.c` must be 100 percent. A model with something generated C does not support yet (a user
# type) is skipped and listed.
set -euo pipefail
cd "$(dirname "$0")/.."
out="build/generated-projects-c"
rm -rf "${out}"
gtest=()
if [[ -d build/dev/_deps/googletest-src ]]; then
  gtest=(-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST="$PWD/build/dev/_deps/googletest-src")
fi
skipped=()
quality/examples.sh | {
  while read -r example name plugin_file; do
    plugins=()
    [[ "${plugin_file}" == "-" ]] || plugins=(--plugins "${plugin_file}")
    set +e
    log="$(PYTHONPATH=codegen python3 -m wireforge.generate "${example}" -o "${out}" --tests \
      --language c --coverage-threshold 100 "${plugins[@]}" 2>&1)"
    status=$?
    set -e
    if [[ ${status} -ne 0 ]]; then
      if grep -q "generated C does not support" <<<"${log}"; then
        echo "-- ${name}: skipped (${log##*: })"
        continue
      fi
      echo "${log}"
      exit 1
    fi
    project="${out}/${name}/generated_tests"
    build="${project}/build/coverage"
    echo "-- ${project}"
    cmake -S "${project}" -B "${build}" -G Ninja -DCMAKE_BUILD_TYPE=Debug \
      -DWIREFORGE_COVERAGE=ON "${gtest[@]}" > /dev/null
    cmake --build "${build}"
    cmake --build "${build}" --target coverage | tail -n 6
  done
}

#!/usr/bin/env bash
# Builds and runs the generated tests of every unit of every project with `nodes` (examples/08_system
# and the like), in both languages, with coverage at the thresholds (100 % lines, branches and
# functions of the unit's generated code), and then the system test: every sender's generated
# encoder against every receiver's generated decoder. A unit with a language of its own is built in
# that language in both runs.
set -euo pipefail
cd "$(dirname "$0")/.."
out="build/system-projects"
rm -rf "${out}"
gtest=()
if [[ -d build/dev/_deps/googletest-src ]]; then
  gtest=(-DFETCHCONTENT_SOURCE_DIR_GOOGLETEST="$PWD/build/dev/_deps/googletest-src")
fi
quality/examples.sh --nodes | while read -r example name plugin_file; do
  plugins=()
  [[ "${plugin_file}" == "-" ]] || plugins=(--plugins "${plugin_file}")
  units="$(PYTHONPATH=codegen python3 -c 'import sys; from pathlib import Path
from wireforge.loader import load_project
print(*load_project(Path(sys.argv[1])).project.nodes)' "${example}")"
  for unit in ${units}; do
    for language in cpp c; do
      echo "-- ${name} unit ${unit} (${language})"
      PYTHONPATH=codegen python3 -m wireforge.cli test "${example}" -o "${out}/${name}-${language}" \
        --node "${unit}" --language "${language}" --coverage --generator Ninja "${plugins[@]}" \
        "${gtest[@]/#/--cmake-arg=}" | tail -n 3
    done
  done
  echo "-- ${name} system test"
  PYTHONPATH=codegen python3 -m wireforge.cli test "${example}" -o "${out}/${name}-system" \
    --system --generator Ninja "${plugins[@]}" "${gtest[@]/#/--cmake-arg=}" | tail -n 3
done

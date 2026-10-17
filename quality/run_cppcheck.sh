#!/usr/bin/env bash
# cppcheck gate: any finding fails. No suppressions are passed on purpose.
set -euo pipefail
build_dir="${1:-build/dev}"
# --check-level exists since cppcheck 2.11; older releases always run the exhaustive analysis.
check_level=()
if cppcheck --help | grep -q -- '--check-level'; then
  check_level=(--check-level=exhaustive)
fi
cppcheck \
  --project="${build_dir}/compile_commands.json" \
  --std=c++17 \
  --enable=warning,style,performance,portability \
  "${check_level[@]}" \
  --inconclusive \
  --error-exitcode=1 \
  --quiet \
  -i "${build_dir}/_deps" \
  --library=googletest

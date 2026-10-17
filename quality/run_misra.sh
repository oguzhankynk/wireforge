#!/usr/bin/env bash
# Optional, non-gating. Runs cppcheck's MISRA C:2012 addon as a rough proxy on C++ code; it is not
# a MISRA C++ or AUTOSAR conformance check (see docs/CODING_RULES.md). Always exits 0.
# Writes build/misra/findings.xml (cppcheck XML v2) and prints the findings per rule.
# WIREFORGE_MISRA_RULE_TEXTS may name a rule text file you are licensed to use; it is never stored here.
set -uo pipefail
cd "$(dirname "$0")/.."
build_dir="${1:-build/dev}"
out="build/misra"
mkdir -p "${out}" "${out}/cppcheck"
check_level=()
if cppcheck --help | grep -q -- '--check-level'; then
  check_level=(--check-level=exhaustive)
fi
addon='{"script": "misra.py", "args": []}'
if [[ -n "${WIREFORGE_MISRA_RULE_TEXTS:-}" ]]; then
  addon="{\"script\": \"misra.py\", \"args\": [\"--rule-texts=${WIREFORGE_MISRA_RULE_TEXTS}\"]}"
fi
echo "${addon}" > "${out}/misra.json"
cppcheck \
  --project="${build_dir}/compile_commands.json" \
  --std=c++17 \
  --addon="${out}/misra.json" \
  --cppcheck-build-dir="${out}/cppcheck" \
  "${check_level[@]}" \
  --xml --xml-version=2 \
  --quiet \
  -i "${build_dir}/_deps" \
  --library=googletest 2> "${out}/findings.xml" || true
python3 -I quality/misra_summary.py "${out}/findings.xml"
exit 0

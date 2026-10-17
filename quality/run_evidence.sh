#!/usr/bin/env bash
# Collects the safety evidence in build/evidence/ (uploaded by CI):
#  - no_heap.md: library symbols of the runtime and the generated code built like embedded code;
#  - trace_<project>.md/.csv: model element -> source line -> generated symbol -> tests;
#  - icd_<project>.md: layout report per profile;
#  - hashes.txt: model hash of every example (also in the banner of the generated files);
#  - SAFETY.md: the coding-rule mapping.
set -euo pipefail
cd "$(dirname "$0")/.."
out="build/evidence"
rm -rf "${out}"
mkdir -p "${out}"
PYTHONPATH=codegen python3 quality/check_no_heap.py "${out}/no_heap.md"
quality/examples.sh | while read -r example name _; do
  PYTHONPATH=codegen python3 -m wireforge.cli trace "${example}" -o "${out}/trace_${name}.md"
  PYTHONPATH=codegen python3 -m wireforge.cli trace "${example}" --format csv -o "${out}/trace_${name}.csv"
  PYTHONPATH=codegen python3 -m wireforge.cli analyze "${example}" > "${out}/icd_${name}.md"
  hash="$(PYTHONPATH=codegen python3 -c 'import sys; from pathlib import Path
from wireforge import bundle
print(bundle.build_bundle(Path(sys.argv[1]))["bundle"]["content_hash"])' "${example}")"
  echo "${name} ${hash}" >> "${out}/hashes.txt"
done
cp docs/SAFETY.md "${out}/SAFETY.md"
echo "evidence in ${out}: $(ls "${out}" | tr '\n' ' ')"

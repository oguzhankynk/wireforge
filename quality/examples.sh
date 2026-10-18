#!/usr/bin/env bash
# Shared by the quality scripts: prints "<example dir> <project name> <plugins file or ->" per example
# (examples/, protocols/, the models of tests/models/ and the templates of `wireforge init`).
set -euo pipefail
cd "$(dirname "$0")/.."
for example in examples/*/ protocols/*/ tests/models/*/ codegen/wireforge/templates/*/; do
  example="${example%/}"
  [[ -f "${example}/project.yaml" ]] || continue  # e.g. examples/icd holds import sources
  name="$(PYTHONPATH=codegen python3 -c 'import sys; from pathlib import Path
from wireforge.loader import load_project
print(load_project(Path(sys.argv[1])).project.name)' "${example}")"
  plugins="-"
  if [[ -d "${example}/plugins" ]]; then
    for file in "${example}"/plugins/*.py; do
      [[ "$(basename "${file}")" == "__init__.py" ]] || plugins="${file}"
    done
  fi
  echo "${example} ${name} ${plugins}"
done

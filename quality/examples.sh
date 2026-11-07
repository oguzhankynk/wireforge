#!/usr/bin/env bash
# Shared by the quality scripts: prints "<example dir> <project name> <plugins file or ->" per example
# (examples/, protocols/, the models of tests/models/ and the templates of `wireforge init`).
# Projects with `nodes` (systems of units) are listed only with `--nodes`; they are built unit by
# unit by quality/run_system_tests.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
for example in examples/*/ protocols/*/ tests/models/*/ codegen/wireforge/templates/*/; do
  example="${example%/}"
  [[ -f "${example}/project.yaml" ]] || continue  # e.g. examples/icd holds import sources
  read -r name kind <<< "$(PYTHONPATH=codegen python3 -c 'import sys; from pathlib import Path
from wireforge.loader import load_project
project = load_project(Path(sys.argv[1])).project
print(project.name, "nodes" if project.nodes else "plain")' "${example}")"
  if [[ "${1:-}" == "--nodes" ]]; then
    [[ "${kind}" == "nodes" ]] || continue
  else
    [[ "${kind}" == "plain" ]] || continue
  fi
  plugins="-"
  if [[ -d "${example}/plugins" ]]; then
    for file in "${example}"/plugins/*.py; do
      [[ "$(basename "${file}")" == "__init__.py" ]] || plugins="${file}"
    done
  fi
  echo "${example} ${name} ${plugins}"
done

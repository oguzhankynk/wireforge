# Contributing

## Development setup (Linux, also WSL2)
```bash
sudo apt install -y git cmake ninja-build g++ clang clang-tools clang-format cppcheck doxygen python3-venv nodejs npm
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
git config core.hooksPath .githooks      # formatting check on commit, build + tests on push
```
`pip install -e ".[dev]"` also installs clang-tidy 18 and gcovr into the venv; run the gates with the venv active.
Under WSL2 keep the checkout on the Linux file system (`~/...`), not under `/mnt/c`, for build speed.

## Quality gates
`quality/run_all.sh` runs every gate (quality/QUALITY_POLICY.md): formatting, build and tests, ASan/UBSan, TSan,
the generated test projects with coverage and differential testing, fuzzing, the portability matrix (Clang
`-Weverything`, scan-build, big-endian and 32-bit cross builds under qemu), safety evidence, the quickstart, the GCC
analyzer, clang-tidy, cppcheck, doxygen, Python (ruff, mypy, pytest), the web UI and the suppression registry. A change
is ready when all of them pass with zero warnings.

- Cross toolchains for the portability matrix: `quality/fetch_cross_toolchains.sh` (no root needed) or the Debian
  packages `g++-s390x-linux-gnu g++-powerpc-linux-gnu g++-i686-linux-gnu g++-arm-linux-gnueabihf
  g++-aarch64-linux-gnu qemu-user`; `WIREFORGE_CROSS_TRIPLES` selects some of the targets.
- Sanitizer tests run with ASLR disabled because older sanitizer runtimes hang with the high mmap entropy of WSL2
  kernels.
- Generated-code snapshots: after an intended generator change run
  `WIREFORGE_UPDATE_SNAPSHOTS=1 pytest tests/python/test_generate.py` and review the diff.
- UI types: `npm run gen:types` in `ui/` after schema or API changes.

## Releases
The version has one source, `__version__` in `codegen/wireforge/__init__.py` (the wheel, `wireforge version`, the CMake
package and `ui/package.json` follow it; `tests/python/test_packaging.py` keeps them equal). Python 3.10 to 3.13 are
tested on every pull request (ruff and mypy on 3.12 only).

- `python3 quality/check_dist.py` builds the wheel and the source distribution twice with `SOURCE_DATE_EPOCH` set to the
  last commit, requires the same wheel hash and the same files in the source distribution, requires the schemas, runtime
  headers, runtime requirements, templates, CMake package and both licenses in the wheel, and installs the wheel and the
  source distribution in clean virtual environments (`wireforge version`, `init`, `validate`, `generate`). CI runs it as
  the job `dist`.
- A tag `v<version>` starts the workflow `release`: it builds the UI and the distributions, writes a CycloneDX SBOM and
  the `pip-audit` report of the resolved dependencies (a known vulnerability fails the release), and creates the GitHub
  release with all of them. The upload to PyPI is a separate job that runs only when the repository variable
  `PUBLISH_TO_PYPI` is `true` and the environment `pypi` approves it (the owner is its required reviewer). The package
  declares both licenses (`License-Expression: BUSL-1.1 AND MIT`, files `LICENSE` and `runtime/LICENSE`), which
  `quality/check_dist.py` checks. To turn the upload on: on pypi.org add a trusted publisher for this repository
  (workflow `release.yml`, environment `pypi`), then set the repository variable `PUBLISH_TO_PYPI` to `true`.
  A published version cannot be replaced, so do the first release as a tag you are sure of.
- `pymodbus`, `pyubx2`, `pymavlink`, `crsf-parser` and `cantools` (pinned) are development dependencies: `tests/python/test_independent.py`,
  `test_ubx.py`, `test_mavlink.py`, `test_crsf.py`, `test_j1939.py` and `test_dbc.py` compare the Modbus RTU, UBX, MAVLink 1, CRSF and J1939 models and the DBC importer with them, byte for byte.
- Development tools are pinned to one minor version (`ruff`, `mypy` in `pyproject.toml`) so that a new release of a
  linter does not turn CI red by itself; raise them deliberately.

## Conventions
- C++17, no heap, exceptions or RTTI in `runtime/` and generated code; never add bytes to the wire format.
- Comments describe behaviour; runtime and generated C++ carry Doxygen comments on every public entity.
- Suppressions are a last resort and must be registered in `quality/suppressions.md`.
- Design rationale belongs in docs/DECISIONS.md.

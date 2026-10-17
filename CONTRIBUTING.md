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
  packages `g++-s390x-linux-gnu g++-i686-linux-gnu qemu-user`.
- Sanitizer tests run with ASLR disabled because older sanitizer runtimes hang with the high mmap entropy of WSL2
  kernels.
- Generated-code snapshots: after an intended generator change run
  `WIREFORGE_UPDATE_SNAPSHOTS=1 pytest tests/python/test_generate.py` and review the diff.
- UI types: `npm run gen:types` in `ui/` after schema or API changes.

## Conventions
- C++17, no heap, exceptions or RTTI in `runtime/` and generated code; never add bytes to the wire format.
- Comments describe behaviour; runtime and generated C++ carry Doxygen comments on every public entity.
- Suppressions are a last resort and must be registered in `quality/suppressions.md`.
- Design rationale belongs in docs/DECISIONS.md.

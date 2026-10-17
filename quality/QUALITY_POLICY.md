# Quality policy: zero warnings, zero errors, zero notes

Applies to runtime, generated code, generated test project, and tooling.

## Compiler flags (all must pass with -Werror / /WX)
- GCC/Clang: `-std=c++17 -Wall -Wextra -Wpedantic -Wconversion -Wsign-conversion -Wshadow
  -Wold-style-cast -Wcast-align -Wcast-qual -Wnon-virtual-dtor -Woverloaded-virtual
  -Wnull-dereference -Wdouble-promotion -Wformat=2 -Wundef -Wunused -Wuseless-cast -Wswitch-enum`
- Clang extra: `-Weverything` run in an advisory-then-gating job with a short documented exclusion list.
- MSVC: `/W4 /WX /permissive-`.
## Analyzers (CI gate, any finding fails)
- Portability matrix (`quality/run_portability.sh`): Clang `-Weverything`, clang `scan-build`, a host build with
  unsigned `char`, cross builds with the full warning set for s390x, powerpc (big endian), i686, armhf and aarch64
  running every test suite under qemu; production code and the
  generated self-tests are compiled with `-fno-exceptions -fno-rtti` (`/GR-` on MSVC) in every build; MSVC `/W4 /WX`
  in the CI build matrix.
- clang-tidy (bugprone-*, cert-*, cppcoreguidelines-*, performance-*, portability-*, misc-*, readability-*)
- cppcheck (all checks, `--inline-suppr` disabled)
- Optional, non-gating: `quality/run_misra.sh` runs cppcheck's MISRA C:2012 addon as a rough proxy on C++ code. It is
  not a MISRA C++ or AUTOSAR conformance check and no required check depends on it (docs/CODING_RULES.md).
- GCC `-fanalyzer`, clang `scan-build`, MSVC `/analyze`
- Sanitizers in tests: ASan, UBSan, TSan (separate runs), plus allocation-trap test (no heap).
- Differential testing of generated code against the reference codec and libFuzzer targets (runtime and generated
  code, ASan/UBSan): `quality/run_generated_tests.sh`, `quality/run_fuzz.sh` (docs/CODEGEN.md).
- Python: ruff + mypy --strict; TypeScript: eslint strict + tsc strict.
## Comments and API documentation
- Runtime headers and generated C++ carry Doxygen comments (`@file`, `@brief`, `@param`, `@return`, `@tparam`) on every
  public entity; `doxygen docs/api/Doxyfile` runs with `WARN_AS_ERROR` and is a gate.
- Comments describe behaviour for the user of the code. They never cite decision numbers or spec
  sections (`D3`, `SPEC section 7`); rationale belongs in `docs/DECISIONS.md`. Rule ids that appear in tool
  output (`[SR-6]`) are output, not comments.

## Suppressions
- Default: none. Any suppression requires an entry in `quality/suppressions.md`
  (location, tool, rule, justification, reviewer). Target count: 0. CI fails if an unregistered suppression appears.
## Generated code
- Codegen output is compiled and analyzed in CI for every example project and every platform profile.
- A model that would produce analyzer findings is a generator bug, not a user problem.
- The generated GoogleTest project carries the same compiler flags and clang-tidy configuration, sanitizer presets
  and a coverage target with a threshold (docs/CODEGEN.md). `quality/run_generated_tests.sh` builds it standalone for
  every example and fails below 95 % line coverage of the generated headers.
- Coverage thresholds and results are kept apart:

  | What | Line coverage |
  |------|---------------|
  | Default threshold of a generated test project (`--coverage-threshold`) | 90 % |
  | Gate threshold for the examples (`WIREFORGE_GENERATED_COVERAGE_MIN_LINE`) | 95 % |
  | Result on the examples | 100 % |
  | Runtime and code generator (`quality/run_coverage.sh`, CI job `coverage`) | reported; fails only below `WIREFORGE_COVERAGE_MIN_LINE` when set |

- In this repository `tests/generated/` generates every example at build time; each generated header is a header-check
  translation unit (all warnings, clang-tidy, cppcheck, GCC analyzer), example 02 is built for both profiles, and
  doxygen must accept the generated headers without warnings (`tests/python/test_generate.py`).

## Check exclusions (global, documented rather than suppressed)
- clang-tidy: `bugprone-easily-swappable-parameters` (flags every `(buffer, length)` style API),
  `cppcoreguidelines-avoid-magic-numbers` / `readability-magic-numbers` (wire constants are the domain;
  generated code names them where meaningful), `misc-include-cleaner` (false positives with gtest/libstdc++).
- GCC `-fanalyzer`: applied to production code only (runtime header-check TUs, generated code). GCC 13 reports
  false positives on libstdc++/gtest in C++, so test TUs are excluded. Re-evaluate when upgrading GCC.
- cppcheck: `--enable=warning,style,performance,portability`; `unusedFunction` and `missingInclude` are not
  enabled (header-only library; system headers are not resolved in CI).
- Over-aligned allocations bypass the allocation trap (documented limitation, runtime has no such types).
- Clang `-Weverything` (gating, preset `clang-everything`, runtime + generated code + self-tests): `-Wno-c++98-compat`,
  `-Wno-c++98-compat-pedantic` (C++17 code base), `-Wno-padded` (layout of in-memory structs is not the wire format),
  `-Wno-weak-vtables` (header-only library and generated headers: virtual interfaces such as `Handler` and
  `ITransport` have no translation unit for an out-of-line key function).
- ruff: `CPY001` (no per-file copyright headers), `S603` (all subprocess calls use argument lists, never a shell), docstring-style conflicts (`D203`, `D213`) and formatter conflicts (`COM812`, `ISC001`).

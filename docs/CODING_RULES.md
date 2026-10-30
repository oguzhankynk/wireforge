# Coding rules: what is enforced and how

**This document maps rule categories to the checks that enforce them in WireForge. It is an engineering coverage map,
not a conformance claim. MISRA® and AUTOSAR® rule texts are copyrighted and not reproduced here.**

WireForge's gating rule set is the baseline of docs/SAFETY.md: compiler warnings, clang-tidy, cppcheck, the compiler
and Clang analyzers and the sanitizers (`quality/run_all.sh`). MISRA C++:2023 and AUTOSAR C++14 are not gates.
Conformance to either needs a licensed checker and a deviation process; WireForge does not provide those. A user who
has them runs their own tool on the generated code and the runtime and gives the findings to WireForge.

The reference is MISRA C++:2023, which absorbed most of AUTOSAR C++14. Rule numbers and texts are left out on purpose:
the mapping below is at the level of topics, and every row marked `to verify` must be checked against your licensed
copy before you rely on it.

## Concerns and their enforcement
| Concern | Related rules (examples) | Enforcement | Evidence |
|---------|------------------------|-------------|----------|
| No dynamic memory | MISRA C++:2008 18-4-1, AUTOSAR A18-5-1 | Fixed-capacity containers (`StaticVector`, `FixedString`, rings); allocation-trap tests; symbol check | `no_heap.md`, allocation-trap tests in every runtime suite and in every generated test project |
| No exceptions, no RTTI | | `noexcept` APIs returning `Status`; production code compiled with `-fno-exceptions -fno-rtti` in every build; no `std::array::at` (`wireforge::array_at`) | `no_heap.md`, header-check targets |
| Defined integer and conversion behaviour | | `-Wconversion -Wsign-conversion -Wuseless-cast`, `-Weverything` (Clang), UBSan, 32-bit and big-endian test runs; byte-wise shifts, no type punning | gates in `quality/run_all.sh`, `run_portability.sh` |
| Bounds and lifetime safety | CERT C++ (CTR, EXP, MEM rules via clang-tidy `cert-*`) | clang-tidy `cppcoreguidelines-*`, `bugprone-*`, `cert-*`; ASan; cppcheck; GCC `-fanalyzer`; clang static analyzer | gates, fuzzing (`run_fuzz.sh`) |
| Concurrency | | Lock policies, SPSC rings with acquire/release, TSan stress tests | `tsan` gate |
| Deterministic, reviewable generated code | | Snapshots, deterministic generator, zero-warning compile of generated code, Doxygen for every public entity | `tests/python/snapshots`, doxygen gate |
| Requirements to code to tests traceability | | `Defined in` and `Requirements:` comments, `requirements`/`reqs` in the model, derived low-level requirements, trace report, requirement ids recorded by every generated test | `trace_<project>.md` |
| Correctness of the codec | | Reference codec as oracle: golden vectors, boundary and mutation tests, differential testing, fuzzing, line coverage of generated code (gate 100 %, examples at 100 %) | `run_generated_tests.sh`, `run_fuzz.sh` |
| No suppressed findings | | Every suppression registered (`quality/suppressions.md`), checked in CI | `check_suppressions.py` |

## MISRA C++:2023 topics
Status is `enforced` (a gate fails on a violation), `partially enforced`, `not enforced` (by design, with the
reason) or `not applicable`.

| Topic | Status | Gate or reason | Note |
|-------|--------|----------------|------|
| Unreachable and dead code, unused entities | partially enforced | `-Wunused*`, clang-tidy, cppcheck; 100 % branch and function coverage of generated code | an unreachable branch shows as uncovered |
| Undefined, unspecified and implementation-defined behaviour | partially enforced | UBSan, ASan, `-Wconversion`, 32-bit and big-endian runs, no type punning | to verify against the rule list |
| Lexical conventions (literals, comments, character sets) | partially enforced | clang-format, clang-tidy `readability-*`; integer literals carry a suffix | to verify |
| Conversions and casts | enforced | `-Wconversion -Wsign-conversion -Wuseless-cast`, `-Weverything` on Clang, `cppcoreguidelines-*` | explicit `static_cast` only |
| Expressions: evaluation order, precedence, side effects | partially enforced | `-Wparentheses`, clang-tidy `bugprone-*`, cppcheck | the generated code avoids relying on evaluation order |
| Pointers, arrays and pointer arithmetic | partially enforced | `Span`, `std::array`, `wireforge::array_at`; `cppcoreguidelines-pro-bounds-*` | no raw pointer arithmetic in the library |
| Control flow: one exit point, `goto`, `switch` form | not enforced | the code uses early returns on purpose; no `goto` is written | the generated code has no branches of its own |
| Declarations: hiding, linkage, one definition | partially enforced | `-Wshadow`, clang-tidy `misc-*`, header-check targets | to verify |
| Special member functions, destructors, virtual functions | partially enforced | `cppcoreguidelines-special-member-functions`, `virtual-class-destructor`; protected non-virtual destructors of `Handler` and `ITransport` | |
| Inheritance and polymorphism | partially enforced | clang-tidy `cppcoreguidelines-*`; one virtual interface per handler | to verify |
| Templates | partially enforced | compiler warnings and clang-tidy on every instantiation the tests use | to verify |
| Exception handling | not applicable | built with `-fno-exceptions`; the API returns `Status` | the rules about using exceptions correctly have nothing to apply to |
| Preprocessor | partially enforced | macros only for configuration defines and include guards; clang-tidy `cppcoreguidelines-macro-usage` | |
| Dynamic memory and standard library restrictions | enforced | `no_heap.md` (object-level symbol check), fixed-capacity containers | |
| Concurrency | partially enforced | lock policies, TSan stress tests | to verify |
| Documentation, traceability and deviations | partially enforced | Doxygen gate with warnings as errors, `Defined in` and `Requirements:` comments, suppression register | |

## AUTOSAR C++14 topics without a MISRA C++:2023 counterpart
Only topics MISRA C++:2023 does not cover are listed; the rest are in the table above.

| Topic | Status | Gate or reason | Note |
|-------|--------|----------------|------|
| Documentation of public entities | enforced | Doxygen gate (`WARN_AS_ERROR`) | to verify |
| Selection of standard library containers and I/O | not enforced | the runtime uses its own fixed-capacity containers; no I/O classes in production code | to verify |
| Specific library functions and headers | partially enforced | `no_heap.md` allow list | to verify |

## Advisory run with an open-source checker
`quality/run_misra.sh` runs cppcheck's MISRA C:2012 addon as a rough proxy on C++ code (the open-source addon
implements MISRA C, not MISRA C++ or AUTOSAR). It never fails a build and the CI job `misra` is not a required check.
It reports the findings per rule and writes `build/misra/findings.xml`. Many C rules are meaningless on C++ (for
example the single-exit and implicit-type rules), so read the result as a hint, not as coverage. Set
`WIREFORGE_MISRA_RULE_TEXTS` to a rule text file you are licensed to use to get rule texts in the report; WireForge
never stores one.

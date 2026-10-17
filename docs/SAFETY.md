# Safety and traceability

## Evidence (`quality/run_evidence.sh`, CI artifact `evidence`)
| File | Shows |
|------|-------|
| `no_heap.md` | Every runtime header, every generated header of every example and profile, and the generated self-tests, compiled with `-fno-exceptions -fno-rtti -DWIREFORGE_NO_STD_MUTEX` at `-O0` and `-O2`: the external symbols of each object. Anything outside a short allow list (compiler runtime, string functions; printing in self-tests) fails, so no object references `operator new/delete`, `malloc`/`free`, exception or RTTI support or threads. |
| `trace_<project>.md`, `.csv` | Every enum, type, message, field and interface: model source (`file:line`, the ICD row), C++ symbol, generated `file:line`, generated tests that exercise it. Messages and interfaces without tests are listed. |
| `icd_<project>.md` | Layout per profile: offsets, sizes, bit positions, frame sizes, link RAM. |
| `hashes.txt` | Model hash (sha256 of the definition content) of every project; the same hash is in the banner of every generated file, so generated code is tied to the exact model. |

Generated code carries the trace inline as well: each struct is documented with `Defined in <file>:<line> (<JSON
pointer>)` and each member with its `file:line`.

## Coding-rule mapping
WireForge does not claim conformance to MISRA C++ or AUTOSAR C++14 (docs/DECISIONS.md D12). The table maps the
concerns behind such rules to what enforces them here; rule numbers are examples of related rules.

| Concern | Related rules (examples) | Enforcement | Evidence |
|---------|------------------------|-------------|----------|
| No dynamic memory | MISRA C++:2008 18-4-1, AUTOSAR A18-5-1 | Fixed-capacity containers (`StaticVector`, `FixedString`, rings); allocation-trap tests; symbol check | `no_heap.md`, allocation-trap tests in every runtime suite |
| No exceptions, no RTTI | | `noexcept` APIs returning `Status`; production code compiled with `-fno-exceptions -fno-rtti` in every build; no `std::array::at` (`wireforge::array_at`) | `no_heap.md`, header-check targets |
| Defined integer and conversion behaviour | | `-Wconversion -Wsign-conversion -Wuseless-cast`, `-Weverything` (Clang), UBSan, 32-bit and big-endian test runs; byte-wise shifts, no type punning | gates in `quality/run_all.sh`, `run_portability.sh` |
| Bounds and lifetime safety | CERT C++ (CTR, EXP, MEM rules via clang-tidy `cert-*`) | clang-tidy `cppcoreguidelines-*`, `bugprone-*`, `cert-*`; ASan; cppcheck; GCC `-fanalyzer`; clang static analyzer | gates, fuzzing (`run_fuzz.sh`) |
| Concurrency | | Lock policies, SPSC rings with acquire/release, TSan stress tests | `tsan` gate |
| Deterministic, reviewable generated code | | Snapshots, deterministic generator, zero-warning compile of generated code, Doxygen for every public entity | `tests/python/snapshots`, doxygen gate |
| Requirements to code to tests traceability | | `Defined in` comments, trace report, generated tests named after messages and fields | `trace_<project>.md` |
| Correctness of the codec | | Reference codec as oracle: golden vectors, boundary and mutation tests, differential testing, fuzzing, line coverage of generated code (gate 95 %, examples at 100 %) | `run_generated_tests.sh`, `run_fuzz.sh` |
| No suppressed findings | | Every suppression registered (`quality/suppressions.md`), checked in CI | `check_suppressions.py` |

Bare-metal targets define `WIREFORGE_NO_STD_MUTEX` (or `WIREFORGE_DEFAULT_LOCK`); the generated `Handler` and the
runtime `ITransport` have protected non-virtual destructors, so no deleting destructor (and no `operator delete`) is
emitted.

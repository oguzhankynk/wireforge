# Safety and traceability

## Evidence (`quality/run_evidence.sh`, CI artifact `evidence`)
| File | Shows |
|------|-------|
| `no_heap.md` | Every runtime header, every generated header of every example and profile, and the generated self-tests, compiled with `-fno-exceptions -fno-rtti -DWIREFORGE_NO_STD_MUTEX` at `-O0` and `-O2`: the external symbols of each object. Anything outside a short allow list (compiler runtime, string functions; printing in self-tests) fails, so no object references `operator new/delete`, `malloc`/`free`, exception or RTTI support or threads. |
| `trace_<project>.md`, `.csv` | Every enum, checksum, type, message, field and interface: model source (`file:line`, the ICD row), C++ symbol, generated `file:line`, the high-level requirements (`reqs`) and derived low-level requirements (`llrs`) of the element, and the generated tests that verify them. A second table lists every low-level requirement with its parents and test count. Messages and interfaces without tests, LLRs without tests, LLRs verified by the runtime tests, requirements without model elements and derived LLRs are listed. |
| `icd_<project>.md` | Layout per profile: offsets, sizes, bit positions, frame sizes, link RAM. |
| `hashes.txt` | Model hash (sha256 of the definition content) of every project; the same hash is in the banner of every generated file, so generated code is tied to the exact model. |

Generated code carries the trace inline as well: each struct is documented with `Defined in <file>:<line> (<JSON
pointer>)` and each member with its `file:line`. In a project with a `requirements` catalog, every struct, enum,
checksum and interface also lists `Requirements: <HLR ids>; <LLR ids>`, and every generated test records the LLR ids it
verifies (`RecordProperty("requirements", ...)`, docs/SCHEMA_SPEC.md section 17).

A user type's own `encode`/`decode` is outside the no-heap check's object-file inspection unless its header is
compiled into an example the safety-evidence pipeline builds; this is the same "informative, not a conformance
claim" boundary as the rest of this table, not a new exception.

## Independent expectations
The expected result of most generated tests is computed by the reference codec, which reads the model the same way as
the code generator does. A misreading would show in both, so the tests could not catch it. Requirement vectors are
bytes and values you write from the ICD; the reference codec only confirms them at validation time, and the generated
tests compare with your values. That is the common-mode argument for keeping the two sources apart (docs/LIFECYCLE.md).

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
| Requirements to code to tests traceability | | `Defined in` and `Requirements:` comments, `requirements`/`reqs` in the model, derived low-level requirements, trace report, requirement ids recorded by every generated test | `trace_<project>.md` |
| Correctness of the codec | | Reference codec as oracle: golden vectors, boundary and mutation tests, differential testing, fuzzing, line coverage of generated code (gate 95 %, examples at 100 %) | `run_generated_tests.sh`, `run_fuzz.sh` |
| No suppressed findings | | Every suppression registered (`quality/suppressions.md`), checked in CI | `check_suppressions.py` |

Bare-metal targets define `WIREFORGE_NO_STD_MUTEX` (or `WIREFORGE_DEFAULT_LOCK`); the generated `Handler` and the
runtime `ITransport` have protected non-virtual destructors, so no deleting destructor (and no `operator delete`) is
emitted.

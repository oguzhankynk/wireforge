# 5. Tests

**What we will do:** look at the tests WireForge generated for `drone`, measure the coverage of the generated code and
read the results.

## What is tested
<!-- tutorial: run -->
```sh
wireforge test drone -o build/drone
ls build/drone/drone/generated_tests
```
| File | What it checks |
|------|----------------|
| `message_test.cpp` | per message: defaults, round trips of values the generator picks, limits, every decode error |
| `golden_test.cpp` | golden frames from the reference codec: `encode_frame` gives the same bytes, decoding gives the same values |
| `interface_test.cpp` | per interface: dispatch of every message, byte-by-byte delivery, garbage before a frame, broken frames, trimmed frames |
| `diff_harness.cpp` | a differential test: random values and frames through the generated code and the reference codec |
| `fuzz_target.cpp` | a libFuzzer target over the decoder (`-DWIREFORGE_FUZZ=ON` with Clang) |
| `selftest.cpp` | a self-test without a framework, heap or exceptions, for embedded targets |

Every expectation comes from WireForge's reference codec, an independent Python implementation of the encoding rules,
not from the generated code itself. For MAVLink 2 the reference codec in turn is checked against pymavlink.

## Coverage
<!-- tutorial: run -->
```sh
python -m pip install gcovr
wireforge test drone -o build/drone --coverage
```
The coverage build fails unless every line, branch and function of the generated code is run by the tests (100 %).
The generated code has no branch of its own that a test cannot reach, so the gate holds for any model.

## Results
The coverage build writes `results/` next to the build: the JUnit XML of the tests and the coverage reports. Each test
records the requirements it verifies, which chapter 10 turns into a traceability matrix:
```text
TEST(Interface_link, TrimsTrailingZeros) {
    ::testing::Test::RecordProperty("requirements", "LLR-link.FRAME_TRIM");
```

**More:** [generated test project](../CODEGEN.md#generated-test-project), [quality policy](https://github.com/oguzhankynk/wireforge/blob/master/quality/QUALITY_POLICY.md).

**If something goes wrong:** `--coverage` needs `gcovr` and GCC or Clang. A failing generated test is a defect of
WireForge (the model was valid); please report it with the model.

Next: [6. Versions and CI](06_versions_and_ci.md)

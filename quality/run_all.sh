#!/usr/bin/env bash
# Local equivalent of the CI quality gates. Any finding fails the run.
set -euo pipefail
cd "$(dirname "$0")/.."
# Older libasan/libtsan (GCC < 13) hang or abort when the kernel uses high mmap ASLR entropy
# (vm.mmap_rnd_bits > 28, e.g. WSL2 6.x kernels). Running sanitizer tests without ASLR is harmless.
no_aslr=(setarch "$(uname -m)" -R)

echo "== format ==";        clang-format --dry-run --Werror $(git ls-files '*.cpp' '*.hpp' 2>/dev/null || find runtime tests -name '*.cpp' -o -name '*.hpp')
echo "== build+test ==";    cmake --preset dev && cmake --build --preset dev && ctest --preset dev
echo "== asan+ubsan ==";    cmake --preset asan-ubsan && cmake --build --preset asan-ubsan && "${no_aslr[@]}" ctest --preset asan-ubsan
echo "== tsan ==";          cmake --preset tsan && cmake --build --preset tsan && "${no_aslr[@]}" ctest --preset tsan
echo "== generated test projects + differential =="; quality/run_generated_tests.sh
echo "== fuzz ==";          quality/run_fuzz.sh
echo "== wireshark ==";     quality/run_wireshark.sh
echo "== portability ==";   quality/run_portability.sh
echo "== evidence ==";      quality/run_evidence.sh
echo "== quickstart ==";    quality/run_quickstart.sh
echo "== gcc analyzer ==";  cmake --preset gcc-analyzer && cmake --build --preset gcc-analyzer
echo "== clang-tidy ==";    python3 quality/run_clang_tidy.py build/dev
echo "== cppcheck ==";      quality/run_cppcheck.sh build/dev
echo "== doxygen ==";      doxygen docs/api/Doxyfile
echo "== python ==";        ruff format --check . && ruff check . && mypy && python3 -m pytest -q
echo "== ui ==";            (cd ui && npm ci --no-audit --no-fund --silent && npm run -s typecheck && npm run -s lint \
                             && npm run -s check:types && npm test --silent && npx vite build --logLevel warn)
echo "== suppressions =="; python3 quality/check_suppressions.py
echo "ALL GATES PASSED"

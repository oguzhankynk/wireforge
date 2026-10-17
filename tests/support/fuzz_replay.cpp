// Replays the fuzz target of a generated project with deterministic inputs, so that the target is
// built, run and measured by the normal test run without libFuzzer.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <vector>

extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size);

namespace {

constexpr std::size_t kRounds = 48U;
constexpr std::size_t kMaxSize = 300U;
constexpr std::uint32_t kSeed = 0x9E3779B9U;

std::uint32_t next(std::uint32_t& state) noexcept {
    state ^= state << 13U;
    state ^= state >> 17U;
    state ^= state << 5U;
    return state;
}

TEST(FuzzReplay, EveryTargetSurvivesPseudoRandomInputs) {
    std::uint32_t state = kSeed;
    std::size_t runs = 0U;
    for (std::size_t selector = 0U; selector < 256U; ++selector) {
        for (std::size_t round = 0U; round < kRounds; ++round) {
            std::vector<std::uint8_t> input(next(state) % kMaxSize);
            std::generate(input.begin(), input.end(),
                          [&state] { return static_cast<std::uint8_t>(next(state)); });
            if (!input.empty()) {
                input[0] = static_cast<std::uint8_t>(selector);
            }
            EXPECT_EQ(LLVMFuzzerTestOneInput(input.data(), input.size()), 0);
            ++runs;
        }
    }
    EXPECT_EQ(runs, 256U * kRounds);
}

TEST(FuzzReplay, EmptyAndZeroInputs) {
    EXPECT_EQ(LLVMFuzzerTestOneInput(nullptr, 0U), 0);
    for (std::size_t size = 1U; size < kMaxSize; size += 7U) {
        const std::vector<std::uint8_t> zeros(size, 0U);
        const std::vector<std::uint8_t> ones(size, 0xFFU);
        EXPECT_EQ(LLVMFuzzerTestOneInput(zeros.data(), zeros.size()), 0);
        EXPECT_EQ(LLVMFuzzerTestOneInput(ones.data(), ones.size()), 0);
    }
}

} // namespace

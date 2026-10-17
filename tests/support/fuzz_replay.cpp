// Replays the fuzz target of a generated project with deterministic inputs, so that the target is
// built, run and measured by the normal test run without libFuzzer.
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <gtest/gtest.h>
#include <iterator>
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

using Bytes = std::vector<std::uint8_t>;

// The golden payloads and frames of the project (the first byte selects the fuzz target).
std::vector<Bytes> read_seeds() {
    const std::filesystem::directory_iterator first(WIREFORGE_FUZZ_SEEDS_DIR);
    std::vector<std::filesystem::path> files;
    std::transform(std::filesystem::begin(first), std::filesystem::end(first),
                   std::back_inserter(files), [](const auto& entry) { return entry.path(); });
    std::sort(files.begin(), files.end());
    std::vector<Bytes> seeds;
    for (const auto& file : files) {
        std::ifstream stream(file, std::ios::binary);
        seeds.emplace_back(std::istreambuf_iterator<char>(stream),
                           std::istreambuf_iterator<char>());
    }
    return seeds;
}

void run(const Bytes& input) {
    EXPECT_EQ(LLVMFuzzerTestOneInput(input.data(), input.size()), 0);
}

TEST(FuzzReplay, MutatedGoldenInputs) {
    const std::vector<Bytes> seeds = read_seeds();
    ASSERT_FALSE(seeds.empty());
    std::uint32_t state = kSeed;
    for (const Bytes& seed : seeds) {
        run(seed);
        // Truncated at every length.
        for (std::size_t size = 0U; size < seed.size(); ++size) {
            run(Bytes(seed.begin(), seed.begin() + static_cast<std::ptrdiff_t>(size)));
        }
        // One byte changed, each byte in turn, two different ways.
        for (std::size_t index = 1U; index < seed.size(); ++index) {
            Bytes changed = seed;
            changed[index] ^= 0xFFU;
            run(changed);
            changed[index] = static_cast<std::uint8_t>(next(state));
            run(changed);
        }
        // Garbage before the frame, the frame twice, and the frame split by garbage.
        for (std::size_t junk = 1U; junk <= 6U; ++junk) {
            Bytes shifted(seed.begin(), seed.begin() + 1);
            for (std::size_t count = 0U; count < junk; ++count) {
                shifted.push_back(static_cast<std::uint8_t>(next(state)));
            }
            shifted.insert(shifted.end(), seed.begin() + 1, seed.end());
            run(shifted);
            Bytes twice = shifted;
            twice.insert(twice.end(), seed.begin() + 1, seed.end());
            run(twice);
            Bytes split = seed;
            split.insert(split.begin() + static_cast<std::ptrdiff_t>(1U + junk % seed.size()),
                         shifted.begin() + 1,
                         shifted.begin() + 1 + static_cast<std::ptrdiff_t>(junk));
            run(split);
        }
    }
}

// Every target with inputs of every size up to a bit more than the largest frame, so that reads
// longer than a frame reach the links of all interfaces.
TEST(FuzzReplay, EveryTargetWithInputsOfEverySize) {
    std::uint32_t state = kSeed;
    for (std::size_t selector = 0U; selector < 64U; ++selector) {
        for (std::size_t size = 1U; size <= 420U; size += 1U + size / 100U) {
            Bytes input(size);
            std::generate(input.begin(), input.end(),
                          [&state] { return static_cast<std::uint8_t>(next(state)); });
            input[0] = static_cast<std::uint8_t>(selector);
            run(input);
        }
    }
}

// Every chunk size of the stream, for every target: a byte value v makes the target read chunks of
// v + 1 bytes, so each read length from one byte to more than a frame occurs.
TEST(FuzzReplay, EveryReadLength) {
    for (std::size_t selector = 0U; selector < 64U; ++selector) {
        for (std::size_t value = 0U; value < 255U; ++value) {
            Bytes input(1U + 3U * (value + 1U), static_cast<std::uint8_t>(value));
            input[0] = static_cast<std::uint8_t>(selector);
            run(input);
        }
    }
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

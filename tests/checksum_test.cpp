#include "support/alloc_trap.hpp"
#include "wireforge/checksum.hpp"
#include "wireforge/span.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>

namespace {

using wireforge::Crc;
using wireforge::Span;

constexpr std::array<std::uint8_t, 9> kCheckInput{'1', '2', '3', '4', '5', '6', '7', '8', '9'};
constexpr Span<const std::uint8_t> check_input() {
    return Span<const std::uint8_t>(kCheckInput);
}

// Catalog entries (reveng.sourceforge.net CRC catalogue), table and table-less variants.
template <bool Table> using Crc8 = Crc<8U, 0x07U, 0x00U, false, false, 0x00U, Table>;
template <bool Table> using Crc16Ccitt = Crc<16U, 0x1021U, 0xFFFFU, false, false, 0x0000U, Table>;
template <bool Table> using Crc16Arc = Crc<16U, 0x8005U, 0x0000U, true, true, 0x0000U, Table>;
template <bool Table> using Crc16Kermit = Crc<16U, 0x1021U, 0x0000U, true, true, 0x0000U, Table>;
template <bool Table>
using Crc32Iso = Crc<32U, 0x04C11DB7U, 0xFFFFFFFFU, true, true, 0xFFFFFFFFU, Table>;
template <bool Table>
using Crc32Bzip2 = Crc<32U, 0x04C11DB7U, 0xFFFFFFFFU, false, false, 0xFFFFFFFFU, Table>;
template <bool Table>
using Crc32C = Crc<32U, 0x1EDC6F41U, 0xFFFFFFFFU, true, true, 0xFFFFFFFFU, Table>;
template <bool Table>
using Crc64Xz =
    Crc<64U, 0x42F0E1EBA9EA3693U, ~std::uint64_t{0U}, true, true, ~std::uint64_t{0U}, Table>;
// Widths below 8 and mixed reflection exercise the left-aligned register.
template <bool Table> using Crc5Usb = Crc<5U, 0x05U, 0x1FU, true, true, 0x1FU, Table>;
template <bool Table> using Crc3Gsm = Crc<3U, 0x3U, 0x0U, false, false, 0x7U, Table>;
template <bool Table> using Crc12Umts = Crc<12U, 0x80FU, 0x000U, false, true, 0x000U, Table>;

static_assert(wireforge::Crc32::compute(check_input()) == 0xCBF43926U, "constexpr CRC-32");
static_assert(Crc64Xz<false>::compute(check_input()) == 0x995DC9BBDF1939FAU, "constexpr CRC-64");

template <typename C> void expect_check(std::uint64_t expected) {
    static_assert(wireforge::is_checksum_v<C>, "catalog CRCs satisfy the contract");
    EXPECT_EQ(C::compute(check_input()), expected);
    EXPECT_EQ(wireforge::compute_checksum<C>(check_input()), expected);
}

template <bool Table> void expect_catalog() {
    expect_check<Crc8<Table>>(0xF4U);
    expect_check<Crc16Ccitt<Table>>(0x29B1U);
    expect_check<Crc16Arc<Table>>(0xBB3DU);
    expect_check<Crc16Kermit<Table>>(0x2189U);
    expect_check<Crc32Iso<Table>>(0xCBF43926U);
    expect_check<Crc32Bzip2<Table>>(0xFC891918U);
    expect_check<Crc32C<Table>>(0xE3069283U);
    expect_check<Crc64Xz<Table>>(0x995DC9BBDF1939FAU);
    expect_check<Crc5Usb<Table>>(0x19U);
    expect_check<Crc3Gsm<Table>>(0x4U);
    expect_check<Crc12Umts<Table>>(0xDAFU);
}

TEST(Crc, CatalogCheckValuesWithTable) {
    ::testing::Test::RecordProperty("requirements", "RTR-checksum-001");
    expect_catalog<true>();
}

TEST(Crc, CatalogCheckValuesTableless) {
    ::testing::Test::RecordProperty("requirements", "RTR-checksum-001");
    expect_catalog<false>();
}

TEST(Crc, AliasesAndSizes) {
    ::testing::Test::RecordProperty("requirements", "RTR-checksum-002");
    EXPECT_EQ(wireforge::Crc32::compute(check_input()), 0xCBF43926U);
    EXPECT_EQ(wireforge::Crc16CcittFalse::compute(check_input()), 0x29B1U);
    EXPECT_EQ(wireforge::Crc32::kSize, 4U);
    EXPECT_EQ(Crc5Usb<true>::kSize, 1U);
    EXPECT_EQ(Crc12Umts<true>::kSize, 2U);
    EXPECT_EQ(Crc64Xz<true>::kSize, 8U);
}

TEST(Crc, IncrementalUpdateMatchesOneShot) {
    ::testing::Test::RecordProperty("requirements", "RTR-checksum-003");
    for (std::size_t split = 0U; split <= kCheckInput.size(); ++split) {
        auto state = wireforge::Crc32::init();
        wireforge::Crc32::update(state, check_input().first(split));
        wireforge::Crc32::update(state, check_input().subspan(split));
        EXPECT_EQ(wireforge::Crc32::finalize(state), 0xCBF43926U) << "split " << split;
    }
}

TEST(Crc, EmptyInputGivesInitXorOut) {
    ::testing::Test::RecordProperty("requirements", "RTR-checksum-003");
    EXPECT_EQ(wireforge::Crc32::compute(Span<const std::uint8_t>()), 0U);
    EXPECT_EQ(wireforge::Crc16CcittFalse::compute(Span<const std::uint8_t>()), 0xFFFFU);
}

// Example of a user-defined checksum: Fletcher-16.
struct Fletcher16 {
    static constexpr std::size_t kSize = 2U;
    struct State {
        std::uint16_t sum1;
        std::uint16_t sum2;
    };
    static constexpr State init() noexcept { return State{0U, 0U}; }
    static constexpr void update(State& state, Span<const std::uint8_t> bytes) noexcept {
        for (const std::uint8_t byte : bytes) {
            state.sum1 = static_cast<std::uint16_t>((state.sum1 + byte) % 255U);
            state.sum2 = static_cast<std::uint16_t>((state.sum2 + state.sum1) % 255U);
        }
    }
    static constexpr std::uint64_t finalize(const State& state) noexcept {
        return (static_cast<std::uint64_t>(state.sum2) << 8U) | state.sum1;
    }
};
static_assert(wireforge::is_checksum_v<Fletcher16>, "custom checksum satisfies the contract");

TEST(CustomChecksum, Fletcher16CheckValue) {
    ::testing::Test::RecordProperty("requirements", "RTR-checksum-004");
    EXPECT_EQ(wireforge::compute_checksum<Fletcher16>(check_input()), 0x1EDEU);
}

// Contract violations are detected at compile time.
struct MissingFinalize {
    static constexpr std::size_t kSize = 1U;
    struct State {};
    static State init() noexcept { return {}; }
    static void update(State& /*state*/, Span<const std::uint8_t> /*bytes*/) noexcept {}
};
struct WrongSize {
    static constexpr std::size_t kSize = 9U;
    struct State {};
    static State init() noexcept { return {}; }
    static void update(State& /*state*/, Span<const std::uint8_t> /*bytes*/) noexcept {}
    static std::uint64_t finalize(const State& /*state*/) noexcept { return 0U; }
};
struct Throwing {
    static constexpr std::size_t kSize = 1U;
    struct State {};
    static State init() { return {}; }
    static void update(State& /*state*/, Span<const std::uint8_t> /*bytes*/) noexcept {}
    static std::uint64_t finalize(const State& /*state*/) noexcept { return 0U; }
};
static_assert(!wireforge::is_checksum_v<MissingFinalize>, "finalize is required");
static_assert(!wireforge::is_checksum_v<WrongSize>, "kSize must be 1..8");
static_assert(!wireforge::is_checksum_v<Throwing>, "contract functions are noexcept");
static_assert(Fletcher16::kSize == 2U && MissingFinalize::kSize == 1U && WrongSize::kSize == 9U &&
              Throwing::kSize == 1U);
static_assert(!wireforge::is_checksum_v<int>, "not a checksum");

TEST(Checksum, NoHeapAllocation) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-005");
    const wireforge::testing::AllocationTrap trap;
    EXPECT_EQ(wireforge::Crc32::compute(check_input()), 0xCBF43926U);
    EXPECT_EQ(Crc32C<false>::compute(check_input()), 0xE3069283U);
    EXPECT_EQ(trap.allocations(), 0U);
}

} // namespace

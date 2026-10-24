#include "wireforge/payload_trim.hpp"
#include "wireforge/span.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>

namespace {

using wireforge::Span;
using wireforge::ZeroExtended;
using Bytes = std::array<std::uint8_t, 6>;

Span<const std::uint8_t> view(const Bytes& bytes, std::size_t size) {
    return Span<const std::uint8_t>(bytes.data(), size);
}

TEST(PayloadTrim, DropsTrailingZerosButKeepsTheFirstByte) {
    ::testing::Test::RecordProperty("requirements", "RTR-payload_trim-001");
    constexpr Bytes kSome{1U, 0U, 2U, 0U, 0U, 0U};
    EXPECT_EQ(wireforge::trimmed_size(view(kSome, 6U)), 3U);
    constexpr Bytes kFull{1U, 2U, 3U, 4U, 5U, 6U};
    EXPECT_EQ(wireforge::trimmed_size(view(kFull, 6U)), 6U);
    constexpr Bytes kZeros{};
    EXPECT_EQ(wireforge::trimmed_size(view(kZeros, 6U)), 1U);
    EXPECT_EQ(wireforge::trimmed_size(view(kZeros, 0U)), 0U);
    static_assert(wireforge::trimmed_size(Span<const std::uint8_t>(kSome.data(), 6U)) == 3U);
}

TEST(PayloadTrim, ExtendsShortPayloadsWithZeros) {
    ::testing::Test::RecordProperty("requirements", "RTR-payload_trim-002");
    constexpr Bytes kSent{7U, 8U, 9U, 9U, 9U, 9U};
    const ZeroExtended<5U> shorter(view(kSent, 2U));
    const Span<const std::uint8_t> full = shorter.span();
    ASSERT_EQ(full.size(), 5U);
    EXPECT_EQ(full[0], 7U);
    EXPECT_EQ(full[1], 8U);
    EXPECT_EQ(full[2], 0U);
    EXPECT_EQ(full[4], 0U);

    // A payload of the full size or longer is passed on unchanged (trailing bytes stay visible).
    const ZeroExtended<5U> exact(view(kSent, 5U));
    EXPECT_EQ(exact.span().data(), kSent.data());
    EXPECT_EQ(exact.span().size(), 5U);
    const ZeroExtended<5U> longer(view(kSent, 6U));
    EXPECT_EQ(longer.span().size(), 6U);
    const ZeroExtended<0U> empty(view(kSent, 0U));
    EXPECT_EQ(empty.span().size(), 0U);
}

} // namespace

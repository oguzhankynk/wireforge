#include "edge_cases/model.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <limits>

namespace {

using demo::edge::Everything;
using demo::edge::Inner;
using demo::edge::Level;
using demo::edge::Packed;
using wireforge::Status;
using wireforge::type_tag;

// magic 4 (wire only), big 8, packed 2 (one 16-bit group), inner 1..5, n 1, maybeEmpty 0..16,
// nameLen 1, name 0..16.
static_assert(min_wire_size(type_tag<Everything>) == 17U, "minimum size");
static_assert(max_wire_size(type_tag<Everything>) == 4U + 8U + 2U + 5U + 1U + 16U + 1U + 16U,
              "maximum size");
static_assert(max_wire_size(type_tag<Packed>) == 2U, "a 16-bit group is two bytes");

TEST(GeneratedEdgeCases, WireOnlyFieldsAreNotMembers) {
    Everything value{};
    value.big = std::numeric_limits<std::uint64_t>::max();
    ASSERT_EQ(value.name.assign("edge"), Status::Ok);
    ASSERT_EQ(value.maybeEmpty.resize(8U), Status::Ok);
    EXPECT_EQ(validate(value), Status::Ok);
}

TEST(GeneratedEdgeCases, SignedBitFieldsAreRangeChecked) {
    Packed packed{};
    packed.delta = -16;
    EXPECT_EQ(validate(packed), Status::Ok);
    packed.delta = 16;
    EXPECT_EQ(validate(packed), Status::OutOfRange);
    packed.delta = -17;
    EXPECT_EQ(validate(packed), Status::OutOfRange);
    packed.delta = 0;
    packed.level = Level::critical;
    EXPECT_EQ(validate(packed), Status::Ok);
}

TEST(GeneratedEdgeCases, OptionalFieldElementsAreChecked) {
    Inner inner{};
    EXPECT_TRUE(inner.value.empty());
    ASSERT_EQ(inner.value.push_back(1001), Status::Ok);
    EXPECT_EQ(validate(inner), Status::OutOfRange);
    inner.value[0] = -1000;
    EXPECT_EQ(validate(inner), Status::Ok);
}

} // namespace

namespace {

using Buffer = std::array<std::uint8_t, 64>;

Buffer encode_everything(const Everything& value, std::size_t& size) {
    Buffer buffer{};
    EXPECT_EQ(serialize(value, wireforge::Span<std::uint8_t>(buffer), size), Status::Ok);
    return buffer;
}

TEST(GeneratedEdgeCasesCodec, ConstAndTruncation) {
    std::size_t size = 0U;
    Buffer buffer = encode_everything(Everything{}, size);
    EXPECT_EQ(buffer.at(0), 0xBEU) << "little-endian 0xCAFEBABE";
    Everything decoded{};
    EXPECT_EQ(deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size - 1U), decoded),
              Status::Truncated);
    EXPECT_EQ(deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size + 1U), decoded),
              Status::TrailingBytes);
    buffer.at(0) = 0U;
    EXPECT_EQ(deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size), decoded),
              Status::ConstMismatch);
}

TEST(GeneratedEdgeCasesCodec, CountsAreCheckedOnDecode) {
    Everything value{};
    ASSERT_EQ(value.maybeEmpty.resize(2U), Status::Ok);
    std::size_t size = 0U;
    Buffer buffer = encode_everything(value, size);
    // Offset of n: magic 4 + big 8 + packed 2 + inner.present 1 = 15.
    EXPECT_EQ(buffer.at(15), 2U);
    buffer.at(15) = 9U;
    Everything decoded{};
    EXPECT_EQ(deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size), decoded),
              Status::CountExceedsMax);
}

TEST(GeneratedEdgeCasesCodec, RangeChecksFollowTheEncoding) {
    Everything value{};
    value.packed.delta = 15;
    std::size_t size = 0U;
    Buffer buffer = encode_everything(value, size);
    // Decode checks are off for this project (range_check.decode: false): a bad enum decodes.
    buffer.at(12) = 0xFFU;
    buffer.at(13) = 0xFFU;
    Everything decoded{};
    EXPECT_EQ(deserialize(wireforge::Span<const std::uint8_t>(buffer.data(), size), decoded),
              Status::Ok);
    EXPECT_EQ(validate(decoded), Status::Ok) << "all 2-bit levels are members";
    value.packed.delta = 16;
    EXPECT_EQ(serialize(value, wireforge::Span<std::uint8_t>(buffer), size), Status::OutOfRange);
    EXPECT_EQ(size, 0U);
}

} // namespace

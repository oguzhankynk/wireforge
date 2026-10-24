#include "support/alloc_trap.hpp"
#include "wireforge/checksum.hpp"
#include "wireforge/codec_support.hpp"
#include "wireforge/span.hpp"
#include "wireforge/static_vector.hpp"
#include "wireforge/status.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <string_view>

namespace {

using wireforge::Decoder;
using wireforge::Encoder;
using wireforge::Encoding;
using wireforge::Span;
using wireforge::Status;
using Bytes = std::array<std::uint8_t, 16>;

enum class Color : std::uint8_t { Red = 1, Blue = 2 };

constexpr Encoding kLittle{wireforge::Endian::Little, wireforge::BitOrder::LsbFirst, true, true,
                           false};

TEST(CodecSupport, WritesEveryWireType) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-001");
    Bytes buffer{};
    Encoder encoder(Span<std::uint8_t>(buffer), kLittle);
    encoder.write(true);
    encoder.write('A');
    encoder.write(Color::Blue);
    encoder.write(std::int16_t{-2});
    encoder.write(1.0F);
    EXPECT_EQ(encoder.status(), Status::Ok);
    EXPECT_EQ(encoder.position(), 9U);
    Decoder decoder(Span<const std::uint8_t>(buffer.data(), 9U), kLittle);
    bool flag = false;
    char letter = '\0';
    Color color = Color::Red;
    std::int16_t number = 0;
    float ratio = 0.0F;
    decoder.read(flag);
    decoder.read(letter);
    decoder.read(color);
    decoder.read(number);
    decoder.read(ratio);
    decoder.finish();
    EXPECT_EQ(decoder.status(), Status::Ok);
    EXPECT_TRUE(flag);
    EXPECT_EQ(letter, 'A');
    EXPECT_EQ(color, Color::Blue);
    EXPECT_EQ(number, -2);
    EXPECT_EQ(ratio, 1.0F);
}

TEST(CodecSupport, StickyErrorStopsFurtherWrites) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-002");
    std::array<std::uint8_t, 2> buffer{};
    Encoder encoder(Span<std::uint8_t>(buffer), Encoding{});
    encoder.write(std::uint32_t{1});
    encoder.write(std::uint8_t{7});
    EXPECT_EQ(encoder.status(), Status::BufferTooSmall);
    EXPECT_EQ(encoder.position(), 0U);
    encoder.check(Status::OutOfRange);
    EXPECT_EQ(encoder.status(), Status::BufferTooSmall) << "the first error is kept";
}

TEST(CodecSupport, CountsAndValueChecks) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-003");
    Bytes buffer{};
    Encoder encoder(
        Span<std::uint8_t>(buffer),
        Encoding{wireforge::Endian::Big, wireforge::BitOrder::MsbFirst, false, true, false});
    encoder.check_value(Status::OutOfRange);
    EXPECT_EQ(encoder.status(), Status::Ok) << "encode checks are disabled";
    encoder.write_count<bool>(1U);
    encoder.write_count<std::uint8_t>(300U);
    EXPECT_EQ(encoder.status(), Status::OutOfRange);
    Encoder flags(Span<std::uint8_t>(buffer), Encoding{});
    flags.write_count<bool>(2U);
    EXPECT_EQ(flags.status(), Status::OutOfRange);
}

TEST(CodecSupport, BoolMustBeZeroOrOne) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-004");
    const std::array<std::uint8_t, 1> two{2U};
    Decoder decoder(Span<const std::uint8_t>(two), Encoding{});
    bool flag = false;
    decoder.read(flag);
    EXPECT_EQ(decoder.status(), Status::OutOfRange);
}

TEST(CodecSupport, TextResizeAndFill) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-005");
    const std::array<std::uint8_t, 6> bytes{'a', 'b', 0U, 0U, 'c', 'd'};
    Decoder decoder(Span<const std::uint8_t>(bytes), Encoding{});
    wireforge::FixedString<4> fixed;
    decoder.read_text(fixed, 4U, true);
    EXPECT_EQ(fixed.view(), std::string_view("ab")) << "trailing zeros are dropped";
    EXPECT_EQ(decoder.fill_count(4U), 0U);
    EXPECT_EQ(decoder.status(), Status::InvalidLength);
    Decoder small(Span<const std::uint8_t>(bytes), Encoding{});
    wireforge::FixedString<2> tiny;
    small.read_text(tiny, 3U, false);
    EXPECT_EQ(small.status(), Status::CountExceedsMax);
    Decoder counted(Span<const std::uint8_t>(bytes), Encoding{});
    wireforge::StaticVector<std::uint8_t, 2> values;
    counted.resize(values, 3U, 0U);
    EXPECT_EQ(counted.status(), Status::CountExceedsMax);
    Decoder minimum(Span<const std::uint8_t>(bytes), Encoding{});
    minimum.resize(values, 0U, 1U);
    EXPECT_EQ(minimum.status(), Status::CountExceedsMax);
    Decoder rest(Span<const std::uint8_t>(bytes), Encoding{});
    EXPECT_EQ(rest.fill_count(2U), 3U);
    EXPECT_EQ(rest.fill_count(0U), 0U);
}

TEST(CodecSupport, BitGroupsConstsAndChecksums) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-006");
    Bytes buffer{};
    Encoder encoder(Span<std::uint8_t>(buffer), Encoding{});
    wireforge::BitPacker group(8U, wireforge::BitOrder::MsbFirst);
    encoder.put(group, std::int8_t{-1}, 3U);
    encoder.put(group, Color::Blue, 2U);
    encoder.put(group, 5U, 3U);
    encoder.write_bits(group);
    encoder.write_checksum<wireforge::Crc32>(0U, encoder.position());
    ASSERT_EQ(encoder.status(), Status::Ok);
    Decoder decoder(Span<const std::uint8_t>(buffer.data(), encoder.position()), Encoding{});
    wireforge::BitUnpacker unpacker = decoder.read_bits(8U);
    std::int8_t value = 0;
    decoder.get(unpacker, value, 3U);
    decoder.expect_bits(unpacker, Color::Blue, 2U);
    decoder.skip_bits(unpacker, 3U);
    decoder.verify_checksum<wireforge::Crc32>(0U, 1U);
    EXPECT_EQ(decoder.status(), Status::Ok);
    EXPECT_EQ(value, -1);
    buffer.at(0) ^= 0x08U; // turns the Color bits (4..3) from 10 into 11
    Decoder bad(Span<const std::uint8_t>(buffer.data(), 5U), Encoding{});
    wireforge::BitUnpacker again = bad.read_bits(8U);
    bad.skip_bits(again, 3U);
    bad.expect_bits(again, Color::Blue, 2U);
    EXPECT_EQ(bad.status(), Status::ConstMismatch);
    Decoder corrupt(Span<const std::uint8_t>(buffer.data(), 5U), Encoding{});
    corrupt.skip(1U);
    corrupt.verify_checksum<wireforge::Crc32>(0U, 1U);
    EXPECT_EQ(corrupt.status(), Status::ChecksumMismatch);
}

TEST(CodecSupport, ExpectAndTrailingBytes) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-007");
    const std::array<std::uint8_t, 3> bytes{0x12U, 0x34U, 0x00U};
    Decoder decoder(Span<const std::uint8_t>(bytes), Encoding{});
    decoder.expect<std::uint16_t>(0x1234U);
    EXPECT_EQ(decoder.status(), Status::Ok);
    decoder.finish();
    EXPECT_EQ(decoder.status(), Status::TrailingBytes);
    Decoder lenient(
        Span<const std::uint8_t>(bytes),
        Encoding{wireforge::Endian::Big, wireforge::BitOrder::MsbFirst, true, true, true});
    lenient.expect<std::uint16_t>(0x1235U);
    EXPECT_EQ(lenient.status(), Status::ConstMismatch);
    EXPECT_EQ(lenient.remaining(), 1U);
}

TEST(CodecSupport, WriteBytesCopiesUnchanged) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-008");
    Bytes buffer{};
    Encoder encoder(Span<std::uint8_t>(buffer), Encoding{});
    const std::array<std::uint8_t, 3> raw{0xDEU, 0xADU, 0xBEU};
    encoder.write_bytes(Span<const std::uint8_t>(raw));
    ASSERT_EQ(encoder.status(), Status::Ok);
    EXPECT_EQ(encoder.position(), 3U);
    EXPECT_EQ(buffer[0], 0xDEU);
    EXPECT_EQ(buffer[1], 0xADU);
    EXPECT_EQ(buffer[2], 0xBEU);
}

TEST(CodecSupport, WriteBytesFailsWhenBufferTooSmall) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-008");
    std::array<std::uint8_t, 2> buffer{};
    Encoder encoder(Span<std::uint8_t>(buffer), Encoding{});
    const std::array<std::uint8_t, 3> raw{1U, 2U, 3U};
    encoder.write_bytes(Span<const std::uint8_t>(raw));
    EXPECT_EQ(encoder.status(), Status::BufferTooSmall);
}

TEST(CodecSupport, ReadBytesCopiesUnchanged) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-008");
    const std::array<std::uint8_t, 3> bytes{0xDEU, 0xADU, 0xBEU};
    Decoder decoder(Span<const std::uint8_t>(bytes), Encoding{});
    std::array<std::uint8_t, 3> out{};
    decoder.read_bytes(Span<std::uint8_t>(out));
    EXPECT_EQ(decoder.status(), Status::Ok);
    EXPECT_EQ(out, bytes);
}

TEST(CodecSupport, ReadBytesFailsWhenTruncated) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-008");
    const std::array<std::uint8_t, 2> bytes{1U, 2U};
    Decoder decoder(Span<const std::uint8_t>(bytes), Encoding{});
    std::array<std::uint8_t, 3> out{};
    decoder.read_bytes(Span<std::uint8_t>(out));
    EXPECT_EQ(decoder.status(), Status::Truncated);
}

TEST(CodecSupport, NarrowFieldsHaveTheirOwnWidth) {
    ::testing::Test::RecordProperty("requirements", "RTR-codec_support-009");
    Bytes buffer{};
    Encoder encoder(Span<std::uint8_t>(buffer), kLittle);
    encoder.write_width(std::uint32_t{0x0A0B0CU}, 3U);
    encoder.write_width(std::uint8_t{0x7FU}, 1U);
    ASSERT_EQ(encoder.status(), Status::Ok);
    ASSERT_EQ(encoder.position(), 4U);
    EXPECT_EQ(buffer[0], 0x0CU);
    EXPECT_EQ(buffer[2], 0x0AU);
    EXPECT_EQ(buffer[3], 0x7FU);

    Decoder decoder(Span<const std::uint8_t>(buffer.data(), 4U), kLittle);
    std::uint32_t wide = 0U;
    std::uint8_t narrow = 0U;
    decoder.read_width(wide, 3U);
    decoder.read_width(narrow, 1U);
    EXPECT_EQ(decoder.status(), Status::Ok);
    EXPECT_EQ(wide, 0x0A0B0CU);
    EXPECT_EQ(narrow, 0x7FU);
    decoder.read_width(wide, 1U);
    EXPECT_EQ(decoder.status(), Status::Truncated);

    // A value that does not fit is refused and nothing is written.
    Encoder small(Span<std::uint8_t>(buffer), kLittle);
    small.write_width(std::uint32_t{0x01000000U}, 3U);
    EXPECT_EQ(small.status(), Status::OutOfRange);
    EXPECT_EQ(small.position(), 0U);
    small.write_width(std::uint8_t{1U}, 1U);
    EXPECT_EQ(small.position(), 0U);
    Encoder full(Span<std::uint8_t>(buffer), kLittle);
    full.write_width(std::uint64_t{0xFFFFFFFFFFFFFFFFU}, 8U);
    EXPECT_EQ(full.status(), Status::Ok);
}

TEST(CodecSupport, NoHeapAllocation) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-005");
    const wireforge::testing::AllocationTrap trap;
    Bytes buffer{};
    Encoder encoder(Span<std::uint8_t>(buffer), Encoding{});
    encoder.write(std::uint64_t{42});
    Decoder decoder(Span<const std::uint8_t>(buffer.data(), 8U), Encoding{});
    std::uint64_t value = 0U;
    decoder.read(value);
    EXPECT_EQ(value, 42U);
    EXPECT_EQ(trap.allocations(), 0U);
}

} // namespace

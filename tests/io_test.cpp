#include "support/alloc_trap.hpp"
#include "wireforge/bit_utils.hpp"
#include "wireforge/byte_io.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <array>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <limits>

namespace {

using wireforge::BitOrder;
using wireforge::BitPacker;
using wireforge::BitUnpacker;
using wireforge::ByteReader;
using wireforge::ByteWriter;
using wireforge::Endian;
using wireforge::Span;
using wireforge::Status;
using Bytes = std::array<std::uint8_t, 16>;

constexpr std::uint64_t kPattern = 0x0102030405060708U;

// Compile-time use: message-level endianness known at compile time costs nothing at run time.
constexpr std::uint64_t compile_time_round_trip() {
    std::array<std::uint8_t, 4> buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    if (writer.write_unsigned(0xA1B2C3D4U, 4U, Endian::Little) != Status::Ok) {
        return 0U;
    }
    std::uint64_t value = 0U;
    ByteReader reader{Span<const std::uint8_t>(buffer)};
    if (reader.read_unsigned(value, 4U, Endian::Big) != Status::Ok) {
        return 0U;
    }
    return value;
}
static_assert(compile_time_round_trip() == 0xD4C3B2A1U, "constexpr byte IO");

// kPattern truncated to `width` bytes has the bytes (9 - width) .. 8, most significant first.
Bytes expected_bytes(std::size_t width, Endian endian) {
    Bytes bytes{};
    for (std::size_t index = 0U; index < width; ++index) {
        const std::size_t position = endian == Endian::Big ? index : width - 1U - index;
        bytes.at(position) = static_cast<std::uint8_t>(9U - width + index);
    }
    return bytes;
}

void check_width_round_trip(std::size_t width, Endian endian) {
    const std::uint64_t value = kPattern & wireforge::low_mask(wireforge::width_bits(width));
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_unsigned(value, width, endian), Status::Ok);
    EXPECT_EQ(buffer, expected_bytes(width, endian));
    std::uint64_t back = 0U;
    ByteReader reader{Span<const std::uint8_t>(buffer)};
    ASSERT_EQ(reader.read_unsigned(back, width, endian), Status::Ok);
    EXPECT_EQ(back, value);
}

TEST(ByteIo, BigAndLittleEndianForEveryWidth) {
    for (std::size_t width = 1U; width <= 8U; ++width) {
        SCOPED_TRACE(width);
        check_width_round_trip(width, Endian::Big);
        check_width_round_trip(width, Endian::Little);
    }
}

void check_signed_round_trip(std::int64_t value, std::size_t width, Endian endian) {
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_signed(value, width, endian), Status::Ok);
    std::int64_t back = 7;
    ByteReader reader{Span<const std::uint8_t>(buffer)};
    ASSERT_EQ(reader.read_signed(back, width, endian), Status::Ok);
    EXPECT_EQ(back, value);
}

TEST(ByteIo, SignedExtremesRoundTrip) {
    for (std::size_t width = 1U; width <= 8U; ++width) {
        const unsigned bits = wireforge::width_bits(width);
        const auto max_value = static_cast<std::int64_t>(wireforge::low_mask(bits - 1U));
        const std::int64_t min_value = -max_value - 1;
        for (const std::int64_t value : {min_value, std::int64_t{-1}, std::int64_t{0}, max_value}) {
            SCOPED_TRACE(width);
            check_signed_round_trip(value, width, Endian::Big);
            check_signed_round_trip(value, width, Endian::Little);
        }
    }
}

TEST(ByteIo, NegativeValueBytesAreTwosComplement) {
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_signed(-2, 2U, Endian::Big), Status::Ok);
    EXPECT_EQ(buffer.at(0), 0xFFU);
    EXPECT_EQ(buffer.at(1), 0xFEU);
}

TEST(ByteIo, OutOfRangeValuesAreRejectedWithoutSideEffects) {
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    EXPECT_EQ(writer.write_unsigned(0x100U, 1U, Endian::Big), Status::OutOfRange);
    EXPECT_EQ(writer.write_signed(128, 1U, Endian::Big), Status::OutOfRange);
    EXPECT_EQ(writer.write_signed(-129, 1U, Endian::Big), Status::OutOfRange);
    EXPECT_EQ(writer.write_unsigned(1U, 0U, Endian::Big), Status::InvalidState);
    EXPECT_EQ(writer.write_unsigned(1U, 9U, Endian::Big), Status::InvalidState);
    EXPECT_EQ(writer.write_signed(1, 9U, Endian::Big), Status::InvalidState);
    EXPECT_EQ(writer.position(), 0U);
    EXPECT_EQ(buffer.at(0), 0U);
}

TEST(ByteIo, OverflowLeavesWriterUnchanged) {
    std::array<std::uint8_t, 3> buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_unsigned(0xAAU, 1U, Endian::Big), Status::Ok);
    EXPECT_EQ(writer.write_unsigned(0x1234567U, 4U, Endian::Big), Status::BufferTooSmall);
    EXPECT_EQ(writer.position(), 1U);
    EXPECT_EQ(buffer.at(1), 0U);
    const std::array<std::uint8_t, 3> bytes{1U, 2U, 3U};
    EXPECT_EQ(writer.write_bytes(Span<const std::uint8_t>(bytes)), Status::BufferTooSmall);
    EXPECT_EQ(writer.write_fill(0U, 3U), Status::BufferTooSmall);
    ASSERT_EQ(writer.write_fill(0x55U, 2U), Status::Ok);
    EXPECT_EQ(writer.remaining(), 0U);
    EXPECT_EQ(writer.capacity(), 3U);
    EXPECT_EQ(writer.written().size(), 3U);
    EXPECT_EQ(buffer.at(2), 0x55U);
}

TEST(ByteIo, TruncatedInputLeavesReaderUnchanged) {
    const std::array<std::uint8_t, 3> buffer{0x01U, 0x02U, 0x03U};
    ByteReader reader{Span<const std::uint8_t>(buffer)};
    std::uint64_t value = 99U;
    EXPECT_EQ(reader.read_unsigned(value, 4U, Endian::Big), Status::Truncated);
    EXPECT_EQ(value, 99U);
    EXPECT_EQ(reader.position(), 0U);
    EXPECT_EQ(reader.read_unsigned(value, 0U, Endian::Big), Status::InvalidState);
    std::int64_t signed_value = 5;
    EXPECT_EQ(reader.read_signed(signed_value, 4U, Endian::Big), Status::Truncated);
    EXPECT_EQ(signed_value, 5);
    std::array<std::uint8_t, 4> out{};
    EXPECT_EQ(reader.read_bytes(Span<std::uint8_t>(out)), Status::Truncated);
    EXPECT_EQ(reader.skip(4U), Status::Truncated);
    ASSERT_EQ(reader.skip(1U), Status::Ok);
    EXPECT_EQ(reader.rest().size(), 2U);
    EXPECT_EQ(reader.size(), 3U);
    ASSERT_EQ(reader.read_bytes(Span<std::uint8_t>(out.data(), 2U)), Status::Ok);
    EXPECT_EQ(out.at(1), 0x03U);
    EXPECT_EQ(reader.remaining(), 0U);
}

TEST(ByteIo, UnalignedOffsetsWork) {
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_unsigned(0x7U, 1U, Endian::Big), Status::Ok);
    ASSERT_EQ(writer.write_unsigned(kPattern, 8U, Endian::Little), Status::Ok);
    ASSERT_EQ(writer.write_unsigned(0xBEEFU, 2U, Endian::Big), Status::Ok);
    ByteReader reader{Span<const std::uint8_t>(buffer)};
    std::uint64_t value = 0U;
    ASSERT_EQ(reader.skip(1U), Status::Ok);
    ASSERT_EQ(reader.read_unsigned(value, 8U, Endian::Little), Status::Ok);
    EXPECT_EQ(value, kPattern);
    ASSERT_EQ(reader.read_unsigned(value, 2U, Endian::Big), Status::Ok);
    EXPECT_EQ(value, 0xBEEFU);
}

TEST(ByteIo, PatchAndPeekInsideWrittenRegion) {
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_fill(0U, 4U), Status::Ok);
    ASSERT_EQ(writer.write_unsigned_at(1U, 0xCAFEU, 2U, Endian::Little), Status::Ok);
    EXPECT_EQ(writer.write_unsigned_at(3U, 0x1U, 2U, Endian::Little), Status::InvalidLength);
    EXPECT_EQ(writer.write_unsigned_at(9U, 0x1U, 1U, Endian::Little), Status::InvalidLength);
    EXPECT_EQ(writer.write_unsigned_at(0U, 0x100U, 1U, Endian::Little), Status::OutOfRange);
    EXPECT_EQ(writer.write_unsigned_at(0U, 0x1U, 0U, Endian::Little), Status::InvalidState);
    EXPECT_EQ(buffer.at(1), 0xFEU);
    EXPECT_EQ(buffer.at(2), 0xCAU);

    const ByteReader reader{Span<const std::uint8_t>(buffer.data(), 4U)};
    std::uint64_t value = 0U;
    ASSERT_EQ(reader.peek_unsigned_at(1U, value, 2U, Endian::Big), Status::Ok);
    EXPECT_EQ(value, 0xFECAU);
    EXPECT_EQ(reader.peek_unsigned_at(3U, value, 2U, Endian::Big), Status::Truncated);
    EXPECT_EQ(reader.peek_unsigned_at(5U, value, 1U, Endian::Big), Status::Truncated);
    EXPECT_EQ(reader.peek_unsigned_at(0U, value, 9U, Endian::Big), Status::InvalidState);
    EXPECT_EQ(reader.position(), 0U);
}

TEST(ByteIo, FloatsUseIeeeBitPatterns) {
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_f32(1.0F, Endian::Big), Status::Ok);
    ASSERT_EQ(writer.write_f64(-2.5, Endian::Little), Status::Ok);
    EXPECT_EQ(buffer.at(0), 0x3FU);
    EXPECT_EQ(buffer.at(1), 0x80U);
    EXPECT_EQ(buffer.at(11), 0xC0U); // -2.5 = 0xC004000000000000
    EXPECT_EQ(buffer.at(10), 0x04U);

    ByteReader reader{Span<const std::uint8_t>(buffer)};
    float single = 0.0F;
    double dbl = 0.0;
    ASSERT_EQ(reader.read_f32(single, Endian::Big), Status::Ok);
    ASSERT_EQ(reader.read_f64(dbl, Endian::Little), Status::Ok);
    EXPECT_EQ(single, 1.0F);
    EXPECT_EQ(dbl, -2.5);
    EXPECT_EQ(reader.read_f64(dbl, Endian::Little), Status::Truncated);
    EXPECT_EQ(reader.skip(4U), Status::Ok);
    EXPECT_EQ(reader.read_f32(single, Endian::Little), Status::Truncated);
}

TEST(ByteIo, FloatSpecialValuesSurvive) {
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_f32(std::numeric_limits<float>::quiet_NaN(), Endian::Little),
              Status::Ok);
    ASSERT_EQ(writer.write_f64(-std::numeric_limits<double>::infinity(), Endian::Big), Status::Ok);
    ByteReader reader{Span<const std::uint8_t>(buffer)};
    float single = 0.0F;
    double dbl = 0.0;
    ASSERT_EQ(reader.read_f32(single, Endian::Little), Status::Ok);
    ASSERT_EQ(reader.read_f64(dbl, Endian::Big), Status::Ok);
    EXPECT_TRUE(std::isnan(single));
    EXPECT_TRUE(std::isinf(dbl));
    EXPECT_LT(dbl, 0.0);
}

TEST(BitUtils, SignHelpers) {
    EXPECT_EQ(wireforge::sign_extend(0x7U, 3U), -1);
    EXPECT_EQ(wireforge::sign_extend(0x3U, 3U), 3);
    EXPECT_EQ(wireforge::sign_extend(0x4U, 3U), -4);
    EXPECT_EQ(wireforge::sign_extend(~std::uint64_t{0U}, 64U), -1);
    EXPECT_EQ(wireforge::sign_extend(std::uint64_t{1U} << 63U, 64U),
              std::numeric_limits<std::int64_t>::min());
    EXPECT_TRUE(wireforge::fits_signed(-4, 3U));
    EXPECT_FALSE(wireforge::fits_signed(4, 3U));
    EXPECT_FALSE(wireforge::fits_signed(0, 0U));
    EXPECT_TRUE(wireforge::fits_signed(std::numeric_limits<std::int64_t>::min(), 64U));
    EXPECT_TRUE(wireforge::fits_unsigned(~std::uint64_t{0U}, 64U));
    EXPECT_FALSE(wireforge::fits_unsigned(8U, 3U));
    EXPECT_EQ(wireforge::to_twos_complement(-1, 5U), 0x1FU);
    EXPECT_EQ(wireforge::reflect(0x1U, 8U), 0x80U);
    EXPECT_EQ(wireforge::reflect(0x3U, 4U), 0xCU);
}

// Group a:3, b:7 (straddles the byte boundary), c:6 = 16 bits; values a=5, b=0x55, c=0x2A.
struct GroupCase {
    BitOrder order;
    Endian endian;
    std::array<std::uint8_t, 2> wire;
};

std::array<std::uint8_t, 2> pack_group(BitOrder order, Endian endian) {
    std::array<std::uint8_t, 2> buffer{};
    BitPacker packer(16U, order);
    EXPECT_EQ(packer.put(5U, 3U), Status::Ok);
    EXPECT_EQ(packer.put(0x55U, 7U), Status::Ok);
    EXPECT_FALSE(packer.complete());
    EXPECT_EQ(packer.put(0x2AU, 6U), Status::Ok);
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    EXPECT_EQ(writer.write_bits(packer, endian), Status::Ok);
    return buffer;
}

std::array<std::uint64_t, 3> unpack_group(const std::array<std::uint8_t, 2>& wire, BitOrder order,
                                          Endian endian) {
    std::array<std::uint64_t, 3> fields{};
    ByteReader reader{Span<const std::uint8_t>(wire)};
    BitUnpacker unpacker(0U, 8U, order);
    EXPECT_EQ(reader.read_bits(unpacker, 16U, order, endian), Status::Ok);
    EXPECT_EQ(unpacker.get(fields.at(0), 3U), Status::Ok);
    EXPECT_EQ(unpacker.get(fields.at(1), 7U), Status::Ok);
    EXPECT_EQ(unpacker.get(fields.at(2), 6U), Status::Ok);
    EXPECT_TRUE(unpacker.complete());
    std::uint64_t extra = 0U;
    EXPECT_EQ(unpacker.get(extra, 1U), Status::InvalidState);
    return fields;
}

TEST(BitGroups, StraddlingFieldsInBothOrdersAndEndians) {
    // msb_first container: 101 1010101 101010 = 0xB56A; lsb_first: c<<10 | b<<3 | a = 0xAAAD.
    const std::array<GroupCase, 4> cases{{
        {BitOrder::MsbFirst, Endian::Big, {0xB5U, 0x6AU}},
        {BitOrder::MsbFirst, Endian::Little, {0x6AU, 0xB5U}},
        {BitOrder::LsbFirst, Endian::Big, {0xAAU, 0xADU}},
        {BitOrder::LsbFirst, Endian::Little, {0xADU, 0xAAU}},
    }};
    const std::array<std::uint64_t, 3> fields{5U, 0x55U, 0x2AU};
    for (const GroupCase& test : cases) {
        EXPECT_EQ(pack_group(test.order, test.endian), test.wire);
        EXPECT_EQ(unpack_group(test.wire, test.order, test.endian), fields);
    }
}

TEST(BitGroups, MsbFirstSingleByteFillsFromBitSeven) {
    BitPacker packer(8U, BitOrder::MsbFirst);
    ASSERT_EQ(packer.put(1U, 1U), Status::Ok);
    ASSERT_EQ(packer.put(0U, 7U), Status::Ok);
    EXPECT_EQ(packer.container(), 0x80U);
    BitPacker lsb(8U, BitOrder::LsbFirst);
    ASSERT_EQ(lsb.put(1U, 1U), Status::Ok);
    ASSERT_EQ(lsb.put(0U, 7U), Status::Ok);
    EXPECT_EQ(lsb.container(), 0x01U);
}

TEST(BitGroups, SignedFieldsAreSignExtended) {
    BitPacker packer(8U, BitOrder::MsbFirst);
    ASSERT_EQ(packer.put_signed(-3, 4U), Status::Ok);
    ASSERT_EQ(packer.put_signed(7, 4U), Status::Ok);
    EXPECT_EQ(packer.container(), 0xD7U);
    BitUnpacker unpacker(packer.container(), 8U, BitOrder::MsbFirst);
    std::int64_t value = 0;
    ASSERT_EQ(unpacker.get_signed(value, 4U), Status::Ok);
    EXPECT_EQ(value, -3);
    ASSERT_EQ(unpacker.get_signed(value, 4U), Status::Ok);
    EXPECT_EQ(value, 7);
    EXPECT_EQ(unpacker.get_signed(value, 1U), Status::InvalidState);
    EXPECT_EQ(value, 7);
}

TEST(BitGroups, FullWidthSixtyFourBitGroup) {
    BitPacker packer(64U, BitOrder::LsbFirst);
    ASSERT_EQ(packer.put(kPattern, 64U), Status::Ok);
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_bits(packer, Endian::Big), Status::Ok);
    ByteReader reader{Span<const std::uint8_t>(buffer)};
    BitUnpacker unpacker(0U, 8U, BitOrder::LsbFirst);
    ASSERT_EQ(reader.read_bits(unpacker, 64U, BitOrder::LsbFirst, Endian::Big), Status::Ok);
    std::uint64_t value = 0U;
    ASSERT_EQ(unpacker.get(value, 64U), Status::Ok);
    EXPECT_EQ(value, kPattern);
}

TEST(BitGroups, MisuseIsReported) {
    BitPacker packer(8U, BitOrder::MsbFirst);
    EXPECT_EQ(packer.put(8U, 3U), Status::OutOfRange);
    EXPECT_EQ(packer.put_signed(4, 3U), Status::OutOfRange);
    EXPECT_EQ(packer.put_signed(0, 0U), Status::InvalidState);
    EXPECT_EQ(packer.put(0U, 0U), Status::InvalidState);
    EXPECT_EQ(packer.put(0U, 9U), Status::InvalidState);
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    EXPECT_EQ(writer.write_bits(packer, Endian::Big), Status::InvalidState);
}

TEST(BitGroups, InvalidGroupSizesAreRejected) {
    const Bytes buffer{};
    for (const unsigned total : {0U, 12U, 72U}) {
        BitPacker invalid(total, BitOrder::MsbFirst);
        EXPECT_FALSE(invalid.valid());
        EXPECT_EQ(invalid.put(0U, 1U), Status::InvalidState);
        BitUnpacker unpacker(0U, 8U, BitOrder::MsbFirst);
        ByteReader reader{Span<const std::uint8_t>(buffer)};
        EXPECT_EQ(reader.read_bits(unpacker, total, BitOrder::MsbFirst, Endian::Big),
                  Status::InvalidState);
    }
}

TEST(BitGroups, TruncatedGroupIsReported) {
    const std::array<std::uint8_t, 1> one{0U};
    ByteReader short_reader{Span<const std::uint8_t>(one)};
    BitUnpacker unpacker(0U, 8U, BitOrder::MsbFirst);
    EXPECT_EQ(short_reader.read_bits(unpacker, 16U, BitOrder::MsbFirst, Endian::Big),
              Status::Truncated);
}

TEST(ByteIo, NoHeapAllocation) {
    const wireforge::testing::AllocationTrap trap;
    Bytes buffer{};
    ByteWriter writer{Span<std::uint8_t>(buffer)};
    ASSERT_EQ(writer.write_f64(3.25, Endian::Big), Status::Ok);
    BitPacker packer(8U, BitOrder::LsbFirst);
    ASSERT_EQ(packer.put(0xFFU, 8U), Status::Ok);
    ASSERT_EQ(writer.write_bits(packer, Endian::Little), Status::Ok);
    ByteReader reader{Span<const std::uint8_t>(buffer)};
    double value = 0.0;
    ASSERT_EQ(reader.read_f64(value, Endian::Big), Status::Ok);
    EXPECT_EQ(trap.allocations(), 0U);
}

TEST(StatusNames, EveryCodeHasAName) {
    EXPECT_STREQ(wireforge::to_string(Status::Ok), "Ok");
    EXPECT_STREQ(wireforge::to_string(Status::BufferTooSmall), "BufferTooSmall");
    EXPECT_STREQ(wireforge::to_string(Status::Truncated), "Truncated");
    EXPECT_STREQ(wireforge::to_string(Status::OutOfRange), "OutOfRange");
    EXPECT_STREQ(wireforge::to_string(Status::CountExceedsMax), "CountExceedsMax");
    EXPECT_STREQ(wireforge::to_string(Status::ConstMismatch), "ConstMismatch");
    EXPECT_STREQ(wireforge::to_string(Status::ChecksumMismatch), "ChecksumMismatch");
    EXPECT_STREQ(wireforge::to_string(Status::UnknownMessageId), "UnknownMessageId");
    EXPECT_STREQ(wireforge::to_string(Status::InvalidLength), "InvalidLength");
    EXPECT_STREQ(wireforge::to_string(Status::TrailingBytes), "TrailingBytes");
    EXPECT_STREQ(wireforge::to_string(Status::InvalidState), "InvalidState");
    EXPECT_STREQ(wireforge::to_string(static_cast<Status>(200U)), "Unknown");
    EXPECT_TRUE(wireforge::is_ok(Status::Ok));
    EXPECT_FALSE(wireforge::is_ok(Status::Truncated));
}

} // namespace

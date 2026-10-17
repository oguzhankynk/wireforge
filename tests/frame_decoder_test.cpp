#include "support/alloc_trap.hpp"
#include "wireforge/byte_io.hpp"
#include "wireforge/checksum.hpp"
#include "wireforge/frame_decoder.hpp"
#include "wireforge/ring_buffer.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <vector>

namespace {

using wireforge::ChecksumCoverage;
using wireforge::Crc32;
using wireforge::Endian;
using wireforge::FrameDecoder;
using wireforge::FrameFormat;
using wireforge::FramingKind;
using wireforge::LengthCounts;
using wireforge::Span;
using wireforge::StaticSpscByteRing;
using wireforge::Status;
using Bytes = std::vector<std::uint8_t>;

constexpr std::uint64_t kSync = 0xA55AU;
constexpr std::size_t kMaxFrame = 64U;

// Header: sync u16, id u16, length u16 (payload bytes); trailer: CRC-32 over header and payload.
constexpr FrameFormat kSyncLength{FramingKind::SyncLength,
                                  Endian::Big,
                                  6U,
                                  4U,
                                  kMaxFrame,
                                  {0U, 2U},
                                  kSync,
                                  {4U, 2U},
                                  LengthCounts::Payload,
                                  0U,
                                  ChecksumCoverage::HeaderAndPayload,
                                  true};

FrameFormat with_resync(FrameFormat format, bool resync) {
    format.resync = resync;
    return format;
}

void append_unsigned(Bytes& bytes, std::uint64_t value, std::size_t width, Endian endian) {
    std::array<std::uint8_t, 8> buffer{};
    wireforge::store_unsigned(Span<std::uint8_t>(buffer), value, width, endian);
    bytes.insert(bytes.end(), buffer.begin(),
                 std::next(buffer.begin(), static_cast<std::ptrdiff_t>(width)));
}

// Builds a frame in the kSyncLength layout with a correct CRC.
Bytes make_frame(std::uint16_t message_id, const Bytes& payload) {
    Bytes frame;
    append_unsigned(frame, kSync, 2U, Endian::Big);
    append_unsigned(frame, message_id, 2U, Endian::Big);
    append_unsigned(frame, payload.size(), 2U, Endian::Big);
    frame.insert(frame.end(), payload.begin(), payload.end());
    const std::uint64_t crc = Crc32::compute(Span<const std::uint8_t>(frame.data(), frame.size()));
    append_unsigned(frame, crc, 4U, Endian::Big);
    return frame;
}

Bytes concat(std::initializer_list<Bytes> parts) {
    Bytes result;
    for (const Bytes& part : parts) {
        result.insert(result.end(), part.begin(), part.end());
    }
    return result;
}

class XorShift32 {
public:
    std::size_t next(std::size_t bound) noexcept {
        state_ ^= state_ << 13U;
        state_ ^= state_ >> 17U;
        state_ ^= state_ << 5U;
        return state_ % bound;
    }

private:
    std::uint32_t state_ = 2463534242U;
};

struct Outcome {
    std::vector<Bytes> frames;
    std::vector<Status> errors;
};

// Feeds `stream` in chunks of the given sizes (cycled) and drains the decoder after each chunk.
template <typename Checksum>
Outcome run(FrameDecoder<Checksum>& decoder, const Bytes& stream,
            const std::vector<std::size_t>& chunks) {
    StaticSpscByteRing<128> ring;
    Outcome outcome;
    std::array<std::uint8_t, kMaxFrame> out{};
    std::size_t position = 0U;
    std::size_t chunk = 0U;
    while (position < stream.size()) {
        const std::size_t size =
            std::min(chunks.at(chunk % chunks.size()), stream.size() - position);
        ++chunk;
        const std::size_t written =
            ring.write(Span<const std::uint8_t>(&stream.at(position), size));
        position += written;
        for (;;) {
            std::size_t frame_size = 0U;
            const Status status = decoder.next(ring, Span<std::uint8_t>(out), frame_size);
            if (status == Status::Truncated) {
                break;
            }
            if (status == Status::Ok) {
                outcome.frames.emplace_back(
                    out.begin(), std::next(out.begin(), static_cast<std::ptrdiff_t>(frame_size)));
            } else {
                outcome.errors.push_back(status);
            }
        }
    }
    return outcome;
}

Outcome run_whole(const FrameFormat& format, const Bytes& stream) {
    FrameDecoder<Crc32> decoder(format);
    return run(decoder, stream, {stream.size()});
}

TEST(FrameDecoder, FormatIsValid) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-001");
    EXPECT_TRUE(FrameDecoder<Crc32>(kSyncLength).valid());
    EXPECT_TRUE(FrameDecoder<>(kSyncLength).valid());
}

TEST(FrameDecoder, DeliversOneFrame) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-002");
    const Bytes frame = make_frame(7U, {1U, 2U, 3U});
    const Outcome outcome = run_whole(kSyncLength, frame);
    ASSERT_EQ(outcome.frames.size(), 1U);
    EXPECT_EQ(outcome.frames.at(0), frame);
    EXPECT_TRUE(outcome.errors.empty());
}

TEST(FrameDecoder, ChunkingDoesNotChangeTheResult) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-002");
    const Bytes stream = concat({make_frame(1U, {}), make_frame(2U, {9U, 8U, 7U, 6U}),
                                 make_frame(3U, Bytes(40U, 0x5AU)), make_frame(4U, {0xA5U})});
    FrameDecoder<Crc32> whole_decoder(kSyncLength);
    const Outcome whole = run(whole_decoder, stream, {stream.size()});
    FrameDecoder<Crc32> byte_decoder(kSyncLength);
    const Outcome bytewise = run(byte_decoder, stream, {1U});
    XorShift32 random;
    std::vector<std::size_t> chunks;
    chunks.reserve(50U);
    for (int index = 0; index < 50; ++index) {
        chunks.push_back(1U + random.next(17U));
    }
    FrameDecoder<Crc32> random_decoder(kSyncLength);
    const Outcome chunked = run(random_decoder, stream, chunks);
    ASSERT_EQ(whole.frames.size(), 4U);
    EXPECT_EQ(bytewise.frames, whole.frames);
    EXPECT_EQ(chunked.frames, whole.frames);
    EXPECT_EQ(random_decoder.counters().frames.load(), 4U);
}

TEST(FrameDecoder, SkipsGarbageBetweenFrames) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-003");
    const Bytes garbage{0x00U, 0xA5U, 0x13U, 0xA5U, 0xA5U, 0xFFU};
    const Bytes first = make_frame(1U, {1U});
    const Bytes second = make_frame(2U, {2U, 2U});
    FrameDecoder<Crc32> decoder(kSyncLength);
    const Outcome outcome = run(decoder, concat({garbage, first, garbage, second, garbage}), {3U});
    ASSERT_EQ(outcome.frames.size(), 2U);
    EXPECT_EQ(outcome.frames.at(0), first);
    EXPECT_EQ(outcome.frames.at(1), second);
    EXPECT_TRUE(outcome.errors.empty());
    // The trailing garbage stays buffered while it could still be the start of a frame.
    EXPECT_GE(decoder.counters().discarded_bytes.load(), 2U * garbage.size());
}

TEST(FrameDecoder, BadChecksumIsReportedAndFollowingFrameRecovered) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-004");
    Bytes corrupt = make_frame(1U, {1U, 2U, 3U});
    corrupt.at(7) ^= 0x01U;
    const Bytes good = make_frame(2U, {4U});
    FrameDecoder<Crc32> decoder(kSyncLength);
    const Outcome outcome = run(decoder, concat({corrupt, good}), {5U});
    ASSERT_EQ(outcome.frames.size(), 1U);
    EXPECT_EQ(outcome.frames.at(0), good);
    ASSERT_EQ(outcome.errors.size(), 1U);
    EXPECT_EQ(outcome.errors.at(0), Status::ChecksumMismatch);
    EXPECT_EQ(decoder.counters().checksum_errors.load(), 1U);
}

TEST(FrameDecoder, OversizedLengthIsRejected) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-005");
    Bytes bogus;
    append_unsigned(bogus, kSync, 2U, Endian::Big);
    append_unsigned(bogus, 1U, 2U, Endian::Big);
    append_unsigned(bogus, 500U, 2U, Endian::Big);
    const Bytes good = make_frame(3U, {3U});
    FrameDecoder<Crc32> decoder(kSyncLength);
    const Outcome outcome = run(decoder, concat({bogus, good}), {64U});
    ASSERT_EQ(outcome.frames.size(), 1U);
    EXPECT_EQ(outcome.frames.at(0), good);
    ASSERT_EQ(outcome.errors.size(), 1U);
    EXPECT_EQ(outcome.errors.at(0), Status::InvalidLength);
    EXPECT_EQ(decoder.counters().length_errors.load(), 1U);
}

TEST(FrameDecoder, WithoutResyncAnErrorDropsBufferedBytes) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-006");
    Bytes corrupt = make_frame(1U, {1U});
    corrupt.back() ^= 0xFFU;
    const Bytes good = make_frame(2U, {2U});
    FrameDecoder<Crc32> decoder(with_resync(kSyncLength, false));
    // Both frames arrive in one chunk: the error discards the good frame as well.
    const Outcome together = run(decoder, concat({corrupt, good}), {64U});
    EXPECT_TRUE(together.frames.empty());
    ASSERT_EQ(together.errors.size(), 1U);
    // Delivered separately, the second frame survives.
    FrameDecoder<Crc32> separate(with_resync(kSyncLength, false));
    const Outcome apart = run(separate, concat({corrupt, good}), {corrupt.size(), good.size()});
    ASSERT_EQ(apart.frames.size(), 1U);
    EXPECT_EQ(apart.frames.at(0), good);
}

TEST(FrameDecoder, OutputBufferTooSmall) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-007");
    const Bytes frame = make_frame(1U, Bytes(20U, 1U));
    StaticSpscByteRing<64> ring;
    ASSERT_EQ(ring.write(Span<const std::uint8_t>(frame.data(), frame.size())), frame.size());
    FrameDecoder<Crc32> decoder(kSyncLength);
    std::array<std::uint8_t, 16> small{};
    std::size_t size = 99U;
    EXPECT_EQ(decoder.next(ring, Span<std::uint8_t>(small), size), Status::BufferTooSmall);
    EXPECT_EQ(size, 0U);
    EXPECT_EQ(decoder.counters().overflow_errors.load(), 1U);
}

TEST(FrameDecoder, FrameLargerThanRingIsRejected) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-005");
    const Bytes frame = make_frame(1U, Bytes(30U, 1U));
    StaticSpscByteRing<16> ring;
    ASSERT_EQ(ring.write(Span<const std::uint8_t>(frame.data(), 16U)), 16U);
    FrameDecoder<Crc32> decoder(kSyncLength);
    std::array<std::uint8_t, kMaxFrame> out{};
    std::size_t size = 0U;
    EXPECT_EQ(decoder.next(ring, Span<std::uint8_t>(out), size), Status::InvalidLength);
}

TEST(FrameDecoder, FixedFramingWithSyncAndPayloadChecksum) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-008");
    // Header: sync u8 0x7E; payload 3 bytes; trailer: CRC-16 over the payload, little endian.
    using Crc16 = wireforge::Crc16CcittFalse;
    const FrameFormat format{FramingKind::Fixed,
                             Endian::Little,
                             1U,
                             2U,
                             6U,
                             {0U, 1U},
                             0x7EU,
                             {0U, 0U},
                             LengthCounts::Payload,
                             0U,
                             ChecksumCoverage::Payload,
                             true};
    const Bytes payload{0x10U, 0x20U, 0x30U};
    Bytes frame{0x7EU};
    frame.insert(frame.end(), payload.begin(), payload.end());
    append_unsigned(frame, Crc16::compute(Span<const std::uint8_t>(payload.data(), payload.size())),
                    2U, Endian::Little);
    FrameDecoder<Crc16> decoder(format);
    ASSERT_TRUE(decoder.valid());
    const Outcome outcome = run(decoder, concat({{0x00U}, frame, frame}), {1U});
    ASSERT_EQ(outcome.frames.size(), 2U);
    EXPECT_EQ(outcome.frames.at(1), frame);
    EXPECT_EQ(decoder.counters().discarded_bytes.load(), 1U);
}

TEST(FrameDecoder, LengthPrefixedCountingWholeFrame) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-009");
    // Header: length u8 counting the whole frame; no sync, no checksum.
    const FrameFormat format{FramingKind::LengthPrefixed,
                             Endian::Big,
                             1U,
                             0U,
                             16U,
                             {0U, 0U},
                             0U,
                             {0U, 1U},
                             LengthCounts::Frame,
                             0U,
                             ChecksumCoverage::Payload,
                             true};
    FrameDecoder<> decoder(format);
    const Bytes first{3U, 0xAAU, 0xBBU};
    const Bytes second{1U};
    const Outcome outcome = run(decoder, concat({first, second, {0U}, first}), {2U});
    ASSERT_EQ(outcome.frames.size(), 3U);
    EXPECT_EQ(outcome.frames.at(0), first);
    EXPECT_EQ(outcome.frames.at(1), second);
    ASSERT_EQ(outcome.errors.size(), 1U); // length 0 is shorter than the header
    EXPECT_EQ(outcome.errors.at(0), Status::InvalidLength);
}

TEST(FrameDecoder, LengthCountingPayloadAndTrailer) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-009");
    FrameFormat format = kSyncLength;
    format.length_counts = LengthCounts::PayloadAndTrailer;
    Bytes frame = make_frame(1U, {5U, 6U});
    // Patch the length field (offset 4) to payload + trailer and recompute the CRC.
    frame.resize(frame.size() - 4U);
    frame.at(5) = static_cast<std::uint8_t>(2U + 4U);
    append_unsigned(frame, Crc32::compute(Span<const std::uint8_t>(frame.data(), frame.size())), 4U,
                    Endian::Big);
    const Outcome outcome = run_whole(format, frame);
    ASSERT_EQ(outcome.frames.size(), 1U);
    EXPECT_EQ(outcome.frames.at(0), frame);
}

TEST(FrameDecoder, InvalidFormatsAreRejected) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-001");
    FrameFormat no_sync = kSyncLength;
    no_sync.sync = {0U, 0U};
    FrameFormat no_length = kSyncLength;
    no_length.length = {0U, 0U};
    FrameFormat field_outside = kSyncLength;
    field_outside.length = {5U, 2U};
    FrameFormat too_small = kSyncLength;
    too_small.max_frame_size = 8U;
    FrameFormat checksum_outside = kSyncLength;
    checksum_outside.checksum_offset = 1U;
    for (const FrameFormat& format :
         {no_sync, no_length, field_outside, too_small, checksum_outside}) {
        FrameDecoder<Crc32> decoder(format);
        EXPECT_FALSE(decoder.valid());
        StaticSpscByteRing<8> ring;
        std::array<std::uint8_t, 8> out{};
        std::size_t size = 0U;
        EXPECT_EQ(decoder.next(ring, Span<std::uint8_t>(out), size), Status::InvalidState);
    }
}

TEST(FrameDecoder, WorksWithLockedRingAndResetsCounters) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-010");
    std::array<std::uint8_t, 64> storage{};
    wireforge::LockedByteRing<> ring{Span<std::uint8_t>(storage)};
    const Bytes frame = make_frame(9U, {1U});
    ASSERT_TRUE(ring.write_all(Span<const std::uint8_t>(frame.data(), frame.size())));
    FrameDecoder<Crc32> decoder(kSyncLength);
    std::array<std::uint8_t, kMaxFrame> out{};
    std::size_t size = 0U;
    ASSERT_EQ(decoder.next(ring, Span<std::uint8_t>(out), size), Status::Ok);
    EXPECT_EQ(size, frame.size());
    EXPECT_EQ(decoder.format().header_size, 6U);
    decoder.reset_counters();
    EXPECT_EQ(decoder.counters().frames.load(), 0U);
}

TEST(FrameDecoder, NoHeapAllocation) {
    ::testing::Test::RecordProperty("requirements", "RTR-static_vector-005");
    const Bytes stream = concat({Bytes{0x01U, 0x02U}, make_frame(1U, {1U, 2U})});
    StaticSpscByteRing<64> ring;
    FrameDecoder<Crc32> decoder(kSyncLength);
    std::array<std::uint8_t, kMaxFrame> out{};
    std::size_t size = 0U;
    const wireforge::testing::AllocationTrap trap;
    for (const std::uint8_t byte : stream) {
        ASSERT_EQ(ring.write(Span<const std::uint8_t>(&byte, 1U)), 1U);
        static_cast<void>(decoder.next(ring, Span<std::uint8_t>(out), size));
    }
    EXPECT_EQ(size, stream.size() - 2U);
    EXPECT_EQ(trap.allocations(), 0U);
}

// Feeds `stream` into `ring` (as far as it fits, draining the decoder between writes) and returns
// the statuses of all rejected frames; `out` is the destination of the delivered frames.
template <typename Ring>
std::vector<Status> feed(FrameDecoder<Crc32>& decoder, Ring& ring, const Bytes& stream,
                         Span<std::uint8_t> out, std::size_t& delivered) {
    std::vector<Status> errors;
    std::size_t position = 0U;
    while (position < stream.size()) {
        const std::size_t written =
            ring.write(Span<const std::uint8_t>(stream.data(), stream.size()).subspan(position));
        position += written;
        for (;;) {
            std::size_t frame_size = 0U;
            const Status status = decoder.next(ring, out, frame_size);
            if (status == Status::Truncated) {
                break;
            }
            if (status == Status::Ok) {
                ++delivered;
            } else {
                errors.push_back(status);
            }
        }
        if (written == 0U) {
            break;
        }
    }
    return errors;
}

// Drives a decoder over a ring of any type and size through every outcome of next(): garbage
// before a frame, a good frame, a wrong checksum, an impossible length, an output buffer that is
// too small, and a frame cut short.
template <typename Ring> void exercise_every_outcome(Ring& ring) {
    FrameDecoder<Crc32> decoder(kSyncLength);
    std::array<std::uint8_t, kMaxFrame> out{};
    std::array<std::uint8_t, 8> small{};
    std::size_t delivered = 0U;
    Bytes corrupt = make_frame(2U, {1U, 2U});
    corrupt.at(7) ^= 0x01U;
    Bytes bogus;
    append_unsigned(bogus, kSync, 2U, Endian::Big);
    append_unsigned(bogus, 1U, 2U, Endian::Big);
    append_unsigned(bogus, 500U, 2U, Endian::Big);
    const Bytes good = make_frame(1U, {7U});
    const Bytes large = make_frame(3U, Bytes(20U, 1U));
    const Bytes cut(good.begin(), good.begin() + 8);
    static_cast<void>(feed(decoder, ring, concat({Bytes{0x01U, 0x02U, 0x03U}, good}),
                           Span<std::uint8_t>(out), delivered));
    static_cast<void>(feed(decoder, ring, corrupt, Span<std::uint8_t>(out), delivered));
    static_cast<void>(feed(decoder, ring, bogus, Span<std::uint8_t>(out), delivered));
    static_cast<void>(feed(decoder, ring, large, Span<std::uint8_t>(small), delivered));
    static_cast<void>(feed(decoder, ring, cut, Span<std::uint8_t>(out), delivered));
    static_cast<void>(feed(decoder, ring, good, Span<std::uint8_t>(out), delivered));
    const wireforge::FrameCounters& counters = decoder.counters();
    const std::uint64_t outcomes = counters.frames.load() + counters.length_errors.load() +
                                   counters.checksum_errors.load() +
                                   counters.overflow_errors.load();
    EXPECT_GT(outcomes, 0U);
    EXPECT_EQ(counters.frames.load(), delivered);
}

TEST(FrameDecoder, EveryOutcomeOnRingsOfEachType) {
    ::testing::Test::RecordProperty("requirements", "RTR-frame_decoder-004");
    StaticSpscByteRing<8> tiny;
    StaticSpscByteRing<16> small;
    StaticSpscByteRing<64> medium;
    StaticSpscByteRing<128> large;
    std::array<std::uint8_t, 64> storage{};
    wireforge::LockedByteRing<> locked{Span<std::uint8_t>(storage)};
    exercise_every_outcome(tiny);
    exercise_every_outcome(small);
    exercise_every_outcome(medium);
    exercise_every_outcome(large);
    exercise_every_outcome(locked);
}

} // namespace

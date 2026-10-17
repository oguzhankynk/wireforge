// libFuzzer target: the frame decoder streams arbitrary bytes in arbitrary chunks without
// overflowing its buffers, and every frame it delivers is well formed.
#include "wireforge/byte_io.hpp"
#include "wireforge/checksum.hpp"
#include "wireforge/frame_decoder.hpp"
#include "wireforge/ring_buffer.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstdlib>

namespace {

using wireforge::ChecksumCoverage;
using wireforge::Endian;
using wireforge::FrameFormat;
using wireforge::FramingKind;
using wireforge::LengthCounts;
using wireforge::Span;
using wireforge::Status;

constexpr std::size_t kMaxFrame = 48U;

// sync u16, length u8 (payload), CRC-16 trailer over header and payload; resynchronizes.
constexpr FrameFormat kSyncLength{FramingKind::SyncLength,
                                  Endian::Big,
                                  3U,
                                  2U,
                                  kMaxFrame,
                                  {0U, 2U},
                                  0xA55AU,
                                  {2U, 1U},
                                  LengthCounts::Payload,
                                  0U,
                                  ChecksumCoverage::HeaderAndPayload,
                                  true};
// length u16 little endian counting the whole frame; drops everything on errors.
constexpr FrameFormat kLengthPrefixed{FramingKind::LengthPrefixed,
                                      Endian::Little,
                                      2U,
                                      0U,
                                      kMaxFrame,
                                      {0U, 0U},
                                      0U,
                                      {0U, 2U},
                                      LengthCounts::Frame,
                                      0U,
                                      ChecksumCoverage::Payload,
                                      false};
// 8-byte frames starting with sync 0x7E.
constexpr FrameFormat kFixed{FramingKind::Fixed,
                             Endian::Big,
                             1U,
                             0U,
                             8U,
                             {0U, 1U},
                             0x7EU,
                             {0U, 0U},
                             LengthCounts::Payload,
                             0U,
                             ChecksumCoverage::Payload,
                             true};

[[noreturn]] void invalid_frame() noexcept {
    std::abort();
}

using Crc16 = wireforge::Crc16CcittFalse;

void check_frame(const FrameFormat& format, Span<const std::uint8_t> frame, bool crc) noexcept {
    const std::size_t overhead = format.header_size + format.trailer_size;
    if (frame.size() < overhead || frame.size() > format.max_frame_size) {
        invalid_frame();
    }
    if (format.sync.width != 0U &&
        wireforge::load_unsigned(frame.subspan(format.sync.offset, format.sync.width),
                                 format.sync.width, format.endian) != format.sync_value) {
        invalid_frame();
    }
    if (crc) {
        const std::size_t covered = frame.size() - format.trailer_size;
        const std::uint64_t expected = Crc16::compute(frame.first(covered));
        if (wireforge::load_unsigned(frame.subspan(covered), Crc16::kSize, format.endian) !=
            expected) {
            invalid_frame();
        }
    }
}

template <typename Checksum>
void stream(const FrameFormat& format, Span<const std::uint8_t> input, bool crc) noexcept {
    wireforge::FrameDecoder<Checksum> decoder(format);
    wireforge::StaticSpscByteRing<2U * kMaxFrame> ring;
    std::array<std::uint8_t, kMaxFrame> out{};
    std::size_t offset = 0U;
    while (offset < input.size()) {
        const std::size_t chunk = 1U + (input[offset] % 7U);
        offset += ring.write(input.subspan(offset, chunk));
        for (;;) {
            std::size_t size = 0U;
            const Status status = decoder.next(ring, Span<std::uint8_t>(out), size);
            if (status == Status::Truncated) {
                break;
            }
            if (status == Status::Ok) {
                check_frame(format, Span<const std::uint8_t>(out.data(), size), crc);
            } else if (size != 0U) {
                invalid_frame();
            }
        }
        if (ring.free_space() == 0U) {
            invalid_frame(); // the decoder must always make room for more bytes
        }
    }
}

} // namespace

/// @brief libFuzzer entry point.
extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {
    const Span<const std::uint8_t> input(data, size);
    stream<Crc16>(kSyncLength, input, true);
    stream<wireforge::NoChecksum>(kLengthPrefixed, input, false);
    stream<wireforge::NoChecksum>(kFixed, input, false);
    return 0;
}

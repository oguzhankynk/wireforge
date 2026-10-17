/**
 * @file frame_decoder.hpp
 * @brief Extracts complete frames from a byte ring: sync + length, fixed size and length-prefixed
 *        framing, optional checksum verification and resynchronisation.
 */
#ifndef WIREFORGE_FRAME_DECODER_HPP
#define WIREFORGE_FRAME_DECODER_HPP

#include "wireforge/byte_io.hpp"
#include "wireforge/checksum.hpp"
#include "wireforge/frame_layout.hpp"
#include "wireforge/lock.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace wireforge {

/// @brief How frame boundaries are found in the byte stream.
enum class FramingKind : std::uint8_t {
    Fixed,          ///< Every frame has the same size (an optional sync field is still checked).
    SyncLength,     ///< A sync field marks the frame start; a length field gives its size.
    LengthPrefixed, ///< A length field at the frame start gives its size; no sync field.
};

/// @brief What the length field of a frame counts.
enum class LengthCounts : std::uint8_t {
    Payload,           ///< Payload bytes only.
    PayloadAndTrailer, ///< Payload and trailer bytes.
    Frame,             ///< The whole frame, header and trailer included.
};

/// @brief Which bytes a frame checksum covers.
enum class ChecksumCoverage : std::uint8_t {
    HeaderAndPayload, ///< From the first header byte to the end of the payload.
    Payload,          ///< The payload only.
};

/**
 * @brief Layout of the frames of one interface.
 *
 * Sync and length fields are whole bytes inside the header and use the frame byte order. When the
 * decoder's checksum type is not NoChecksum, the checksum is a trailer field of the checksum's
 * size.
 */
struct FrameFormat {
    FramingKind kind;                 ///< Framing method.
    Endian endian;                    ///< Byte order of the sync, length and checksum fields.
    std::size_t header_size;          ///< Header bytes.
    std::size_t trailer_size;         ///< Trailer bytes.
    std::size_t max_frame_size;       ///< Largest frame; the frame size for FramingKind::Fixed.
    FieldLocation sync;               ///< Sync field; required for FramingKind::SyncLength.
    std::uint64_t sync_value;         ///< Expected value of the sync field.
    FieldLocation length;             ///< Length field; required unless the framing is fixed.
    LengthCounts length_counts;       ///< Meaning of the length field.
    std::size_t checksum_offset;      ///< Offset of the checksum inside the trailer.
    ChecksumCoverage checksum_covers; ///< Bytes covered by the checksum.
    bool resync; ///< On errors skip one byte and rescan; otherwise drop all buffered bytes.
};

/// @brief Checksum type meaning "frames carry no checksum".
struct NoChecksum {};

/// @brief Statistics of a FrameDecoder; readable from any thread.
struct FrameCounters {
    RelaxedCounter frames;          ///< Frames delivered.
    RelaxedCounter discarded_bytes; ///< Bytes dropped while searching for a frame start.
    RelaxedCounter length_errors;   ///< Frames rejected because of their length field.
    RelaxedCounter checksum_errors; ///< Frames rejected because of their checksum.
    RelaxedCounter overflow_errors; ///< Frames that did not fit the output buffer.
};

/**
 * @brief Finds frames in a byte ring and copies each complete frame to a caller buffer.
 *
 * Incomplete frames stay in the ring until more bytes arrive, so the result does not depend on how
 * the stream was chunked. The ring must provide `size()`, `capacity()`,
 * `peek(Span<std::uint8_t>, std::size_t)` and `consume(std::size_t)` (SpscByteRing,
 * LockedByteRing). Use one decoder per ring, called from the ring's consumer thread.
 * @tparam Checksum Checksum algorithm of the trailer, or NoChecksum.
 */
template <typename Checksum = NoChecksum> class FrameDecoder {
    static constexpr bool kHasChecksum = !std::is_same_v<Checksum, NoChecksum>;
    static_assert(!kHasChecksum || is_checksum_v<Checksum>,
                  "Checksum must be NoChecksum or satisfy the checksum contract");

public:
    /// @brief Creates a decoder for @p format; check valid() before use.
    explicit constexpr FrameDecoder(const FrameFormat& format) noexcept : format_(format) {}

    /// @brief true when the format is consistent (field positions, sizes, checksum placement).
    [[nodiscard]] constexpr bool valid() const noexcept {
        const FrameFormat& fmt = format_;
        const std::size_t overhead = fmt.header_size + fmt.trailer_size;
        const bool sized = fmt.max_frame_size >= overhead && fmt.max_frame_size > 0U;
        const bool sync_ok =
            field_ok(fmt.sync) && (fmt.kind != FramingKind::SyncLength || fmt.sync.width != 0U);
        const bool length_ok =
            field_ok(fmt.length) && (fmt.kind == FramingKind::Fixed || fmt.length.width != 0U);
        return sized && sync_ok && length_ok && checksum_ok();
    }

    /**
     * @brief Extracts the next frame from @p ring.
     * @param ring Byte source; bytes of delivered and rejected frames are consumed.
     * @param out Destination of the frame (header, payload and trailer).
     * @param[out] frame_size Size of the delivered frame; 0 unless Status::Ok is returned.
     * @return Status::Ok when a frame was copied to @p out;
     *         Status::Truncated when more bytes are needed (call again after the next write);
     *         Status::InvalidLength, Status::ChecksumMismatch or Status::BufferTooSmall when a
     *         frame was rejected (call again to continue with the following bytes);
     *         Status::InvalidState when the format is not valid().
     */
    template <typename Ring>
    Status next(Ring& ring, Span<std::uint8_t> out, std::size_t& frame_size) noexcept {
        frame_size = 0U;
        if (!valid()) {
            return Status::InvalidState;
        }
        for (;;) {
            if (ring.size() < format_.header_size || ring.size() == 0U) {
                return Status::Truncated;
            }
            if (!sync_matches(ring)) {
                counters_.discarded_bytes.add(ring.consume(1U));
                continue;
            }
            std::size_t total = 0U;
            Status status = frame_length(ring, total);
            if (status == Status::Ok && ring.capacity() < total) {
                status = Status::InvalidLength;
            }
            if (status != Status::Ok) {
                counters_.length_errors.increment();
                return reject(ring, status);
            }
            if (ring.size() < total) {
                return Status::Truncated;
            }
            if (out.size() < total) {
                counters_.overflow_errors.increment();
                return reject(ring, Status::BufferTooSmall);
            }
            const Span<std::uint8_t> frame = out.first(total);
            static_cast<void>(ring.peek(frame, 0U));
            if (!checksum_matches(Span<const std::uint8_t>(frame))) {
                counters_.checksum_errors.increment();
                return reject(ring, Status::ChecksumMismatch);
            }
            static_cast<void>(ring.consume(total));
            counters_.frames.increment();
            frame_size = total;
            return Status::Ok;
        }
    }

    /// @brief Statistics since construction or the last reset_counters().
    [[nodiscard]] const FrameCounters& counters() const noexcept { return counters_; }

    /// @brief Sets every counter to zero.
    void reset_counters() noexcept {
        counters_.frames.reset();
        counters_.discarded_bytes.reset();
        counters_.length_errors.reset();
        counters_.checksum_errors.reset();
        counters_.overflow_errors.reset();
    }

    /// @brief The format this decoder was created with.
    [[nodiscard]] constexpr const FrameFormat& format() const noexcept { return format_; }

private:
    [[nodiscard]] constexpr bool field_ok(const FieldLocation& field) const noexcept {
        return field.width == 0U ||
               (valid_width(field.width) && field.offset <= format_.header_size &&
                format_.header_size - field.offset >= field.width);
    }

    [[nodiscard]] constexpr bool checksum_ok() const noexcept {
        if constexpr (kHasChecksum) {
            return format_.checksum_offset <= format_.trailer_size &&
                   format_.trailer_size - format_.checksum_offset >= Checksum::kSize;
        } else {
            return true;
        }
    }

    template <typename Ring>
    [[nodiscard]] std::uint64_t peek_field(const Ring& ring,
                                           const FieldLocation& field) const noexcept {
        std::array<std::uint8_t, kMaxWidth> bytes{};
        const Span<std::uint8_t> view = Span<std::uint8_t>(bytes).first(field.width);
        static_cast<void>(ring.peek(view, field.offset));
        return load_unsigned(Span<const std::uint8_t>(view), field.width, format_.endian);
    }

    template <typename Ring> [[nodiscard]] bool sync_matches(const Ring& ring) const noexcept {
        if (format_.sync.width == 0U) {
            return true;
        }
        return peek_field(ring, format_.sync) == format_.sync_value;
    }

    template <typename Ring>
    Status frame_length(const Ring& ring, std::size_t& total) const noexcept {
        const std::size_t overhead = format_.header_size + format_.trailer_size;
        if (format_.kind == FramingKind::Fixed) {
            total = format_.max_frame_size;
            return Status::Ok;
        }
        const std::uint64_t length = peek_field(ring, format_.length);
        if (length > format_.max_frame_size) {
            return Status::InvalidLength;
        }
        const auto value = static_cast<std::size_t>(length);
        // An if chain: a switch over every LengthCounts value needs a default label that
        // -Wcovered-switch-default rejects.
        if (format_.length_counts == LengthCounts::Payload) {
            total = overhead + value;
        } else if (format_.length_counts == LengthCounts::PayloadAndTrailer) {
            total = format_.header_size + value;
        } else {
            total = value;
        }
        return total >= overhead && total <= format_.max_frame_size ? Status::Ok
                                                                    : Status::InvalidLength;
    }

    [[nodiscard]] bool checksum_matches(Span<const std::uint8_t> frame) const noexcept {
        if constexpr (kHasChecksum) {
            const std::size_t payload_end = frame.size() - format_.trailer_size;
            const std::size_t start =
                format_.checksum_covers == ChecksumCoverage::Payload ? format_.header_size : 0U;
            const std::uint64_t expected =
                compute_checksum<Checksum>(frame.subspan(start, payload_end - start));
            const std::uint64_t received =
                load_unsigned(frame.subspan(payload_end + format_.checksum_offset), Checksum::kSize,
                              format_.endian);
            return expected == received;
        } else {
            static_cast<void>(frame);
            return true;
        }
    }

    template <typename Ring> Status reject(Ring& ring, Status status) noexcept {
        const std::size_t dropped = format_.resync ? ring.consume(1U) : ring.consume(ring.size());
        counters_.discarded_bytes.add(dropped);
        return status;
    }

    FrameFormat format_;
    FrameCounters counters_;
};

} // namespace wireforge

#endif // WIREFORGE_FRAME_DECODER_HPP

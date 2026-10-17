/**
 * @file byte_io.hpp
 * @brief Host-independent reading and writing of integers, floats and bit groups.
 */
#ifndef WIREFORGE_BYTE_IO_HPP
#define WIREFORGE_BYTE_IO_HPP

#include "wireforge/bit_utils.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <cstddef>
#include <cstdint>
#include <cstring>
#include <limits>

namespace wireforge {

/// @brief Byte order of multi-byte values on the wire.
enum class Endian : std::uint8_t {
    Big,    ///< Most significant byte first.
    Little, ///< Least significant byte first.
};

static_assert(std::numeric_limits<float>::is_iec559 && sizeof(float) == 4U,
              "f32 requires IEEE-754 binary32");
static_assert(std::numeric_limits<double>::is_iec559 && sizeof(double) == 8U,
              "f64 requires IEEE-754 binary64");

/// @brief Largest integer width in bytes.
inline constexpr std::size_t kMaxWidth = 8U;

/**
 * @brief Stores the low @p width bytes of @p value at the start of @p out.
 *
 * Uses shifts only, so the result never depends on the host byte order.
 * @param out Destination; must hold at least @p width bytes.
 * @param value Value to store.
 * @param width Number of bytes, 1..8.
 * @param endian Byte order.
 */
constexpr void store_unsigned(Span<std::uint8_t> out, std::uint64_t value, std::size_t width,
                              Endian endian) noexcept {
    for (std::size_t index = 0U; index < width; ++index) {
        const std::size_t byte_index = endian == Endian::Big ? width - 1U - index : index;
        out[byte_index] = static_cast<std::uint8_t>((value >> (8U * index)) & 0xFFU);
    }
}

/**
 * @brief Loads an unsigned integer of @p width bytes from the start of @p input.
 * @param input Source; must hold at least @p width bytes.
 * @param width Number of bytes, 1..8.
 * @param endian Byte order.
 * @return The value, zero-extended to 64 bits.
 */
[[nodiscard]] constexpr std::uint64_t load_unsigned(Span<const std::uint8_t> input,
                                                    std::size_t width, Endian endian) noexcept {
    std::uint64_t value = 0U;
    for (std::size_t index = 0U; index < width; ++index) {
        const std::size_t byte_index = endian == Endian::Big ? index : width - 1U - index;
        value = (value << 8U) | input[byte_index];
    }
    return value;
}

/// @brief true when @p width is a supported integer width (1..8 bytes).
[[nodiscard]] constexpr bool valid_width(std::size_t width) noexcept {
    return width >= 1U && width <= kMaxWidth;
}

/// @brief Number of bits in @p width bytes.
[[nodiscard]] constexpr unsigned width_bits(std::size_t width) noexcept {
    return narrow_to<unsigned>(width * 8U);
}

/**
 * @brief Sequential writer over a caller-provided buffer.
 *
 * Every write returns a Status; a failing write changes neither the position nor the buffer.
 * All functions except the float writers are constexpr.
 */
class ByteWriter {
public:
    /// @brief Writes into @p buffer starting at its first byte.
    explicit constexpr ByteWriter(Span<std::uint8_t> buffer) noexcept : buffer_(buffer) {}

    /// @brief Number of bytes written so far.
    [[nodiscard]] constexpr std::size_t position() const noexcept { return position_; }
    /// @brief Size of the buffer.
    [[nodiscard]] constexpr std::size_t capacity() const noexcept { return buffer_.size(); }
    /// @brief Bytes still free.
    [[nodiscard]] constexpr std::size_t remaining() const noexcept {
        return buffer_.size() - position_;
    }
    /// @brief The bytes written so far.
    [[nodiscard]] constexpr Span<const std::uint8_t> written() const noexcept {
        return Span<const std::uint8_t>(buffer_.first(position_));
    }

    /**
     * @brief Writes an unsigned integer.
     * @param value Value; must fit @p width bytes.
     * @param width Number of bytes, 1..8.
     * @param endian Byte order.
     * @return Status::OutOfRange when @p value does not fit, Status::BufferTooSmall when the
     *         buffer is full, Status::InvalidState for an unsupported @p width.
     */
    constexpr Status write_unsigned(std::uint64_t value, std::size_t width,
                                    Endian endian) noexcept {
        if (!valid_width(width)) {
            return Status::InvalidState;
        }
        if (!fits_unsigned(value, width_bits(width))) {
            return Status::OutOfRange;
        }
        if (remaining() < width) {
            return Status::BufferTooSmall;
        }
        store_unsigned(buffer_.subspan(position_), value, width, endian);
        position_ += width;
        return Status::Ok;
    }

    /**
     * @brief Writes a two's complement integer.
     * @param value Value; must fit @p width bytes as a signed integer.
     * @param width Number of bytes, 1..8.
     * @param endian Byte order.
     * @return As write_unsigned().
     */
    constexpr Status write_signed(std::int64_t value, std::size_t width, Endian endian) noexcept {
        if (!valid_width(width)) {
            return Status::InvalidState;
        }
        if (!fits_signed(value, width_bits(width))) {
            return Status::OutOfRange;
        }
        return write_unsigned(to_twos_complement(value, width_bits(width)), width, endian);
    }

    /**
     * @brief Overwrites @p width bytes at @p offset inside the already written region.
     *
     * Used to fill in length or checksum fields after the rest of a frame has been written.
     * @return Status::InvalidLength when [offset, offset + width) is not fully written yet;
     *         otherwise as write_unsigned().
     */
    constexpr Status write_unsigned_at(std::size_t offset, std::uint64_t value, std::size_t width,
                                       Endian endian) const noexcept {
        if (!valid_width(width)) {
            return Status::InvalidState;
        }
        if (!fits_unsigned(value, width_bits(width))) {
            return Status::OutOfRange;
        }
        if (offset > position_ || position_ - offset < width) {
            return Status::InvalidLength;
        }
        store_unsigned(buffer_.subspan(offset), value, width, endian);
        return Status::Ok;
    }

    /**
     * @brief Writes an IEEE-754 binary32 value through its bit pattern.
     * @return Status::BufferTooSmall when fewer than 4 bytes are free.
     */
    Status write_f32(float value, Endian endian) noexcept {
        std::uint32_t bits = 0U;
        std::memcpy(&bits, &value, sizeof(bits));
        return write_unsigned(bits, sizeof(bits), endian);
    }

    /**
     * @brief Writes an IEEE-754 binary64 value through its bit pattern.
     * @return Status::BufferTooSmall when fewer than 8 bytes are free.
     */
    Status write_f64(double value, Endian endian) noexcept {
        std::uint64_t bits = 0U;
        std::memcpy(&bits, &value, sizeof(bits));
        return write_unsigned(bits, sizeof(bits), endian);
    }

    /**
     * @brief Copies @p bytes.
     * @return Status::BufferTooSmall when they do not fit.
     */
    constexpr Status write_bytes(Span<const std::uint8_t> bytes) noexcept {
        if (remaining() < bytes.size()) {
            return Status::BufferTooSmall;
        }
        for (std::size_t index = 0U; index < bytes.size(); ++index) {
            buffer_[position_ + index] = bytes[index];
        }
        position_ += bytes.size();
        return Status::Ok;
    }

    /**
     * @brief Writes @p count copies of @p value.
     * @return Status::BufferTooSmall when they do not fit.
     */
    constexpr Status write_fill(std::uint8_t value, std::size_t count) noexcept {
        if (remaining() < count) {
            return Status::BufferTooSmall;
        }
        for (std::size_t index = 0U; index < count; ++index) {
            buffer_[position_ + index] = value;
        }
        position_ += count;
        return Status::Ok;
    }

    /**
     * @brief Writes the container of a complete bit group.
     * @return Status::InvalidState when @p packer is not complete; otherwise as write_unsigned().
     */
    constexpr Status write_bits(const BitPacker& packer, Endian endian) noexcept {
        if (!packer.complete()) {
            return Status::InvalidState;
        }
        return write_unsigned(packer.container(), packer.total_bits() / 8U, endian);
    }

private:
    Span<std::uint8_t> buffer_;
    std::size_t position_ = 0U;
};

/**
 * @brief Sequential reader over a caller-provided buffer.
 *
 * A failing read changes neither the position nor its output argument. All functions except the
 * float readers are constexpr.
 */
class ByteReader {
public:
    /// @brief Reads from @p buffer starting at its first byte.
    explicit constexpr ByteReader(Span<const std::uint8_t> buffer) noexcept : buffer_(buffer) {}

    /// @brief Number of bytes consumed so far.
    [[nodiscard]] constexpr std::size_t position() const noexcept { return position_; }
    /// @brief Size of the buffer.
    [[nodiscard]] constexpr std::size_t size() const noexcept { return buffer_.size(); }
    /// @brief Bytes not consumed yet.
    [[nodiscard]] constexpr std::size_t remaining() const noexcept {
        return buffer_.size() - position_;
    }
    /// @brief The bytes not consumed yet.
    [[nodiscard]] constexpr Span<const std::uint8_t> rest() const noexcept {
        return buffer_.subspan(position_);
    }

    /**
     * @brief Reads an unsigned integer.
     * @param[out] value Result, zero-extended.
     * @param width Number of bytes, 1..8.
     * @param endian Byte order.
     * @return Status::Truncated when fewer than @p width bytes remain, Status::InvalidState for an
     *         unsupported @p width.
     */
    constexpr Status read_unsigned(std::uint64_t& value, std::size_t width,
                                   Endian endian) noexcept {
        if (!valid_width(width)) {
            return Status::InvalidState;
        }
        if (remaining() < width) {
            return Status::Truncated;
        }
        value = load_unsigned(buffer_.subspan(position_), width, endian);
        position_ += width;
        return Status::Ok;
    }

    /**
     * @brief Reads a two's complement integer and sign-extends it.
     * @return As read_unsigned().
     */
    constexpr Status read_signed(std::int64_t& value, std::size_t width, Endian endian) noexcept {
        std::uint64_t raw = 0U;
        const Status status = read_unsigned(raw, width, endian);
        if (status == Status::Ok) {
            value = sign_extend(raw, width_bits(width));
        }
        return status;
    }

    /**
     * @brief Reads @p width bytes at absolute @p offset without moving the position.
     * @return Status::Truncated when the range is outside the buffer; otherwise as
     *         read_unsigned().
     */
    constexpr Status peek_unsigned_at(std::size_t offset, std::uint64_t& value, std::size_t width,
                                      Endian endian) const noexcept {
        if (!valid_width(width)) {
            return Status::InvalidState;
        }
        if (offset > buffer_.size() || buffer_.size() - offset < width) {
            return Status::Truncated;
        }
        value = load_unsigned(buffer_.subspan(offset), width, endian);
        return Status::Ok;
    }

    /**
     * @brief Reads an IEEE-754 binary32 value from its bit pattern.
     * @return Status::Truncated when fewer than 4 bytes remain.
     */
    Status read_f32(float& value, Endian endian) noexcept {
        std::uint64_t raw = 0U;
        const Status status = read_unsigned(raw, sizeof(float), endian);
        if (status == Status::Ok) {
            const auto bits = static_cast<std::uint32_t>(raw);
            std::memcpy(&value, &bits, sizeof(value));
        }
        return status;
    }

    /**
     * @brief Reads an IEEE-754 binary64 value from its bit pattern.
     * @return Status::Truncated when fewer than 8 bytes remain.
     */
    Status read_f64(double& value, Endian endian) noexcept {
        std::uint64_t bits = 0U;
        const Status status = read_unsigned(bits, sizeof(double), endian);
        if (status == Status::Ok) {
            std::memcpy(&value, &bits, sizeof(value));
        }
        return status;
    }

    /**
     * @brief Copies out.size() bytes into @p out.
     * @return Status::Truncated when not enough bytes remain.
     */
    constexpr Status read_bytes(Span<std::uint8_t> out) noexcept {
        if (remaining() < out.size()) {
            return Status::Truncated;
        }
        for (std::size_t index = 0U; index < out.size(); ++index) {
            out[index] = buffer_[position_ + index];
        }
        position_ += out.size();
        return Status::Ok;
    }

    /**
     * @brief Skips @p count bytes.
     * @return Status::Truncated when not enough bytes remain.
     */
    constexpr Status skip(std::size_t count) noexcept {
        if (remaining() < count) {
            return Status::Truncated;
        }
        position_ += count;
        return Status::Ok;
    }

    /**
     * @brief Reads the container of a bit group and prepares an unpacker for its fields.
     * @param[out] unpacker Replaced by an unpacker over the container on success.
     * @param total_bits Width of the group: 8, 16, ..., 64.
     * @param order Placement of the first field.
     * @param endian Byte order of the container.
     * @return Status::InvalidState for an invalid group width; otherwise as read_unsigned().
     */
    constexpr Status read_bits(BitUnpacker& unpacker, unsigned total_bits, BitOrder order,
                               Endian endian) noexcept {
        if (!BitUnpacker(0U, total_bits, order).valid()) {
            return Status::InvalidState;
        }
        std::uint64_t container = 0U;
        const Status status = read_unsigned(container, total_bits / 8U, endian);
        if (status == Status::Ok) {
            unpacker = BitUnpacker(container, total_bits, order);
        }
        return status;
    }

private:
    Span<const std::uint8_t> buffer_;
    std::size_t position_ = 0U;
};

} // namespace wireforge

#endif // WIREFORGE_BYTE_IO_HPP

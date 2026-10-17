/**
 * @file codec_support.hpp
 * @brief Encoder and Decoder used by generated serialize/deserialize functions.
 *
 * Both keep the first error ("sticky" status): after a failure every further operation does
 * nothing, so generated code is a straight sequence of calls followed by `status()`.
 */
#ifndef WIREFORGE_CODEC_SUPPORT_HPP
#define WIREFORGE_CODEC_SUPPORT_HPP

#include "wireforge/bit_utils.hpp"
#include "wireforge/byte_io.hpp"
#include "wireforge/checksum.hpp"
#include "wireforge/span.hpp"
#include "wireforge/static_vector.hpp"
#include "wireforge/status.hpp"

#include <cstddef>
#include <cstdint>
#include <limits>
#include <type_traits>

namespace wireforge {

/// @brief Encoding settings of one message on one interface.
struct Encoding {
    Endian endian = Endian::Big;             ///< Byte order of multi-byte values.
    BitOrder bit_order = BitOrder::MsbFirst; ///< Placement of fields in bit groups.
    bool check_encode = true;                ///< Check min/max and enum values when encoding.
    bool check_decode = true;                ///< Check min/max and enum values when decoding.
    bool allow_trailing_bytes = false;       ///< Accept payload bytes after the last field.
};

/// @cond INTERNAL
namespace detail {

template <typename T> struct WireInt {
    using type = T;
};
template <> struct WireInt<bool> {
    using type = std::uint8_t;
};
template <> struct WireInt<char> {
    using type = std::uint8_t;
};

// Integer type that carries the wire value of T (enums: their underlying type).
template <typename T>
using wire_int_t =
    typename WireInt<typename std::conditional_t<std::is_enum_v<T>, std::underlying_type<T>,
                                                 std::enable_if<true, T>>::type>::type;

// Converts without a cast when the types already match (keeps -Wuseless-cast quiet).
template <typename To, typename From> [[nodiscard]] constexpr To convert(From value) noexcept {
    if constexpr (std::is_same_v<To, From>) {
        return value;
    } else {
        return static_cast<To>(value);
    }
}

} // namespace detail
/// @endcond

/**
 * @brief Sequential encoder with a sticky status.
 */
class Encoder {
public:
    /// @brief Encodes into @p buffer with @p encoding.
    Encoder(Span<std::uint8_t> buffer, const Encoding& encoding) noexcept
        : writer_(buffer), encoding_(encoding) {}

    /// @brief Settings in use.
    [[nodiscard]] const Encoding& encoding() const noexcept { return encoding_; }
    /// @brief The first error, or Status::Ok.
    [[nodiscard]] Status status() const noexcept { return status_; }
    /// @brief Bytes written so far.
    [[nodiscard]] std::size_t position() const noexcept { return writer_.position(); }

    /// @brief Records @p status unless an earlier error is already recorded.
    void check(Status status) noexcept {
        if (status_ == Status::Ok) {
            status_ = status;
        }
    }

    /// @brief Records @p status when encode-side value checks are enabled.
    void check_value(Status status) noexcept {
        if (encoding_.check_encode) {
            check(status);
        }
    }

    /**
     * @brief Writes @p value as wire type T (integers, bool, char, enums, float, double).
     */
    template <typename T> void write(T value) noexcept {
        if (status_ != Status::Ok) {
            return;
        }
        if constexpr (std::is_same_v<T, float>) {
            check(writer_.write_f32(value, encoding_.endian));
        } else if constexpr (std::is_same_v<T, double>) {
            check(writer_.write_f64(value, encoding_.endian));
        } else {
            using Wire = detail::wire_int_t<T>;
            const auto wire = detail::convert<Wire>(value);
            if constexpr (std::is_signed_v<Wire>) {
                check(writer_.write_signed(wire, sizeof(Wire), encoding_.endian));
            } else {
                check(writer_.write_unsigned(wire, sizeof(Wire), encoding_.endian));
            }
        }
    }

    /**
     * @brief Writes an element count as wire type W (bool counts must be 0 or 1).
     */
    template <typename W> void write_count(std::size_t count) noexcept {
        const std::uint64_t limit = std::is_same_v<W, bool>
                                        ? 1U
                                        : static_cast<std::uint64_t>(std::numeric_limits<W>::max());
        if (count > limit) {
            check(Status::OutOfRange);
            return;
        }
        if constexpr (std::is_same_v<W, bool>) {
            write(count == 1U);
        } else {
            write(detail::convert<W>(count));
        }
    }

    /// @brief Writes the first @p count characters of @p text and pads with zeros to @p width.
    template <std::size_t N>
    void write_text(const FixedString<N>& text, std::size_t width) noexcept {
        for (std::size_t index = 0U; index < width; ++index) {
            write(index < text.size() ? text[index] : '\0');
        }
    }

    /**
     * @brief Adds a field to a bit group (bool, enum, signed or unsigned integer).
     */
    template <typename T> void put(BitPacker& group, T value, unsigned bits) noexcept {
        using Wire = detail::wire_int_t<T>;
        const auto wire = detail::convert<Wire>(value);
        if constexpr (std::is_signed_v<Wire>) {
            check(group.put_signed(wire, bits));
        } else {
            check(group.put(wire, bits));
        }
    }

    /// @brief Writes a complete bit group.
    void write_bits(const BitPacker& group) noexcept {
        if (status_ == Status::Ok) {
            check(writer_.write_bits(group, encoding_.endian));
        }
    }

    /**
     * @brief Writes the checksum C of the bytes written in [@p begin, @p end).
     */
    template <typename C> void write_checksum(std::size_t begin, std::size_t end) noexcept {
        if (status_ != Status::Ok) {
            return;
        }
        const Span<const std::uint8_t> covered = writer_.written().subspan(begin, end - begin);
        check(writer_.write_unsigned(compute_checksum<C>(covered), C::kSize, encoding_.endian));
    }

private:
    ByteWriter writer_;
    Encoding encoding_;
    Status status_ = Status::Ok;
};

/**
 * @brief Sequential decoder with a sticky status.
 */
class Decoder {
public:
    /// @brief Decodes @p input with @p encoding.
    Decoder(Span<const std::uint8_t> input, const Encoding& encoding) noexcept
        : input_(input), reader_(input), encoding_(encoding) {}

    /// @brief Settings in use.
    [[nodiscard]] const Encoding& encoding() const noexcept { return encoding_; }
    /// @brief The first error, or Status::Ok.
    [[nodiscard]] Status status() const noexcept { return status_; }
    /// @brief Bytes consumed so far.
    [[nodiscard]] std::size_t position() const noexcept { return reader_.position(); }
    /// @brief Bytes not consumed yet.
    [[nodiscard]] std::size_t remaining() const noexcept { return reader_.remaining(); }

    /// @brief Records @p status unless an earlier error is already recorded.
    void check(Status status) noexcept {
        if (status_ == Status::Ok) {
            status_ = status;
        }
    }

    /// @brief Records @p status when decode-side value checks are enabled.
    void check_value(Status status) noexcept {
        if (encoding_.check_decode) {
            check(status);
        }
    }

    /**
     * @brief Reads a value of wire type T into @p out (a bool must be 0 or 1).
     */
    template <typename T> void read(T& out) noexcept {
        if (status_ != Status::Ok) {
            return;
        }
        if constexpr (std::is_same_v<T, float>) {
            check(reader_.read_f32(out, encoding_.endian));
        } else if constexpr (std::is_same_v<T, double>) {
            check(reader_.read_f64(out, encoding_.endian));
        } else {
            using Wire = detail::wire_int_t<T>;
            Wire wire{};
            read_int(wire);
            assign(out, wire);
        }
    }

    /// @brief Reads an element count stored as wire type W.
    template <typename W> [[nodiscard]] std::size_t read_count() noexcept {
        W value{};
        read(value);
        return detail::convert<std::size_t>(value);
    }

    /// @brief Reads a value of wire type T and fails with ConstMismatch unless it is @p expected.
    template <typename T> void expect(T expected) noexcept {
        T value{};
        read(value);
        if (status_ == Status::Ok && !(value == expected)) {
            check(Status::ConstMismatch);
        }
    }

    /// @brief Skips @p count bytes.
    void skip(std::size_t count) noexcept {
        if (status_ == Status::Ok) {
            check(reader_.skip(count));
        }
    }

    /**
     * @brief Reads @p width characters; a fixed-width text drops trailing zeros.
     */
    template <std::size_t N>
    void read_text(FixedString<N>& text, std::size_t width, bool fixed) noexcept {
        if (width > N) {
            check(Status::CountExceedsMax);
        }
        if (status_ != Status::Ok) {
            return;
        }
        static_cast<void>(text.resize(width));
        std::size_t length = 0U;
        for (std::size_t index = 0U; index < width; ++index) {
            read(text[index]);
            length = text[index] != '\0' || !fixed ? index + 1U : length;
        }
        static_cast<void>(text.resize(length));
    }

    /// @brief Sets the size of @p container to @p count (CountExceedsMax outside [low, capacity]).
    template <typename Container>
    void resize(Container& container, std::size_t count, std::size_t low) noexcept {
        if (count < low || count > Container::capacity()) {
            check(Status::CountExceedsMax);
        }
        if (status_ == Status::Ok) {
            static_cast<void>(container.resize(count));
        }
    }

    /**
     * @brief Number of elements of @p element_size bytes in the rest of the input.
     * @return 0 with InvalidLength when the rest is not a multiple of the element size.
     */
    [[nodiscard]] std::size_t fill_count(std::size_t element_size) noexcept {
        if (element_size == 0U || remaining() % element_size != 0U) {
            check(Status::InvalidLength);
            return 0U;
        }
        return remaining() / element_size;
    }

    /// @brief Reads a bit group of @p total_bits bits.
    [[nodiscard]] BitUnpacker read_bits(unsigned total_bits) noexcept {
        BitUnpacker group(0U, total_bits, encoding_.bit_order);
        if (status_ == Status::Ok) {
            check(reader_.read_bits(group, total_bits, encoding_.bit_order, encoding_.endian));
        }
        return group;
    }

    /// @brief Extracts the next field of a bit group into @p out.
    template <typename T> void get(BitUnpacker& group, T& out, unsigned bits) noexcept {
        using Wire = detail::wire_int_t<T>;
        if constexpr (std::is_signed_v<Wire>) {
            std::int64_t value = 0;
            check(group.get_signed(value, bits));
            assign(out, detail::convert<Wire>(value));
        } else {
            std::uint64_t value = 0U;
            check(group.get(value, bits));
            assign(out, detail::convert<Wire>(value));
        }
    }

    /// @brief Extracts a constant field of a bit group (ConstMismatch when it differs).
    template <typename T> void expect_bits(BitUnpacker& group, T expected, unsigned bits) noexcept {
        T value{};
        get(group, value, bits);
        if (status_ == Status::Ok && !(value == expected)) {
            check(Status::ConstMismatch);
        }
    }

    /// @brief Skips a reserved field of a bit group.
    void skip_bits(BitUnpacker& group, unsigned bits) noexcept {
        std::uint64_t ignored = 0U;
        check(group.get(ignored, bits));
    }

    /**
     * @brief Reads a checksum C and compares it with the checksum of the input [@p begin, @p end).
     */
    template <typename C> void verify_checksum(std::size_t begin, std::size_t end) noexcept {
        std::uint64_t received = 0U;
        if (status_ == Status::Ok) {
            check(reader_.read_unsigned(received, C::kSize, encoding_.endian));
        }
        if (status_ == Status::Ok &&
            compute_checksum<C>(input_.subspan(begin, end - begin)) != received) {
            check(Status::ChecksumMismatch);
        }
    }

    /// @brief Fails with TrailingBytes when input is left and trailing bytes are not allowed.
    void finish() noexcept {
        if (remaining() > 0U && !encoding_.allow_trailing_bytes) {
            check(Status::TrailingBytes);
        }
    }

private:
    template <typename Wire> void read_int(Wire& wire) noexcept {
        if constexpr (std::is_signed_v<Wire>) {
            std::int64_t value = 0;
            check(reader_.read_signed(value, sizeof(Wire), encoding_.endian));
            wire = detail::convert<Wire>(value);
        } else {
            std::uint64_t value = 0U;
            check(reader_.read_unsigned(value, sizeof(Wire), encoding_.endian));
            wire = detail::convert<Wire>(value);
        }
    }

    template <typename T, typename Wire> void assign(T& out, Wire wire) noexcept {
        if constexpr (std::is_same_v<T, bool>) {
            if (wire > 1U) {
                check(Status::OutOfRange);
            }
            out = wire == 1U;
        } else {
            out = detail::convert<T>(wire);
        }
    }

    Span<const std::uint8_t> input_;
    ByteReader reader_;
    Encoding encoding_;
    Status status_ = Status::Ok;
};

} // namespace wireforge

#endif // WIREFORGE_CODEC_SUPPORT_HPP

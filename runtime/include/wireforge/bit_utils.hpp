/**
 * @file bit_utils.hpp
 * @brief Bit masks, two's complement helpers and packing of bit-field groups.
 */
#ifndef WIREFORGE_BIT_UTILS_HPP
#define WIREFORGE_BIT_UTILS_HPP

#include "wireforge/status.hpp"

#include <cstdint>
#include <type_traits>

namespace wireforge {

/**
 * @brief Converts @p value to the integer type @p To; the value must fit.
 *
 * For conversions whose source width depends on the platform (std::size_t is 32 bits wide on
 * 32-bit targets), so that the same code is free of useless-cast and conversion warnings on
 * every target.
 */
template <typename To, typename From> [[nodiscard]] constexpr To narrow_to(From value) noexcept {
    static_assert(std::is_integral_v<To> && std::is_integral_v<From>, "integer conversions only");
    return static_cast<To>(value);
}

/// @brief Order in which the fields of a bit group are placed in the group.
enum class BitOrder : std::uint8_t {
    MsbFirst, ///< The first field takes the most significant bits of the group.
    LsbFirst, ///< The first field takes the least significant bits of the group.
};

/// @brief Largest supported bit width (fields and bit groups).
inline constexpr unsigned kMaxBits = 64U;

/**
 * @brief Mask with the low @p bits bits set.
 * @param bits Number of bits, 0..64.
 */
[[nodiscard]] constexpr std::uint64_t low_mask(unsigned bits) noexcept {
    return bits >= kMaxBits ? ~std::uint64_t{0U} : ((std::uint64_t{1U} << bits) - 1U);
}

/// @brief true when @p value is representable as an unsigned integer of @p bits bits.
[[nodiscard]] constexpr bool fits_unsigned(std::uint64_t value, unsigned bits) noexcept {
    return (value & ~low_mask(bits)) == 0U;
}

/**
 * @brief true when @p value is representable as a two's complement integer of @p bits bits.
 * @param value Value to test.
 * @param bits Width, 1..64 (0 always gives false).
 */
[[nodiscard]] constexpr bool fits_signed(std::int64_t value, unsigned bits) noexcept {
    if (bits == 0U) {
        return false;
    }
    if (bits >= kMaxBits) {
        return true;
    }
    const std::int64_t limit = std::int64_t{1} << (bits - 1U);
    return value >= -limit && value < limit;
}

/// @brief Two's complement bit pattern of @p value truncated to @p bits bits.
[[nodiscard]] constexpr std::uint64_t to_twos_complement(std::int64_t value,
                                                         unsigned bits) noexcept {
    return static_cast<std::uint64_t>(value) & low_mask(bits);
}

/**
 * @brief Interprets the low @p bits bits of @p raw as two's complement and sign-extends them.
 *
 * Avoids the implementation-defined unsigned-to-signed conversion of C++17.
 * @param raw Bit pattern; bits above @p bits are ignored.
 * @param bits Width, 1..64.
 */
[[nodiscard]] constexpr std::int64_t sign_extend(std::uint64_t raw, unsigned bits) noexcept {
    const std::uint64_t mask = low_mask(bits);
    const std::uint64_t value = raw & mask;
    const std::uint64_t sign_bit = std::uint64_t{1U} << (bits - 1U);
    if ((value & sign_bit) == 0U) {
        return static_cast<std::int64_t>(value);
    }
    return -static_cast<std::int64_t>(~value & mask) - 1;
}

/// @brief Reverses the order of the low @p bits bits of @p value.
[[nodiscard]] constexpr std::uint64_t reflect(std::uint64_t value, unsigned bits) noexcept {
    std::uint64_t result = 0U;
    for (unsigned index = 0U; index < bits; ++index) {
        result = (result << 1U) | ((value >> index) & 1U);
    }
    return result;
}

/**
 * @brief Packs consecutive bit fields into one container integer.
 *
 * The group is 8..64 bits wide, a multiple of 8. Fields are added in declaration order with put();
 * when complete(), the container is written with ByteWriter::write_bits in the message byte order.
 */
class BitPacker {
public:
    /**
     * @param total_bits Width of the group: 8, 16, ..., 64.
     * @param order Placement of the first field.
     */
    constexpr BitPacker(unsigned total_bits, BitOrder order) noexcept
        : total_bits_(total_bits), order_(order) {}

    /**
     * @brief Appends the next unsigned field.
     * @param value Field value.
     * @param bits Field width.
     * @return Status::OutOfRange when @p value needs more than @p bits bits; Status::InvalidState
     *         when the group width is invalid, @p bits is 0 or the field does not fit the group.
     */
    constexpr Status put(std::uint64_t value, unsigned bits) noexcept {
        if (!valid() || bits == 0U || bits > total_bits_ - used_bits_) {
            return Status::InvalidState;
        }
        if (!fits_unsigned(value, bits)) {
            return Status::OutOfRange;
        }
        const unsigned shift =
            order_ == BitOrder::MsbFirst ? total_bits_ - used_bits_ - bits : used_bits_;
        container_ |= value << shift;
        used_bits_ += bits;
        return Status::Ok;
    }

    /**
     * @brief Appends the next signed field as @p bits-bit two's complement.
     * @return As put(); Status::OutOfRange when @p value does not fit @p bits bits.
     */
    constexpr Status put_signed(std::int64_t value, unsigned bits) noexcept {
        if (!fits_signed(value, bits)) {
            return bits == 0U ? Status::InvalidState : Status::OutOfRange;
        }
        return put(to_twos_complement(value, bits), bits);
    }

    /// @brief true when the group width is 8..64 and a multiple of 8.
    [[nodiscard]] constexpr bool valid() const noexcept {
        return total_bits_ >= 8U && total_bits_ <= kMaxBits && total_bits_ % 8U == 0U;
    }
    /// @brief true when the fields fill the whole group.
    [[nodiscard]] constexpr bool complete() const noexcept {
        return valid() && used_bits_ == total_bits_;
    }
    /// @brief Width of the group in bits.
    [[nodiscard]] constexpr unsigned total_bits() const noexcept { return total_bits_; }
    /// @brief Packed value of the group.
    [[nodiscard]] constexpr std::uint64_t container() const noexcept { return container_; }

private:
    unsigned total_bits_;
    unsigned used_bits_ = 0U;
    BitOrder order_;
    std::uint64_t container_ = 0U;
};

/// @brief Splits the container integer of a bit group back into fields, in declaration order.
class BitUnpacker {
public:
    /**
     * @param container Packed group value, usually from ByteReader::read_bits.
     * @param total_bits Width of the group: 8, 16, ..., 64.
     * @param order Placement of the first field.
     */
    constexpr BitUnpacker(std::uint64_t container, unsigned total_bits, BitOrder order) noexcept
        : container_(container), total_bits_(total_bits), order_(order) {}

    /**
     * @brief Extracts the next unsigned field.
     * @param[out] value Field value; unchanged on failure.
     * @param bits Field width.
     * @return Status::InvalidState when the group is invalid, @p bits is 0 or the group is
     *         exhausted.
     */
    constexpr Status get(std::uint64_t& value, unsigned bits) noexcept {
        if (!valid() || bits == 0U || bits > total_bits_ - used_bits_) {
            return Status::InvalidState;
        }
        const unsigned shift =
            order_ == BitOrder::MsbFirst ? total_bits_ - used_bits_ - bits : used_bits_;
        value = (container_ >> shift) & low_mask(bits);
        used_bits_ += bits;
        return Status::Ok;
    }

    /**
     * @brief Extracts the next field as @p bits-bit two's complement, sign-extended.
     * @param[out] value Field value; unchanged on failure.
     * @param bits Field width.
     * @return As get().
     */
    constexpr Status get_signed(std::int64_t& value, unsigned bits) noexcept {
        std::uint64_t raw = 0U;
        const Status status = get(raw, bits);
        if (status == Status::Ok) {
            value = sign_extend(raw, bits);
        }
        return status;
    }

    /// @brief true when the group width is 8..64 and a multiple of 8.
    [[nodiscard]] constexpr bool valid() const noexcept {
        return total_bits_ >= 8U && total_bits_ <= kMaxBits && total_bits_ % 8U == 0U;
    }
    /// @brief true when every bit of the group has been extracted.
    [[nodiscard]] constexpr bool complete() const noexcept {
        return valid() && used_bits_ == total_bits_;
    }

private:
    std::uint64_t container_;
    unsigned total_bits_;
    unsigned used_bits_ = 0U;
    BitOrder order_;
};

} // namespace wireforge

#endif // WIREFORGE_BIT_UTILS_HPP

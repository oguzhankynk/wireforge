/**
 * @file checksum.hpp
 * @brief Checksum algorithm requirements and a parametric CRC engine.
 *
 * A checksum algorithm is a type @c C with only static members:
 * @code
 * struct MyChecksum {
 *     static constexpr std::size_t kSize = 2;   // bytes of the value on the wire, 1..8
 *     struct State { ... };                     // copyable running state
 *     static State init() noexcept;
 *     static void update(State&, wireforge::Span<const std::uint8_t>) noexcept; // any chunking
 *     static std::uint64_t finalize(const State&) noexcept;
 * };
 * static_assert(wireforge::is_checksum_v<MyChecksum>);
 * @endcode
 * The finalized value is written with the byte order of the message that carries it.
 */
#ifndef WIREFORGE_CHECKSUM_HPP
#define WIREFORGE_CHECKSUM_HPP

#include "wireforge/array_access.hpp"
#include "wireforge/bit_utils.hpp"
#include "wireforge/config.hpp"
#include "wireforge/span.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <type_traits>
#include <utility>

namespace wireforge {

/// @cond INTERNAL
namespace detail {

template <typename C>
using ChecksumUpdate = decltype(C::update(std::declval<typename C::State&>(),
                                          std::declval<Span<const std::uint8_t>>()));
template <typename C>
using ChecksumFinalize = decltype(C::finalize(std::declval<const typename C::State&>()));

template <typename C>
inline constexpr bool kChecksumSignatures =
    std::is_same_v<decltype(C::init()), typename C::State> &&
    std::is_same_v<ChecksumFinalize<C>, std::uint64_t> &&
    std::is_convertible_v<decltype(C::kSize), std::size_t>;

template <typename C> inline constexpr bool kChecksumSize = C::kSize >= 1U && C::kSize <= 8U;

template <typename C>
inline constexpr bool kChecksumNoexcept =
    noexcept(C::init()) &&
    noexcept(
        C::update(std::declval<typename C::State&>(), std::declval<Span<const std::uint8_t>>())) &&
    noexcept(C::finalize(std::declval<const typename C::State&>()));

} // namespace detail
/// @endcond

/// @brief Detects whether @p C satisfies the checksum requirements (see the file description).
template <typename C, typename = void> struct is_checksum : std::false_type {};

/// @cond INTERNAL
template <typename C>
struct is_checksum<C, std::void_t<typename C::State, decltype(C::kSize), decltype(C::init()),
                                  detail::ChecksumUpdate<C>, detail::ChecksumFinalize<C>>>
    : std::bool_constant<detail::kChecksumSignatures<C> && detail::kChecksumSize<C> &&
                         detail::kChecksumNoexcept<C>> {};
/// @endcond

/// @brief true when @p C is a checksum algorithm.
template <typename C> inline constexpr bool is_checksum_v = is_checksum<C>::value;

/**
 * @brief Computes a checksum over one contiguous range.
 * @tparam C Checksum algorithm.
 * @param bytes Input data.
 * @return The finalized checksum value.
 */
template <typename C>
[[nodiscard]] std::uint64_t compute_checksum(Span<const std::uint8_t> bytes) noexcept {
    static_assert(is_checksum_v<C>, "C must satisfy the checksum contract");
    typename C::State state = C::init();
    C::update(state, bytes);
    return C::finalize(state);
}

/// @cond INTERNAL
namespace detail {

inline constexpr std::uint64_t kCrcTopBit = std::uint64_t{1U} << 63U;

// Eight shift/xor steps of a left-aligned (bit 63 = top) CRC register.
[[nodiscard]] constexpr std::uint64_t crc_step_byte(std::uint64_t reg,
                                                    std::uint64_t aligned_poly) noexcept {
    for (unsigned bit = 0U; bit < 8U; ++bit) {
        reg = (reg & kCrcTopBit) != 0U ? (reg << 1U) ^ aligned_poly : reg << 1U;
    }
    return reg;
}

[[nodiscard]] constexpr std::array<std::uint64_t, 256>
make_crc_table(std::uint64_t aligned_poly) noexcept {
    std::array<std::uint64_t, 256> table{};
    std::uint64_t index = 0U;
    for (std::uint64_t& entry : table) {
        entry = crc_step_byte(index << 56U, aligned_poly);
        ++index;
    }
    return table;
}

// One read-only table per left-aligned polynomial, shared by every CRC that uses it.
template <std::uint64_t AlignedPoly>
inline constexpr std::array<std::uint64_t, 256> kCrcTable = make_crc_table(AlignedPoly);

} // namespace detail
/// @endcond

/**
 * @brief Parametric CRC in the Rocksoft model; satisfies the checksum requirements.
 *
 * The parameters are the ones listed in CRC catalogues ("width", "poly", "init", "refin",
 * "refout", "xorout"). The table mode uses a shared 2 KiB constant table per polynomial; the
 * table-less mode is slower and needs no table.
 * @tparam Width CRC width in bits, 1..64.
 * @tparam Poly Generator polynomial, normal (non-reflected) form.
 * @tparam Init Initial register value.
 * @tparam RefIn Reflect each input byte.
 * @tparam RefOut Reflect the final register.
 * @tparam XorOut Value XOR-ed into the result.
 * @tparam UseTable Use the lookup table (default from config::kCrcUseTable).
 */
template <unsigned Width, std::uint64_t Poly, std::uint64_t Init, bool RefIn, bool RefOut,
          std::uint64_t XorOut, bool UseTable = config::kCrcUseTable>
class Crc {
    static_assert(Width >= 1U && Width <= kMaxBits, "CRC width must be 1..64");
    static_assert(fits_unsigned(Poly, Width) && fits_unsigned(Init, Width) &&
                      fits_unsigned(XorOut, Width),
                  "CRC parameters must fit the width");

    static constexpr unsigned kShift = kMaxBits - Width;
    static constexpr std::uint64_t kAlignedPoly = std::uint64_t{Poly} << kShift;

public:
    /// @brief Bytes of the CRC value on the wire.
    static constexpr std::size_t kSize = (Width + 7U) / 8U;
    /// @brief CRC width in bits.
    static constexpr unsigned kWidth = Width;

    /// @brief Running state of an incremental computation.
    struct State {
        std::uint64_t reg; ///< Register, left-aligned and non-reflected.
    };

    /// @brief Starts a computation.
    [[nodiscard]] static constexpr State init() noexcept {
        return State{std::uint64_t{Init} << kShift};
    }

    /**
     * @brief Feeds @p bytes into @p state; may be called any number of times.
     */
    static constexpr void update(State& state, Span<const std::uint8_t> bytes) noexcept {
        for (const std::uint8_t byte : bytes) {
            const std::uint8_t input = RefIn ? static_cast<std::uint8_t>(reflect(byte, 8U)) : byte;
            if constexpr (UseTable) {
                const auto index = static_cast<std::uint8_t>((state.reg >> 56U) ^ input);
                state.reg = (state.reg << 8U) ^ array_at(detail::kCrcTable<kAlignedPoly>, index);
            } else {
                state.reg =
                    detail::crc_step_byte(state.reg ^ (std::uint64_t{input} << 56U), kAlignedPoly);
            }
        }
    }

    /// @brief Returns the CRC of all bytes fed into @p state.
    [[nodiscard]] static constexpr std::uint64_t finalize(const State& state) noexcept {
        const std::uint64_t value = state.reg >> kShift;
        return (RefOut ? reflect(value, Width) : value) ^ XorOut;
    }

    /// @brief Computes the CRC of @p bytes in one call.
    [[nodiscard]] static constexpr std::uint64_t compute(Span<const std::uint8_t> bytes) noexcept {
        State state = init();
        update(state, bytes);
        return finalize(state);
    }
};

/// @brief CRC-32/ISO-HDLC (the common "CRC-32"); check value 0xCBF43926.
using Crc32 = Crc<32U, 0x04C11DB7U, 0xFFFFFFFFU, true, true, 0xFFFFFFFFU>;
/// @brief CRC-16/CCITT-FALSE (CRC-16/IBM-3740); check value 0x29B1.
using Crc16CcittFalse = Crc<16U, 0x1021U, 0xFFFFU, false, false, 0x0000U>;

static_assert(is_checksum_v<Crc32>, "Crc32 must satisfy the checksum contract");

} // namespace wireforge

#endif // WIREFORGE_CHECKSUM_HPP

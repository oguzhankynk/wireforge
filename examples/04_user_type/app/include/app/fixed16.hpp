/**
 * @file fixed16.hpp
 * @brief Example opaque user type: a 16-bit value WireForge treats as 2 raw bytes.
 */
#ifndef APP_FIXED16_HPP
#define APP_FIXED16_HPP

#include <array>
#include <cstdint>
#include <wireforge/codec_support.hpp>
#include <wireforge/span.hpp>

/// @brief Example application namespace.
namespace app {

/// @brief A small opaque type; WireForge only knows it is 2 bytes on the wire (see user_types).
struct Fixed16 {
    std::int16_t raw{};
};

/// @brief Writes @p value as 2 little-endian bytes (found by ADL, like a struct's own encode()).
inline void encode(const Fixed16& value, wireforge::Encoder& encoder) noexcept {
    const auto bits = static_cast<std::uint16_t>(value.raw);
    const std::array<std::uint8_t, 2U> bytes{
        static_cast<std::uint8_t>(bits & 0xFFU),
        static_cast<std::uint8_t>((bits >> 8U) & 0xFFU),
    };
    encoder.write_bytes(wireforge::Span<const std::uint8_t>(bytes));
}

/// @brief Reads @p value from 2 little-endian bytes (found by ADL, like a struct's own decode()).
inline void decode(Fixed16& value, wireforge::Decoder& decoder) noexcept {
    std::array<std::uint8_t, 2U> bytes{};
    decoder.read_bytes(wireforge::Span<std::uint8_t>(bytes));
    const auto bits =
        static_cast<std::uint16_t>(bytes[0] | static_cast<std::uint16_t>(bytes[1] << 8U));
    value.raw = static_cast<std::int16_t>(bits);
}

} // namespace app

#endif // APP_FIXED16_HPP

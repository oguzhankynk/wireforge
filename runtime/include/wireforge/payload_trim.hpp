/**
 * @file payload_trim.hpp
 * @brief Payloads sent without their trailing zero bytes (MAVLink 2 style).
 *
 * An interface that trims payloads drops the zero bytes at the end of every encoded payload; the
 * receiver reads the missing bytes as zeros. trimmed_size() gives the size to send and
 * ZeroExtended restores the full payload before it is decoded.
 */
#ifndef WIREFORGE_PAYLOAD_TRIM_HPP
#define WIREFORGE_PAYLOAD_TRIM_HPP

#include "wireforge/array_access.hpp"
#include "wireforge/span.hpp"

#include <array>
#include <cstddef>
#include <cstdint>

namespace wireforge {

/**
 * @brief Size of @p payload without its trailing zero bytes.
 *
 * The first byte always stays, so a non-empty payload keeps at least one byte; an empty payload
 * stays empty.
 */
[[nodiscard]] constexpr std::size_t trimmed_size(Span<const std::uint8_t> payload) noexcept {
    std::size_t size = payload.size();
    while (size > 1U && payload[size - 1U] == 0U) {
        --size;
    }
    return size;
}

/**
 * @brief A payload that may have lost its trailing zero bytes, extended with zeros to @p N bytes.
 * @tparam N Size of the full payload (the wire size of the message).
 *
 * A payload shorter than @p N is copied and padded with zeros; a payload of @p N bytes or more is
 * viewed unchanged, so the decoder still sees bytes after the last field.
 */
template <std::size_t N> class ZeroExtended {
public:
    /// @brief Extends @p payload.
    explicit constexpr ZeroExtended(Span<const std::uint8_t> payload) noexcept : payload_(payload) {
        const std::size_t count = payload.size() < N ? payload.size() : N;
        for (std::size_t index = 0U; index < count; ++index) {
            array_at(bytes_, index) = payload[index];
        }
    }

    ZeroExtended(const ZeroExtended&) = delete;
    ZeroExtended& operator=(const ZeroExtended&) = delete;
    ZeroExtended(ZeroExtended&&) = delete;
    ZeroExtended& operator=(ZeroExtended&&) = delete;
    ~ZeroExtended() = default;

    /// @brief The payload to decode: the padded copy, or the payload itself when it is not shorter.
    [[nodiscard]] constexpr Span<const std::uint8_t> span() const noexcept {
        return payload_.size() < N ? Span<const std::uint8_t>(bytes_.data(), N) : payload_;
    }

private:
    Span<const std::uint8_t> payload_;
    std::array<std::uint8_t, N> bytes_{};
};

} // namespace wireforge

#endif // WIREFORGE_PAYLOAD_TRIM_HPP

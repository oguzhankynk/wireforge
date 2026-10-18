/**
 * @file crc.hpp
 * @brief The checksum of a MAVLink 1 frame, with the CRC_EXTRA byte of its message.
 */
#ifndef MAVSUM_CRC_HPP
#define MAVSUM_CRC_HPP

#include <cstddef>
#include <cstdint>
#include <wireforge/span.hpp>

/// @brief Helpers of the MAVLink protocol model.
namespace mavsum {

/**
 * @brief CRC-16/MCRF4XX over the frame without its start byte, then the CRC_EXTRA byte of the
 * message.
 *
 * The checksum field covers the whole header, so the start byte is the first byte this checksum is
 * given and it is skipped. The message id is the sixth byte; it selects the CRC_EXTRA byte that is
 * added at the end.
 */
struct Crc {
    static constexpr std::size_t kSize = 2U; ///< Bytes on the wire.

    /// @brief The running CRC and the position in the frame.
    struct State {
        std::uint16_t crc;      ///< CRC so far.
        std::uint8_t position;  ///< Bytes seen, up to the position after the message id.
        std::uint8_t messageId; ///< The message id, once seen.
    };

    /// @brief Starts a computation.
    static constexpr State init() noexcept { return State{0xFFFFU, 0U, 0U}; }

    /// @brief Adds @p bytes of the frame.
    static constexpr void update(State& state, wireforge::Span<const std::uint8_t> bytes) noexcept {
        for (const std::uint8_t byte : bytes) {
            if (state.position == kMessageIdAt) {
                state.messageId = byte;
            }
            if (state.position < kMessageIdAt + 1U) {
                ++state.position;
            }
            if (state.position > 1U) {
                state.crc = accumulate(state.crc, byte);
            }
        }
    }

    /// @brief Adds the CRC_EXTRA byte and returns the checksum.
    static constexpr std::uint64_t finalize(const State& state) noexcept {
        return accumulate(state.crc, extra(state.messageId));
    }

private:
    static constexpr std::uint8_t kMessageIdAt = 5U;

    static constexpr std::uint16_t accumulate(std::uint16_t crc, std::uint8_t byte) noexcept {
        std::uint8_t tmp = static_cast<std::uint8_t>(byte ^ (crc & 0xFFU));
        tmp = static_cast<std::uint8_t>(tmp ^ static_cast<std::uint8_t>(tmp << 4U));
        return static_cast<std::uint16_t>((crc >> 8U) ^ (static_cast<std::uint16_t>(tmp) << 8U) ^
                                          (static_cast<std::uint16_t>(tmp) << 3U) ^ (tmp >> 4U));
    }

    static constexpr std::uint8_t extra(std::uint8_t messageId) noexcept {
        switch (messageId) {
        case 0U:
            return 50U;
        case 30U:
            return 39U;
        case 33U:
            return 104U;
        case 76U:
            return 152U;
        default:
            return 0U;
        }
    }
};

} // namespace mavsum

#endif // MAVSUM_CRC_HPP

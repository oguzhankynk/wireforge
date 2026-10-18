/**
 * @file fletcher8.hpp
 * @brief The 8-bit Fletcher checksum of the UBX protocol.
 */
#ifndef UBX_FLETCHER8_HPP
#define UBX_FLETCHER8_HPP

#include <cstddef>
#include <cstdint>
#include <wireforge/span.hpp>

/// @brief Helpers of the UBX protocol model.
namespace ubxsum {

/**
 * @brief Fletcher-8 over the class, the identifier, the length and the payload of a UBX frame.
 *
 * The checksum field covers the whole header, so the two sync characters that start every frame
 * are the first two bytes this checksum is given; it skips them.
 */
struct Fletcher8 {
    static constexpr std::size_t kSize = 2U; ///< Bytes on the wire (CK_A, CK_B).

    /// @brief Running sums and the number of sync characters still to skip.
    struct State {
        std::uint8_t a;    ///< CK_A.
        std::uint8_t b;    ///< CK_B.
        std::uint8_t skip; ///< Sync characters not yet skipped.
    };

    /// @brief Starts a computation.
    static constexpr State init() noexcept { return State{0U, 0U, 2U}; }

    /// @brief Adds @p bytes to the sums.
    static constexpr void update(State& state, wireforge::Span<const std::uint8_t> bytes) noexcept {
        for (const std::uint8_t byte : bytes) {
            if (state.skip > 0U) {
                --state.skip;
                continue;
            }
            state.a = static_cast<std::uint8_t>(state.a + byte);
            state.b = static_cast<std::uint8_t>(state.b + state.a);
        }
    }

    /// @brief Returns CK_B in the high byte and CK_A in the low byte.
    static constexpr std::uint64_t finalize(const State& state) noexcept {
        return (static_cast<std::uint64_t>(state.b) << 8U) | state.a;
    }
};

} // namespace ubxsum

#endif // UBX_FLETCHER8_HPP

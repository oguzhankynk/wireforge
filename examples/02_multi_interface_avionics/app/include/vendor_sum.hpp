/**
 * @file vendor_sum.hpp
 * @brief Example custom checksum: 16-bit sum of all bytes.
 */
#ifndef VENDOR_SUM_HPP
#define VENDOR_SUM_HPP

#include <cstddef>
#include <cstdint>
#include <wireforge/span.hpp>

/// @brief Example vendor namespace.
namespace vendor {

/// @brief Sum of all bytes modulo 2^16 (matches plugins/vendor_sum.py).
struct SumChecksum {
    static constexpr std::size_t kSize = 2U; ///< Bytes on the wire.

    /// @brief Running sum.
    struct State {
        std::uint16_t sum; ///< Sum so far.
    };

    /// @brief Starts a computation.
    static constexpr State init() noexcept { return State{0U}; }

    /// @brief Adds @p bytes to the sum.
    static constexpr void update(State& state, wireforge::Span<const std::uint8_t> bytes) noexcept {
        for (const std::uint8_t byte : bytes) {
            state.sum = static_cast<std::uint16_t>(state.sum + byte);
        }
    }

    /// @brief Returns the sum.
    static constexpr std::uint64_t finalize(const State& state) noexcept { return state.sum; }
};

} // namespace vendor

#endif // VENDOR_SUM_HPP

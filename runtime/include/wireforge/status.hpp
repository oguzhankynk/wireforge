/**
 * @file status.hpp
 * @brief Status codes returned by every WireForge runtime and generated codec function.
 */
#ifndef WIREFORGE_STATUS_HPP
#define WIREFORGE_STATUS_HPP

#include <array>
#include <cstddef>
#include <cstdint>
#include <iterator>

namespace wireforge {

/**
 * @brief Result of a runtime or codec operation.
 *
 * The runtime never throws exceptions; every fallible function returns a Status that the caller
 * must inspect.
 */
enum class Status : std::uint8_t {
    Ok,               ///< The operation succeeded.
    BufferTooSmall,   ///< The output buffer cannot hold the encoded data.
    Truncated,        ///< The input ended before the data was complete.
    OutOfRange,       ///< A value violates its min/max, enum or bit-width range.
    CountExceedsMax,  ///< An element count is above the capacity (or below the minimum).
    ConstMismatch,    ///< A constant field differs from its declared value.
    ChecksumMismatch, ///< A received checksum does not match the recomputed one.
    UnknownMessageId, ///< A message id is not known on this interface.
    InvalidLength,    ///< A length field is inconsistent with the frame or buffer.
    TrailingBytes,    ///< The payload was not fully consumed.
    InvalidState,     ///< The API was misused (invalid argument or call sequence).
};

/**
 * @brief Tests a status for success.
 * @param status Status to test.
 * @return true when @p status is Status::Ok.
 */
[[nodiscard]] constexpr bool is_ok(Status status) noexcept {
    return status == Status::Ok;
}

/**
 * @brief Returns the name of a status code.
 * @param status Status to name.
 * @return A static, zero-terminated string such as "Truncated"; "Unknown" for values outside the
 *         enumeration.
 */
[[nodiscard]] constexpr const char* to_string(Status status) noexcept {
    // Indexed by the enumerator value. A lookup table instead of a switch: a switch over
    // every enumerator needs a default label that -Wcovered-switch-default rejects.
    constexpr std::array<const char*, 11U> kNames{
        "Ok",
        "BufferTooSmall",
        "Truncated",
        "OutOfRange",
        "CountExceedsMax",
        "ConstMismatch",
        "ChecksumMismatch",
        "UnknownMessageId",
        "InvalidLength",
        "TrailingBytes",
        "InvalidState",
    };
    const auto index = static_cast<std::size_t>(status);
    if (index < kNames.size()) {
        return *std::next(kNames.begin(), static_cast<std::ptrdiff_t>(index));
    }
    return "Unknown";
}

} // namespace wireforge

#endif // WIREFORGE_STATUS_HPP

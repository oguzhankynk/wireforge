/**
 * @file unhandled.hpp
 * @brief The frame handed to the generated `onUnhandled` hook and the reason it got there.
 */
#ifndef WIREFORGE_UNHANDLED_HPP
#define WIREFORGE_UNHANDLED_HPP

#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <cstdint>

namespace wireforge {

/// @brief Why a complete frame reached no received-message callback.
enum class UnhandledReason : std::uint8_t {
    UnknownMessageId, ///< The interface has no message with this id.
    NotForThisNode,   ///< The message exists but this unit does not receive it.
    DecodeError,      ///< The payload could not be decoded by the default raw handler.
};

/// @brief A valid frame that was not delivered; the data is valid only while the hook runs.
struct UnhandledFrame {
    UnhandledReason reason{UnhandledReason::UnknownMessageId}; ///< Why the frame was not delivered.
    Status status{Status::Ok};      ///< The decoder's status for DecodeError, else the cause.
    std::uint64_t message_id{0U};   ///< Message id from the header, 0 when there is none.
    Span<const std::uint8_t> frame; ///< Header, payload and trailer.
};

} // namespace wireforge

#endif // WIREFORGE_UNHANDLED_HPP

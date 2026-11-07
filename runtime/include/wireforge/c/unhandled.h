/**
 * @file unhandled.h
 * @brief The frame handed to a handler's `on_unhandled` hook and the reason it got there.
 */
#ifndef WIREFORGE_C_UNHANDLED_H
#define WIREFORGE_C_UNHANDLED_H

#include "wireforge/c/status.h"

#include <stddef.h>
#include <stdint.h>

/// Why a complete frame reached no received-message callback.
typedef uint8_t wf_unhandled_reason;

enum {
    WF_UNHANDLED_UNKNOWN_MESSAGE_ID = 0, ///< The interface has no message with this id.
    WF_UNHANDLED_NOT_FOR_THIS_NODE = 1,  ///< The message exists but this unit does not receive it.
    WF_UNHANDLED_DECODE_ERROR = 2 ///< The payload could not be decoded by the default handler.
};

/// A valid frame that was not delivered; the pointer is valid only while the hook runs.
typedef struct wf_unhandled_frame {
    wf_unhandled_reason reason; ///< Why the frame was not delivered.
    wf_status status;           ///< The decoder's status for a decode error, else the cause.
    uint64_t message_id;        ///< Message id from the header, 0 when there is none.
    const uint8_t* frame;       ///< Header, payload and trailer.
    size_t frame_size;          ///< Number of frame bytes.
} wf_unhandled_frame;

/// The callback type of a handler's `on_unhandled` member.
typedef void (*wf_unhandled_fn)(void* context, const wf_unhandled_frame* frame);

/**
 * Hands a frame to an `on_unhandled` callback; a null callback only lets the link count the frame.
 * Generated code calls this without a condition of its own.
 */
static inline void wf_report_unhandled(wf_unhandled_fn callback, void* context,
                                       wf_unhandled_reason reason, wf_status status,
                                       uint64_t message_id, const uint8_t* frame,
                                       size_t frame_size) {
    if (callback != NULL) {
        const wf_unhandled_frame unhandled = {reason, status, message_id, frame, frame_size};
        callback(context, &unhandled);
    }
}

#endif /* WIREFORGE_C_UNHANDLED_H */

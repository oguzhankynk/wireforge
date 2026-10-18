/**
 * @file status.h
 * @brief Status codes returned by every function of the C runtime and of generated C code.
 *
 * The values and the order are those of wireforge::Status of the C++ runtime. Nothing in the C
 * runtime allocates memory, uses exceptions or global state.
 */
#ifndef WIREFORGE_C_STATUS_H
#define WIREFORGE_C_STATUS_H

#include <stddef.h>
#include <stdint.h>

/// Result of an operation; one of the WF_* values.
typedef uint8_t wf_status;

enum {
    WF_OK = 0,                 ///< The operation succeeded.
    WF_BUFFER_TOO_SMALL = 1,   ///< The output buffer cannot hold the encoded data.
    WF_TRUNCATED = 2,          ///< The input ended before the data was complete.
    WF_OUT_OF_RANGE = 3,       ///< A value violates its min/max, enum or bit-width range.
    WF_COUNT_EXCEEDS_MAX = 4,  ///< An element count is above the capacity (or below the minimum).
    WF_CONST_MISMATCH = 5,     ///< A constant field differs from its declared value.
    WF_CHECKSUM_MISMATCH = 6,  ///< A received checksum does not match the recomputed one.
    WF_UNKNOWN_MESSAGE_ID = 7, ///< A message id is not known on this interface.
    WF_INVALID_LENGTH = 8,     ///< A length field is inconsistent with the frame or buffer.
    WF_TRAILING_BYTES = 9,     ///< The payload was not fully consumed.
    WF_INVALID_STATE = 10      ///< The API was misused (invalid argument or call sequence).
};

/**
 * Returns the name of a status code.
 * @param status Status to name.
 * @return A static, zero-terminated string such as "Truncated"; "Unknown" for other values.
 */
static inline const char* wf_status_name(wf_status status) {
    static const char* const names[] = {
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
    const size_t count = sizeof(names) / sizeof(names[0]);
    return (size_t)status < count ? names[status] : "Unknown";
}

#endif /* WIREFORGE_C_STATUS_H */

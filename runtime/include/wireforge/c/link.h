/**
 * @file link.h
 * @brief A byte transport, the raw message handed to handlers, and the link that reads frames from
 *        a transport and sends frames to it.
 *
 * A link belongs to one thread at a time; the application guards it when several threads send.
 */
#ifndef WIREFORGE_C_LINK_H
#define WIREFORGE_C_LINK_H

#include "wireforge/c/frame.h"
#include "wireforge/c/ring.h"
#include "wireforge/c/status.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/// A received message before decoding; the pointers are valid only while the handler runs.
typedef struct wf_raw_message {
    const uint8_t* payload; ///< Bytes between header and trailer.
    size_t payload_size;    ///< Number of payload bytes.
    const uint8_t* frame;   ///< The whole frame: header, payload and trailer.
    size_t frame_size;      ///< Number of frame bytes.
} wf_raw_message;

/// A byte driver: both functions return at once and never block.
typedef struct wf_transport {
    void* context; ///< Passed to both functions.
    /// Hands bytes to the driver; returns the number accepted (0 when it can take none now).
    size_t (*send)(void* context, const uint8_t* bytes, size_t length);
    /// Copies received bytes into @p buffer; returns the number copied (0 when nothing arrived).
    size_t (*receive)(void* context, uint8_t* buffer, size_t capacity);
} wf_transport;

/// Passes a complete frame to the handlers of an interface; returns their result.
typedef wf_status (*wf_dispatch_fn)(const void* context, const uint8_t* frame, size_t size);

/// What one poll found.
typedef struct wf_poll_result {
    size_t frames;        ///< Frames dispatched successfully.
    size_t errors;        ///< Frames rejected by the decoder or the dispatcher.
    wf_status last_error; ///< The most recent error, WF_OK when none.
} wf_poll_result;

/// Statistics of a link.
typedef struct wf_link_counters {
    size_t rx_bytes;  ///< Bytes received from the transport.
    size_t tx_bytes;  ///< Bytes accepted by the transport.
    size_t tx_frames; ///< Frames sent completely.
    size_t tx_errors; ///< Frames not sent (encoder error or transport full).
} wf_link_counters;

#define WF_LINK_CHUNK 64U

/// The state of a link; the buffers are provided by the caller (generated code declares them).
typedef struct wf_link {
    const wf_transport* transport; ///< Where bytes come from and go to.
    bool framed;                   ///< false: each transport read is one frame.
    wf_frame_decoder decoder;      ///< Frame decoder (framed links).
    wf_ring rx_ring;               ///< Received bytes not yet framed.
    uint8_t* rx_frame;             ///< Buffer for one received frame.
    uint8_t* tx_frame;             ///< Buffer for one frame to send.
    size_t frame_capacity;         ///< Size of both frame buffers.
    wf_dispatch_fn dispatch;       ///< Receives every complete frame.
    const void* dispatch_context;  ///< Passed to @p dispatch.
    wf_link_counters counters;     ///< Statistics.
} wf_link;

/// Sets up a link; the buffers must outlive it and the ring storage holds a whole frame at least.
static inline void wf_link_init(wf_link* link, const wf_transport* transport, bool framed,
                                const wf_frame_decoder* decoder, uint8_t* rx_storage,
                                size_t rx_capacity, uint8_t* rx_frame, uint8_t* tx_frame,
                                size_t frame_capacity, wf_dispatch_fn dispatch,
                                const void* dispatch_context) {
    link->transport = transport;
    link->framed = framed;
    link->decoder = *decoder;
    wf_ring_init(&link->rx_ring, rx_storage, rx_capacity);
    link->rx_frame = rx_frame;
    link->tx_frame = tx_frame;
    link->frame_capacity = frame_capacity;
    link->dispatch = dispatch;
    link->dispatch_context = dispatch_context;
    link->counters.rx_bytes = 0U;
    link->counters.tx_bytes = 0U;
    link->counters.tx_frames = 0U;
    link->counters.tx_errors = 0U;
}

static inline void wf_link_record(wf_poll_result* result, wf_status status) {
    if (status == WF_OK) {
        ++result->frames;
    } else {
        ++result->errors;
        result->last_error = status;
    }
}

/* Drops what the transport still holds after a read that filled the frame buffer. */
static inline size_t wf_link_discard_excess(wf_link* link) {
    uint8_t chunk[WF_LINK_CHUNK];
    size_t total = 0U;
    for (;;) {
        const size_t received =
            link->transport->receive(link->transport->context, chunk, sizeof(chunk));
        if (received == 0U) {
            return total;
        }
        link->counters.rx_bytes += received;
        total += received;
    }
}

/* Moves bytes from the transport into the ring until either is exhausted. */
static inline size_t wf_link_fill_ring(wf_link* link) {
    uint8_t chunk[WF_LINK_CHUNK];
    size_t total = 0U;
    while (wf_ring_free_space(&link->rx_ring) > 0U) {
        const size_t space = wf_ring_free_space(&link->rx_ring);
        const size_t wanted = space < sizeof(chunk) ? space : sizeof(chunk);
        const size_t received = link->transport->receive(link->transport->context, chunk, wanted);
        if (received == 0U) {
            break;
        }
        link->counters.rx_bytes += received;
        (void)wf_ring_write(&link->rx_ring, chunk, received);
        total += received;
    }
    return total;
}

static inline void wf_link_drain_ring(wf_link* link, wf_poll_result* result) {
    for (;;) {
        size_t size = 0U;
        const wf_status status = wf_frame_decoder_next(&link->decoder, &link->rx_ring,
                                                       link->rx_frame, link->frame_capacity, &size);
        if (status == WF_TRUNCATED) {
            return;
        }
        if (status != WF_OK) {
            wf_link_record(result, status);
            continue;
        }
        wf_link_record(result, link->dispatch(link->dispatch_context, link->rx_frame, size));
    }
}

/**
 * Reads everything the transport has and dispatches every complete frame.
 *
 * Call regularly. For an unframed link each non-empty transport read is dispatched as one frame;
 * a read with more bytes than a frame buffer holds is rejected as WF_INVALID_LENGTH and its bytes
 * are dropped.
 */
static inline wf_poll_result wf_link_poll(wf_link* link) {
    wf_poll_result result = {0U, 0U, WF_OK};
    if (!link->framed) {
        const size_t received = link->transport->receive(link->transport->context, link->rx_frame,
                                                         link->frame_capacity);
        link->counters.rx_bytes += received;
        if (received == link->frame_capacity && wf_link_discard_excess(link) > 0U) {
            wf_link_record(&result, WF_INVALID_LENGTH);
        } else if (received > 0U) {
            wf_link_record(&result,
                           link->dispatch(link->dispatch_context, link->rx_frame, received));
        }
        return result;
    }
    for (;;) {
        const size_t received = wf_link_fill_ring(link);
        wf_link_drain_ring(link, &result);
        if (received == 0U) {
            break;
        }
    }
    return result;
}

/// Sends an already encoded frame; WF_BUFFER_TOO_SMALL when the transport did not accept it all.
static inline wf_status wf_link_send_frame(wf_link* link, const uint8_t* frame, size_t size) {
    size_t sent = 0U;
    while (sent < size) {
        const size_t accepted =
            link->transport->send(link->transport->context, &frame[sent], size - sent);
        if (accepted == 0U) {
            break;
        }
        sent += accepted;
    }
    link->counters.tx_bytes += sent;
    if (sent != size) {
        ++link->counters.tx_errors;
        return WF_BUFFER_TOO_SMALL;
    }
    ++link->counters.tx_frames;
    return WF_OK;
}

/**
 * Sends the frame that an encoder wrote into the transmit buffer.
 * @param status The result of the encoder; its error is returned.
 * @param size Size of the frame in the transmit buffer.
 */
static inline wf_status wf_link_send_encoded(wf_link* link, wf_status status, size_t size) {
    if (status != WF_OK || size > link->frame_capacity) {
        ++link->counters.tx_errors;
        return status != WF_OK ? status : WF_BUFFER_TOO_SMALL;
    }
    return wf_link_send_frame(link, link->tx_frame, size);
}

#endif /* WIREFORGE_C_LINK_H */

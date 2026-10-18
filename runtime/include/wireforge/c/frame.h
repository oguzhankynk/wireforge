/**
 * @file frame.h
 * @brief Finds the frames in a byte ring: sync and length, fixed size and length-prefixed framing,
 *        an optional checksum and resynchronisation.
 */
#ifndef WIREFORGE_C_FRAME_H
#define WIREFORGE_C_FRAME_H

#include "wireforge/c/bytes.h"
#include "wireforge/c/codec.h"
#include "wireforge/c/ring.h"
#include "wireforge/c/status.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/// How frame boundaries are found in the byte stream.
typedef enum {
    WF_FRAMING_FIXED = 0,          ///< Every frame has the same size.
    WF_FRAMING_SYNC_LENGTH = 1,    ///< A sync field marks the frame start; a length gives its size.
    WF_FRAMING_LENGTH_PREFIXED = 2 ///< A length field at the frame start gives its size.
} wf_framing_kind;

/// What the length field of a frame counts.
typedef enum {
    WF_LENGTH_PAYLOAD = 0,             ///< Payload bytes only.
    WF_LENGTH_PAYLOAD_AND_TRAILER = 1, ///< Payload and trailer bytes.
    WF_LENGTH_FRAME = 2                ///< The whole frame, header and trailer included.
} wf_length_counts;

/// Which bytes a frame checksum covers.
typedef enum {
    WF_COVERS_HEADER_AND_PAYLOAD = 0, ///< From the first header byte to the end of the payload.
    WF_COVERS_PAYLOAD = 1             ///< The payload only.
} wf_checksum_coverage;

/// Position of a header or trailer field; a width of 0 means "no such field".
typedef struct wf_field_location {
    size_t offset; ///< Byte offset inside the header (or trailer).
    size_t width;  ///< Width in bytes, 0..8.
} wf_field_location;

/// Layout of the frames of one interface (see wireforge/frame_decoder.hpp).
typedef struct wf_frame_format {
    wf_framing_kind kind;                 ///< Framing method.
    wf_endian endian;                     ///< Byte order of the sync, length and checksum fields.
    size_t header_size;                   ///< Header bytes.
    size_t trailer_size;                  ///< Trailer bytes.
    size_t max_frame_size;                ///< Largest frame; the frame size for fixed framing.
    wf_field_location sync;               ///< Sync field; required for sync-and-length framing.
    uint64_t sync_value;                  ///< Expected value of the sync field.
    wf_field_location length;             ///< Length field; required unless the framing is fixed.
    wf_length_counts length_counts;       ///< Meaning of the length field.
    size_t checksum_offset;               ///< Offset of the checksum inside the trailer.
    wf_checksum_coverage checksum_covers; ///< Bytes covered by the checksum.
    bool resync; ///< On errors skip one byte and rescan; otherwise drop all buffered bytes.
} wf_frame_format;

/// Statistics of a frame decoder.
typedef struct wf_frame_counters {
    size_t frames;          ///< Frames delivered.
    size_t discarded_bytes; ///< Bytes dropped while searching for a frame start.
    size_t length_errors;   ///< Frames rejected because of their length field.
    size_t checksum_errors; ///< Frames rejected because of their checksum.
    size_t overflow_errors; ///< Frames that did not fit the output buffer.
} wf_frame_counters;

/// Finds frames in a byte ring; one decoder per ring.
typedef struct wf_frame_decoder {
    wf_frame_format format;     ///< The layout of the frames.
    wf_checksum_fn checksum;    ///< Checksum of the trailer, or NULL when frames carry none.
    size_t checksum_size;       ///< Bytes of the checksum (0 without one).
    wf_frame_counters counters; ///< Statistics.
} wf_frame_decoder;

/// Creates a decoder for @p format; @p checksum is NULL for frames without a checksum.
static inline void wf_frame_decoder_init(wf_frame_decoder* decoder, const wf_frame_format* format,
                                         wf_checksum_fn checksum, size_t checksum_size) {
    decoder->format = *format;
    decoder->checksum = checksum;
    decoder->checksum_size = checksum == NULL ? 0U : checksum_size;
    decoder->counters.frames = 0U;
    decoder->counters.discarded_bytes = 0U;
    decoder->counters.length_errors = 0U;
    decoder->counters.checksum_errors = 0U;
    decoder->counters.overflow_errors = 0U;
}

static inline bool wf_frame_field_ok(const wf_frame_format* format, wf_field_location field) {
    return field.width == 0U ||
           (wf_valid_width(field.width) && field.offset <= format->header_size &&
            format->header_size - field.offset >= field.width);
}

/// true when the format is consistent (field positions, sizes, checksum placement).
static inline bool wf_frame_decoder_valid(const wf_frame_decoder* decoder) {
    const wf_frame_format* format = &decoder->format;
    const size_t overhead = format->header_size + format->trailer_size;
    const bool sized = format->max_frame_size >= overhead && format->max_frame_size > 0U;
    const bool sync_ok = wf_frame_field_ok(format, format->sync) &&
                         (format->kind != WF_FRAMING_SYNC_LENGTH || format->sync.width != 0U);
    const bool length_ok = wf_frame_field_ok(format, format->length) &&
                           (format->kind == WF_FRAMING_FIXED || format->length.width != 0U);
    const bool checksum_ok =
        decoder->checksum == NULL ||
        (format->checksum_offset <= format->trailer_size &&
         format->trailer_size - format->checksum_offset >= decoder->checksum_size);
    return sized && sync_ok && length_ok && checksum_ok;
}

static inline uint64_t wf_frame_peek_field(const wf_frame_decoder* decoder, const wf_ring* ring,
                                           wf_field_location field) {
    uint8_t bytes[WF_MAX_WIDTH] = {0};
    (void)wf_ring_peek(ring, bytes, field.width, field.offset);
    return wf_load_unsigned(bytes, field.width, decoder->format.endian);
}

static inline bool wf_frame_sync_matches(const wf_frame_decoder* decoder, const wf_ring* ring) {
    if (decoder->format.sync.width == 0U) {
        return true;
    }
    return wf_frame_peek_field(decoder, ring, decoder->format.sync) == decoder->format.sync_value;
}

static inline wf_status wf_frame_length(const wf_frame_decoder* decoder, const wf_ring* ring,
                                        size_t* total) {
    const wf_frame_format* format = &decoder->format;
    const size_t overhead = format->header_size + format->trailer_size;
    if (format->kind == WF_FRAMING_FIXED) {
        *total = format->max_frame_size;
        return WF_OK;
    }
    const uint64_t length = wf_frame_peek_field(decoder, ring, format->length);
    if (length > format->max_frame_size) {
        return WF_INVALID_LENGTH;
    }
    const size_t value = (size_t)length;
    if (format->length_counts == WF_LENGTH_PAYLOAD) {
        *total = overhead + value;
    } else if (format->length_counts == WF_LENGTH_PAYLOAD_AND_TRAILER) {
        *total = format->header_size + value;
    } else {
        *total = value;
    }
    return (*total >= overhead && *total <= format->max_frame_size) ? WF_OK : WF_INVALID_LENGTH;
}

static inline bool wf_frame_checksum_matches(const wf_frame_decoder* decoder, const uint8_t* frame,
                                             size_t size) {
    if (decoder->checksum == NULL) {
        return true;
    }
    const wf_frame_format* format = &decoder->format;
    const size_t payload_end = size - format->trailer_size;
    const size_t start = format->checksum_covers == WF_COVERS_PAYLOAD ? format->header_size : 0U;
    const uint64_t expected = decoder->checksum(&frame[start], payload_end - start);
    const uint64_t received = wf_load_unsigned(&frame[payload_end + format->checksum_offset],
                                               decoder->checksum_size, format->endian);
    return expected == received;
}

static inline wf_status wf_frame_reject(wf_frame_decoder* decoder, wf_ring* ring,
                                        wf_status status) {
    const size_t dropped =
        decoder->format.resync ? wf_ring_consume(ring, 1U) : wf_ring_consume(ring, ring->size);
    decoder->counters.discarded_bytes += dropped;
    return status;
}

/**
 * Extracts the next frame from @p ring.
 * @param out Destination of the frame (header, payload and trailer).
 * @param[out] frame_size Size of the delivered frame; 0 unless WF_OK is returned.
 * @return WF_OK when a frame was copied to @p out; WF_TRUNCATED when more bytes are needed;
 *         WF_INVALID_LENGTH, WF_CHECKSUM_MISMATCH or WF_BUFFER_TOO_SMALL when a frame was rejected
 *         (call again to continue); WF_INVALID_STATE when the format is not valid.
 */
static inline wf_status wf_frame_decoder_next(wf_frame_decoder* decoder, wf_ring* ring,
                                              uint8_t* out, size_t out_capacity,
                                              size_t* frame_size) {
    *frame_size = 0U;
    if (!wf_frame_decoder_valid(decoder)) {
        return WF_INVALID_STATE;
    }
    for (;;) {
        if (ring->size < decoder->format.header_size || ring->size == 0U) {
            return WF_TRUNCATED;
        }
        if (!wf_frame_sync_matches(decoder, ring)) {
            decoder->counters.discarded_bytes += wf_ring_consume(ring, 1U);
            continue;
        }
        size_t total = 0U;
        wf_status status = wf_frame_length(decoder, ring, &total);
        if (status == WF_OK && ring->capacity < total) {
            status = WF_INVALID_LENGTH;
        }
        if (status != WF_OK) {
            ++decoder->counters.length_errors;
            return wf_frame_reject(decoder, ring, status);
        }
        if (ring->size < total) {
            return WF_TRUNCATED;
        }
        if (out_capacity < total) {
            ++decoder->counters.overflow_errors;
            return wf_frame_reject(decoder, ring, WF_BUFFER_TOO_SMALL);
        }
        (void)wf_ring_peek(ring, out, total, 0U);
        if (!wf_frame_checksum_matches(decoder, out, total)) {
            ++decoder->counters.checksum_errors;
            return wf_frame_reject(decoder, ring, WF_CHECKSUM_MISMATCH);
        }
        (void)wf_ring_consume(ring, total);
        ++decoder->counters.frames;
        *frame_size = total;
        return WF_OK;
    }
}

#endif /* WIREFORGE_C_FRAME_H */

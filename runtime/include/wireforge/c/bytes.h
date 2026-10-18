/**
 * @file bytes.h
 * @brief Host-independent reading and writing of integers, floats and bit groups.
 */
#ifndef WIREFORGE_C_BYTES_H
#define WIREFORGE_C_BYTES_H

#include "wireforge/c/bits.h"
#include "wireforge/c/status.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

/// Byte order of multi-byte values on the wire.
typedef enum {
    WF_BIG_ENDIAN = 0,   ///< Most significant byte first.
    WF_LITTLE_ENDIAN = 1 ///< Least significant byte first.
} wf_endian;

/* f32 and f64 are IEEE-754 binary32 and binary64 (a negative array size is a compile error). */
typedef char wf_float_is_32_bits[sizeof(float) == 4U ? 1 : -1];
typedef char wf_double_is_64_bits[sizeof(double) == 8U ? 1 : -1];

/// Largest integer width in bytes.
#define WF_MAX_WIDTH 8U

/// true when @p width is a supported integer width (1..8 bytes).
static inline bool wf_valid_width(size_t width) {
    return width >= 1U && width <= WF_MAX_WIDTH;
}

/// Number of bits in @p width bytes.
static inline unsigned wf_width_bits(size_t width) {
    return (unsigned)(width * 8U);
}

/**
 * Stores the low @p width bytes of @p value at the start of @p out. Uses shifts only, so the
 * result never depends on the host byte order.
 */
static inline void wf_store_unsigned(uint8_t* out, uint64_t value, size_t width, wf_endian endian) {
    for (size_t index = 0U; index < width; ++index) {
        const size_t byte_index = endian == WF_BIG_ENDIAN ? width - 1U - index : index;
        out[byte_index] = (uint8_t)((value >> (8U * index)) & 0xFFU);
    }
}

/// Loads an unsigned integer of @p width bytes from the start of @p input.
static inline uint64_t wf_load_unsigned(const uint8_t* input, size_t width, wf_endian endian) {
    uint64_t value = 0U;
    for (size_t index = 0U; index < width; ++index) {
        const size_t byte_index = endian == WF_BIG_ENDIAN ? index : width - 1U - index;
        value = (value << 8U) | input[byte_index];
    }
    return value;
}

/// Sequential writer over a caller-provided buffer.
typedef struct {
    uint8_t* buffer; ///< Destination.
    size_t capacity; ///< Size of the buffer.
    size_t position; ///< Bytes written so far.
} wf_writer;

/// Writes into @p buffer of @p capacity bytes, starting at its first byte.
static inline void wf_writer_init(wf_writer* writer, uint8_t* buffer, size_t capacity) {
    writer->buffer = buffer;
    writer->capacity = capacity;
    writer->position = 0U;
}

/// Bytes still free.
static inline size_t wf_writer_remaining(const wf_writer* writer) {
    return writer->capacity - writer->position;
}

/**
 * Writes an unsigned integer.
 * @return WF_OUT_OF_RANGE when @p value does not fit, WF_BUFFER_TOO_SMALL when the buffer is full,
 *         WF_INVALID_STATE for an unsupported @p width. A failing write changes nothing.
 */
static inline wf_status wf_write_unsigned(wf_writer* writer, uint64_t value, size_t width,
                                          wf_endian endian) {
    if (!wf_valid_width(width)) {
        return WF_INVALID_STATE;
    }
    if (!wf_fits_unsigned(value, wf_width_bits(width))) {
        return WF_OUT_OF_RANGE;
    }
    if (wf_writer_remaining(writer) < width) {
        return WF_BUFFER_TOO_SMALL;
    }
    wf_store_unsigned(&writer->buffer[writer->position], value, width, endian);
    writer->position += width;
    return WF_OK;
}

/// Writes a two's complement integer; errors as wf_write_unsigned().
static inline wf_status wf_write_signed(wf_writer* writer, int64_t value, size_t width,
                                        wf_endian endian) {
    if (!wf_valid_width(width)) {
        return WF_INVALID_STATE;
    }
    if (!wf_fits_signed(value, wf_width_bits(width))) {
        return WF_OUT_OF_RANGE;
    }
    return wf_write_unsigned(writer, wf_to_twos_complement(value, wf_width_bits(width)), width,
                             endian);
}

/// Overwrites @p width bytes at @p offset inside the already written region.
static inline wf_status wf_write_unsigned_at(const wf_writer* writer, size_t offset, uint64_t value,
                                             size_t width, wf_endian endian) {
    if (!wf_valid_width(width)) {
        return WF_INVALID_STATE;
    }
    if (!wf_fits_unsigned(value, wf_width_bits(width))) {
        return WF_OUT_OF_RANGE;
    }
    if (offset > writer->position || writer->position - offset < width) {
        return WF_INVALID_LENGTH;
    }
    wf_store_unsigned(&writer->buffer[offset], value, width, endian);
    return WF_OK;
}

/// Writes an IEEE-754 binary32 value through its bit pattern.
static inline wf_status wf_write_f32(wf_writer* writer, float value, wf_endian endian) {
    uint32_t bits = 0U;
    memcpy(&bits, &value, sizeof(bits));
    return wf_write_unsigned(writer, bits, sizeof(bits), endian);
}

/// Writes an IEEE-754 binary64 value through its bit pattern.
static inline wf_status wf_write_f64(wf_writer* writer, double value, wf_endian endian) {
    uint64_t bits = 0U;
    memcpy(&bits, &value, sizeof(bits));
    return wf_write_unsigned(writer, bits, sizeof(bits), endian);
}

/// Copies @p length bytes; WF_BUFFER_TOO_SMALL when they do not fit.
static inline wf_status wf_write_bytes(wf_writer* writer, const uint8_t* bytes, size_t length) {
    if (wf_writer_remaining(writer) < length) {
        return WF_BUFFER_TOO_SMALL;
    }
    for (size_t index = 0U; index < length; ++index) {
        writer->buffer[writer->position + index] = bytes[index];
    }
    writer->position += length;
    return WF_OK;
}

/// Writes the container of a complete bit group.
static inline wf_status wf_write_bits(wf_writer* writer, const wf_bit_packer* packer,
                                      wf_endian endian) {
    if (!wf_packer_complete(packer)) {
        return WF_INVALID_STATE;
    }
    return wf_write_unsigned(writer, packer->container, packer->total_bits / 8U, endian);
}

/// Sequential reader over a caller-provided buffer.
typedef struct {
    const uint8_t* buffer; ///< Source.
    size_t size;           ///< Size of the buffer.
    size_t position;       ///< Bytes consumed so far.
} wf_reader;

/// Reads from @p buffer of @p size bytes, starting at its first byte.
static inline void wf_reader_init(wf_reader* reader, const uint8_t* buffer, size_t size) {
    reader->buffer = buffer;
    reader->size = size;
    reader->position = 0U;
}

/// Bytes not consumed yet.
static inline size_t wf_reader_remaining(const wf_reader* reader) {
    return reader->size - reader->position;
}

/**
 * Reads an unsigned integer.
 * @param[out] value Result, zero-extended; unchanged on failure.
 * @return WF_TRUNCATED when fewer than @p width bytes remain, WF_INVALID_STATE for an unsupported
 *         @p width.
 */
static inline wf_status wf_read_unsigned(wf_reader* reader, uint64_t* value, size_t width,
                                         wf_endian endian) {
    if (!wf_valid_width(width)) {
        return WF_INVALID_STATE;
    }
    if (wf_reader_remaining(reader) < width) {
        return WF_TRUNCATED;
    }
    *value = wf_load_unsigned(&reader->buffer[reader->position], width, endian);
    reader->position += width;
    return WF_OK;
}

/// Reads a two's complement integer and sign-extends it; errors as wf_read_unsigned().
static inline wf_status wf_read_signed(wf_reader* reader, int64_t* value, size_t width,
                                       wf_endian endian) {
    uint64_t raw = 0U;
    const wf_status status = wf_read_unsigned(reader, &raw, width, endian);
    if (status == WF_OK) {
        *value = wf_sign_extend(raw, wf_width_bits(width));
    }
    return status;
}

/// Reads an IEEE-754 binary32 value from its bit pattern.
static inline wf_status wf_read_f32(wf_reader* reader, float* value, wf_endian endian) {
    uint64_t raw = 0U;
    const wf_status status = wf_read_unsigned(reader, &raw, sizeof(float), endian);
    if (status == WF_OK) {
        const uint32_t bits = (uint32_t)raw;
        memcpy(value, &bits, sizeof(*value));
    }
    return status;
}

/// Reads an IEEE-754 binary64 value from its bit pattern.
static inline wf_status wf_read_f64(wf_reader* reader, double* value, wf_endian endian) {
    uint64_t bits = 0U;
    const wf_status status = wf_read_unsigned(reader, &bits, sizeof(double), endian);
    if (status == WF_OK) {
        memcpy(value, &bits, sizeof(*value));
    }
    return status;
}

/// Copies @p length bytes into @p out; WF_TRUNCATED when not enough bytes remain.
static inline wf_status wf_read_bytes(wf_reader* reader, uint8_t* out, size_t length) {
    if (wf_reader_remaining(reader) < length) {
        return WF_TRUNCATED;
    }
    for (size_t index = 0U; index < length; ++index) {
        out[index] = reader->buffer[reader->position + index];
    }
    reader->position += length;
    return WF_OK;
}

/// Skips @p count bytes; WF_TRUNCATED when not enough bytes remain.
static inline wf_status wf_read_skip(wf_reader* reader, size_t count) {
    if (wf_reader_remaining(reader) < count) {
        return WF_TRUNCATED;
    }
    reader->position += count;
    return WF_OK;
}

/// Reads the container of a bit group and prepares an unpacker for its fields.
static inline wf_status wf_read_bits(wf_reader* reader, wf_bit_unpacker* unpacker,
                                     unsigned total_bits, wf_bit_order order, wf_endian endian) {
    if (!wf_group_width_valid(total_bits)) {
        return WF_INVALID_STATE;
    }
    uint64_t container = 0U;
    const wf_status status = wf_read_unsigned(reader, &container, total_bits / 8U, endian);
    if (status == WF_OK) {
        wf_unpacker_init(unpacker, container, total_bits, order);
    }
    return status;
}

#endif /* WIREFORGE_C_BYTES_H */

/**
 * @file codec.h
 * @brief Encoder and decoder used by generated serialize/deserialize functions.
 *
 * Both keep the first error ("sticky" status): after a failure every further operation does
 * nothing, so generated code is a straight sequence of calls followed by a look at the status.
 */
#ifndef WIREFORGE_C_CODEC_H
#define WIREFORGE_C_CODEC_H

#include "wireforge/c/bits.h"
#include "wireforge/c/bytes.h"
#include "wireforge/c/status.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/// Encoding settings of one message on one interface.
typedef struct wf_encoding {
    wf_endian endian;          ///< Byte order of multi-byte values.
    wf_bit_order bit_order;    ///< Placement of fields in bit groups.
    bool check_encode;         ///< Check min/max and enum values when encoding.
    bool check_decode;         ///< Check min/max and enum values when decoding.
    bool allow_trailing_bytes; ///< Accept payload bytes after the last field.
} wf_encoding;

/// A checksum over a contiguous range; the value is as wide as the field that carries it.
typedef uint64_t (*wf_checksum_fn)(const uint8_t* data, size_t length);

/* ------------------------------------------------------------------------------------------- */
/* Encoder                                                                                      */
/* ------------------------------------------------------------------------------------------- */

/// Sequential encoder with a sticky status.
typedef struct wf_encoder {
    wf_writer writer;     ///< Destination.
    wf_encoding encoding; ///< Settings in use.
    wf_status status;     ///< The first error, or WF_OK.
} wf_encoder;

/// Encodes into @p buffer of @p capacity bytes with @p encoding.
static inline void wf_encoder_init(wf_encoder* encoder, uint8_t* buffer, size_t capacity,
                                   const wf_encoding* encoding) {
    wf_writer_init(&encoder->writer, buffer, capacity);
    encoder->encoding = *encoding;
    encoder->status = WF_OK;
}

/// Bytes written so far.
static inline size_t wf_encoder_position(const wf_encoder* encoder) {
    return encoder->writer.position;
}

/// Bytes written when the encoding succeeded, 0 after an error.
static inline size_t wf_encoder_size(const wf_encoder* encoder) {
    return encoder->status == WF_OK ? encoder->writer.position : 0U;
}

/// Records @p status unless an earlier error is already recorded.
static inline void wf_enc_check(wf_encoder* encoder, wf_status status) {
    if (encoder->status == WF_OK) {
        encoder->status = status;
    }
}

/// Records @p status when encode-side value checks are enabled.
static inline void wf_enc_check_value(wf_encoder* encoder, wf_status status) {
    if (encoder->encoding.check_encode) {
        wf_enc_check(encoder, status);
    }
}

/// Writes an unsigned integer (also bool, char and enums) of @p width bytes.
static inline void wf_enc_unsigned(wf_encoder* encoder, uint64_t value, size_t width) {
    if (encoder->status == WF_OK) {
        wf_enc_check(encoder,
                     wf_write_unsigned(&encoder->writer, value, width, encoder->encoding.endian));
    }
}

/// Writes a signed integer (also signed enums) of @p width bytes.
static inline void wf_enc_signed(wf_encoder* encoder, int64_t value, size_t width) {
    if (encoder->status == WF_OK) {
        wf_enc_check(encoder,
                     wf_write_signed(&encoder->writer, value, width, encoder->encoding.endian));
    }
}

/// Writes a bool as one byte, 0 or 1.
static inline void wf_enc_bool(wf_encoder* encoder, bool value) {
    wf_enc_unsigned(encoder, value ? 1U : 0U, 1U);
}

/// Writes an IEEE-754 binary32 value.
static inline void wf_enc_f32(wf_encoder* encoder, float value) {
    if (encoder->status == WF_OK) {
        wf_enc_check(encoder, wf_write_f32(&encoder->writer, value, encoder->encoding.endian));
    }
}

/// Writes an IEEE-754 binary64 value.
static inline void wf_enc_f64(wf_encoder* encoder, double value) {
    if (encoder->status == WF_OK) {
        wf_enc_check(encoder, wf_write_f64(&encoder->writer, value, encoder->encoding.endian));
    }
}

/**
 * Writes an element count as an unsigned integer of @p width bytes.
 * @param limit Largest count the wire type holds (1 for a bool count).
 */
static inline void wf_enc_count(wf_encoder* encoder, size_t count, uint64_t limit, size_t width) {
    if (count > limit) {
        wf_enc_check(encoder, WF_OUT_OF_RANGE);
        return;
    }
    wf_enc_unsigned(encoder, (uint64_t)count, width);
}

/// Writes the first @p length characters of @p text and pads with zeros to @p width.
static inline void wf_enc_text(wf_encoder* encoder, const char* text, size_t length, size_t width) {
    for (size_t index = 0U; index < width; ++index) {
        wf_enc_unsigned(encoder, index < length ? (uint64_t)(uint8_t)text[index] : 0U, 1U);
    }
}

/// Adds an unsigned field (bool, enum, integer) to a bit group.
static inline void wf_enc_put(wf_encoder* encoder, wf_bit_packer* group, uint64_t value,
                              unsigned bits) {
    wf_enc_check(encoder, wf_packer_put(group, value, bits));
}

/// Adds a signed field to a bit group.
static inline void wf_enc_put_signed(wf_encoder* encoder, wf_bit_packer* group, int64_t value,
                                     unsigned bits) {
    wf_enc_check(encoder, wf_packer_put_signed(group, value, bits));
}

/// Starts a bit group of @p total_bits bits.
static inline void wf_enc_group(const wf_encoder* encoder, wf_bit_packer* group,
                                unsigned total_bits) {
    wf_packer_init(group, total_bits, encoder->encoding.bit_order);
}

/// Writes a complete bit group.
static inline void wf_enc_write_bits(wf_encoder* encoder, const wf_bit_packer* group) {
    if (encoder->status == WF_OK) {
        wf_enc_check(encoder, wf_write_bits(&encoder->writer, group, encoder->encoding.endian));
    }
}

/// Copies @p length bytes onto the wire unchanged (no endian or bit-order transform).
static inline void wf_enc_bytes(wf_encoder* encoder, const uint8_t* bytes, size_t length) {
    if (encoder->status == WF_OK) {
        wf_enc_check(encoder, wf_write_bytes(&encoder->writer, bytes, length));
    }
}

/// Writes the checksum @p fn (@p size bytes wide) of the bytes written in [@p begin, @p end).
static inline void wf_enc_checksum(wf_encoder* encoder, size_t begin, size_t end, wf_checksum_fn fn,
                                   size_t size) {
    if (encoder->status != WF_OK) {
        return;
    }
    wf_enc_check(encoder, wf_write_unsigned(&encoder->writer,
                                            fn(&encoder->writer.buffer[begin], end - begin), size,
                                            encoder->encoding.endian));
}

/* ------------------------------------------------------------------------------------------- */
/* Decoder                                                                                      */
/* ------------------------------------------------------------------------------------------- */

/// Sequential decoder with a sticky status.
typedef struct wf_decoder {
    wf_reader reader;     ///< Source.
    wf_encoding encoding; ///< Settings in use.
    wf_status status;     ///< The first error, or WF_OK.
} wf_decoder;

/// Decodes @p input of @p size bytes with @p encoding.
static inline void wf_decoder_init(wf_decoder* decoder, const uint8_t* input, size_t size,
                                   const wf_encoding* encoding) {
    wf_reader_init(&decoder->reader, input, size);
    decoder->encoding = *encoding;
    decoder->status = WF_OK;
}

/// Bytes consumed so far.
static inline size_t wf_decoder_position(const wf_decoder* decoder) {
    return decoder->reader.position;
}

/// Bytes not consumed yet.
static inline size_t wf_decoder_remaining(const wf_decoder* decoder) {
    return wf_reader_remaining(&decoder->reader);
}

/// Records @p status unless an earlier error is already recorded.
static inline void wf_dec_check(wf_decoder* decoder, wf_status status) {
    if (decoder->status == WF_OK) {
        decoder->status = status;
    }
}

/// Records @p status when decode-side value checks are enabled.
static inline void wf_dec_check_value(wf_decoder* decoder, wf_status status) {
    if (decoder->encoding.check_decode) {
        wf_dec_check(decoder, status);
    }
}

/// Reads an unsigned integer of @p width bytes (0 after an error).
static inline uint64_t wf_dec_unsigned(wf_decoder* decoder, size_t width) {
    uint64_t value = 0U;
    if (decoder->status == WF_OK) {
        wf_dec_check(decoder,
                     wf_read_unsigned(&decoder->reader, &value, width, decoder->encoding.endian));
    }
    return value;
}

/// Reads a signed integer of @p width bytes (0 after an error).
static inline int64_t wf_dec_signed(wf_decoder* decoder, size_t width) {
    int64_t value = 0;
    if (decoder->status == WF_OK) {
        wf_dec_check(decoder,
                     wf_read_signed(&decoder->reader, &value, width, decoder->encoding.endian));
    }
    return value;
}

/// Reads a bool: one byte that must be 0 or 1 (WF_OUT_OF_RANGE otherwise).
static inline bool wf_dec_bool(wf_decoder* decoder) {
    const uint64_t wire = wf_dec_unsigned(decoder, 1U);
    if (wire > 1U) {
        wf_dec_check(decoder, WF_OUT_OF_RANGE);
    }
    return wire == 1U;
}

/// Reads an IEEE-754 binary32 value (0 after an error).
static inline float wf_dec_f32(wf_decoder* decoder) {
    float value = 0.0F;
    if (decoder->status == WF_OK) {
        wf_dec_check(decoder, wf_read_f32(&decoder->reader, &value, decoder->encoding.endian));
    }
    return value;
}

/// Reads an IEEE-754 binary64 value (0 after an error).
static inline double wf_dec_f64(wf_decoder* decoder) {
    double value = 0.0;
    if (decoder->status == WF_OK) {
        wf_dec_check(decoder, wf_read_f64(&decoder->reader, &value, decoder->encoding.endian));
    }
    return value;
}

/// Reads an unsigned integer and fails with WF_CONST_MISMATCH unless it is @p expected.
static inline void wf_dec_expect_unsigned(wf_decoder* decoder, uint64_t expected, size_t width) {
    const uint64_t value = wf_dec_unsigned(decoder, width);
    if (decoder->status == WF_OK && value != expected) {
        wf_dec_check(decoder, WF_CONST_MISMATCH);
    }
}

/// Reads a signed integer and fails with WF_CONST_MISMATCH unless it is @p expected.
static inline void wf_dec_expect_signed(wf_decoder* decoder, int64_t expected, size_t width) {
    const int64_t value = wf_dec_signed(decoder, width);
    if (decoder->status == WF_OK && value != expected) {
        wf_dec_check(decoder, WF_CONST_MISMATCH);
    }
}

/// Reads a bool and fails with WF_CONST_MISMATCH unless it is @p expected.
static inline void wf_dec_expect_bool(wf_decoder* decoder, bool expected) {
    const bool value = wf_dec_bool(decoder);
    if (decoder->status == WF_OK && value != expected) {
        wf_dec_check(decoder, WF_CONST_MISMATCH);
    }
}

/// Reads a binary32 value and fails with WF_CONST_MISMATCH unless it is @p expected.
static inline void wf_dec_expect_f32(wf_decoder* decoder, float expected) {
    const float value = wf_dec_f32(decoder);
    if (decoder->status == WF_OK && !(value >= expected && value <= expected)) {
        wf_dec_check(decoder, WF_CONST_MISMATCH);
    }
}

/// Reads a binary64 value and fails with WF_CONST_MISMATCH unless it is @p expected.
static inline void wf_dec_expect_f64(wf_decoder* decoder, double expected) {
    const double value = wf_dec_f64(decoder);
    if (decoder->status == WF_OK && !(value >= expected && value <= expected)) {
        wf_dec_check(decoder, WF_CONST_MISMATCH);
    }
}

/// Skips @p count bytes.
static inline void wf_dec_skip(wf_decoder* decoder, size_t count) {
    if (decoder->status == WF_OK) {
        wf_dec_check(decoder, wf_read_skip(&decoder->reader, count));
    }
}

/// Reads an element count stored as an unsigned integer of @p width bytes.
static inline size_t wf_dec_count(wf_decoder* decoder, size_t width) {
    return (size_t)wf_dec_unsigned(decoder, width);
}

/// Reads an element count stored as a bool (0 or 1).
static inline size_t wf_dec_count_bool(wf_decoder* decoder) {
    return wf_dec_bool(decoder) ? 1U : 0U;
}

/**
 * Reads @p width characters into @p text (capacity @p capacity).
 * @param[out] length Number of characters kept; a fixed-width text drops trailing zeros.
 */
static inline void wf_dec_text(wf_decoder* decoder, char* text, size_t capacity, size_t* length,
                               size_t width, bool fixed) {
    if (width > capacity) {
        wf_dec_check(decoder, WF_COUNT_EXCEEDS_MAX);
    }
    if (decoder->status != WF_OK) {
        return;
    }
    size_t kept = 0U;
    for (size_t index = 0U; index < width; ++index) {
        text[index] = (char)(uint8_t)wf_dec_unsigned(decoder, 1U);
        kept = (text[index] != '\0' || !fixed) ? index + 1U : kept;
    }
    *length = kept;
}

/// Records WF_COUNT_EXCEEDS_MAX unless @p low <= @p count <= @p capacity.
static inline void wf_dec_count_in(wf_decoder* decoder, size_t count, size_t low, size_t capacity) {
    if (count < low || count > capacity) {
        wf_dec_check(decoder, WF_COUNT_EXCEEDS_MAX);
    }
}

/**
 * Number of elements of @p element_size bytes in the rest of the input.
 * @return 0 with WF_INVALID_LENGTH when the rest is not a multiple of the element size.
 */
static inline size_t wf_dec_fill_count(wf_decoder* decoder, size_t element_size) {
    if (element_size == 0U || wf_decoder_remaining(decoder) % element_size != 0U) {
        wf_dec_check(decoder, WF_INVALID_LENGTH);
        return 0U;
    }
    return wf_decoder_remaining(decoder) / element_size;
}

/**
 * Sets @p *target to @p count unless the count is outside [@p low, @p capacity]
 * (WF_COUNT_EXCEEDS_MAX) or an earlier error is recorded; the target then keeps its value.
 */
static inline void wf_dec_resize(wf_decoder* decoder, size_t* target, size_t count, size_t low,
                                 size_t capacity) {
    wf_dec_count_in(decoder, count, low, capacity);
    if (decoder->status == WF_OK) {
        *target = count;
    }
}

/// Reads a bit group of @p total_bits bits.
static inline void wf_dec_group(wf_decoder* decoder, wf_bit_unpacker* group, unsigned total_bits) {
    wf_unpacker_init(group, 0U, total_bits, decoder->encoding.bit_order);
    if (decoder->status == WF_OK) {
        wf_dec_check(decoder, wf_read_bits(&decoder->reader, group, total_bits,
                                           decoder->encoding.bit_order, decoder->encoding.endian));
    }
}

/// Extracts the next unsigned field of a bit group.
static inline uint64_t wf_dec_get(wf_decoder* decoder, wf_bit_unpacker* group, unsigned bits) {
    uint64_t value = 0U;
    wf_dec_check(decoder, wf_unpacker_get(group, &value, bits));
    return value;
}

/// Extracts the next signed field of a bit group.
static inline int64_t wf_dec_get_signed(wf_decoder* decoder, wf_bit_unpacker* group,
                                        unsigned bits) {
    int64_t value = 0;
    wf_dec_check(decoder, wf_unpacker_get_signed(group, &value, bits));
    return value;
}

/// Extracts the next field of a bit group as a bool (WF_OUT_OF_RANGE when it is more than 1).
static inline bool wf_dec_get_bool(wf_decoder* decoder, wf_bit_unpacker* group, unsigned bits) {
    const uint64_t wire = wf_dec_get(decoder, group, bits);
    if (wire > 1U) {
        wf_dec_check(decoder, WF_OUT_OF_RANGE);
    }
    return wire == 1U;
}

/// Extracts a constant unsigned field of a bit group (WF_CONST_MISMATCH when it differs).
static inline void wf_dec_expect_bits(wf_decoder* decoder, wf_bit_unpacker* group,
                                      uint64_t expected, unsigned bits) {
    const uint64_t value = wf_dec_get(decoder, group, bits);
    if (decoder->status == WF_OK && value != expected) {
        wf_dec_check(decoder, WF_CONST_MISMATCH);
    }
}

/// Extracts a constant signed field of a bit group (WF_CONST_MISMATCH when it differs).
static inline void wf_dec_expect_bits_signed(wf_decoder* decoder, wf_bit_unpacker* group,
                                             int64_t expected, unsigned bits) {
    const int64_t value = wf_dec_get_signed(decoder, group, bits);
    if (decoder->status == WF_OK && value != expected) {
        wf_dec_check(decoder, WF_CONST_MISMATCH);
    }
}

/// Skips a reserved field of a bit group.
static inline void wf_dec_skip_bits(wf_decoder* decoder, wf_bit_unpacker* group, unsigned bits) {
    uint64_t ignored = 0U;
    wf_dec_check(decoder, wf_unpacker_get(group, &ignored, bits));
}

/// Copies @p length bytes unchanged into @p out.
static inline void wf_dec_bytes(wf_decoder* decoder, uint8_t* out, size_t length) {
    if (decoder->status == WF_OK) {
        wf_dec_check(decoder, wf_read_bytes(&decoder->reader, out, length));
    }
}

/// Reads a checksum of @p size bytes and compares it with @p fn over [@p begin, @p end).
static inline void wf_dec_verify_checksum(wf_decoder* decoder, size_t begin, size_t end,
                                          wf_checksum_fn fn, size_t size) {
    uint64_t received = 0U;
    if (decoder->status == WF_OK) {
        wf_dec_check(decoder,
                     wf_read_unsigned(&decoder->reader, &received, size, decoder->encoding.endian));
    }
    if (decoder->status == WF_OK && fn(&decoder->reader.buffer[begin], end - begin) != received) {
        wf_dec_check(decoder, WF_CHECKSUM_MISMATCH);
    }
}

/// Fails with WF_TRAILING_BYTES when input is left and trailing bytes are not allowed.
static inline void wf_dec_finish(wf_decoder* decoder) {
    if (wf_decoder_remaining(decoder) > 0U && !decoder->encoding.allow_trailing_bytes) {
        wf_dec_check(decoder, WF_TRAILING_BYTES);
    }
}

#endif /* WIREFORGE_C_CODEC_H */

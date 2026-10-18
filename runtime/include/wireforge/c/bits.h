/**
 * @file bits.h
 * @brief Bit masks, two's complement helpers and packing of bit-field groups.
 */
#ifndef WIREFORGE_C_BITS_H
#define WIREFORGE_C_BITS_H

#include "wireforge/c/status.h"

#include <stdbool.h>
#include <stdint.h>

/// Largest supported bit width (fields and bit groups).
#define WF_MAX_BITS 64U

/// Order in which the fields of a bit group are placed in the group.
typedef enum {
    WF_MSB_FIRST = 0, ///< The first field takes the most significant bits of the group.
    WF_LSB_FIRST = 1  ///< The first field takes the least significant bits of the group.
} wf_bit_order;

/**
 * Mask with the low @p bits bits set.
 * @param bits Number of bits, 0..64.
 */
static inline uint64_t wf_low_mask(unsigned bits) {
    return bits >= WF_MAX_BITS ? ~(uint64_t)0U : (((uint64_t)1U << bits) - 1U);
}

/// true when @p value is representable as an unsigned integer of @p bits bits.
static inline bool wf_fits_unsigned(uint64_t value, unsigned bits) {
    return (value & ~wf_low_mask(bits)) == 0U;
}

/// true when @p value is representable as a two's complement integer of @p bits bits (1..64).
static inline bool wf_fits_signed(int64_t value, unsigned bits) {
    if (bits == 0U) {
        return false;
    }
    if (bits >= WF_MAX_BITS) {
        return true;
    }
    const int64_t limit = (int64_t)1 << (bits - 1U);
    return value >= -limit && value < limit;
}

/// Two's complement bit pattern of @p value truncated to @p bits bits.
static inline uint64_t wf_to_twos_complement(int64_t value, unsigned bits) {
    return (uint64_t)value & wf_low_mask(bits);
}

/**
 * Interprets the low @p bits bits of @p raw as two's complement and sign-extends them.
 * @param raw Bit pattern; bits above @p bits are ignored.
 * @param bits Width, 1..64.
 */
static inline int64_t wf_sign_extend(uint64_t raw, unsigned bits) {
    const uint64_t mask = wf_low_mask(bits);
    const uint64_t value = raw & mask;
    const uint64_t sign_bit = (uint64_t)1U << (bits - 1U);
    if ((value & sign_bit) == 0U) {
        return (int64_t)value;
    }
    return -(int64_t)(~value & mask) - 1;
}

/// Reverses the order of the low @p bits bits of @p value.
static inline uint64_t wf_reflect(uint64_t value, unsigned bits) {
    uint64_t result = 0U;
    for (unsigned index = 0U; index < bits; ++index) {
        result = (result << 1U) | ((value >> index) & 1U);
    }
    return result;
}

/// Packs consecutive bit fields into one container integer of 8..64 bits (a multiple of 8).
typedef struct {
    unsigned total_bits; ///< Width of the group.
    unsigned used_bits;  ///< Bits taken so far.
    wf_bit_order order;  ///< Placement of the first field.
    uint64_t container;  ///< Packed value.
} wf_bit_packer;

/// Splits the container integer of a bit group back into fields, in declaration order.
typedef struct {
    uint64_t container;  ///< Packed value.
    unsigned total_bits; ///< Width of the group.
    unsigned used_bits;  ///< Bits extracted so far.
    wf_bit_order order;  ///< Placement of the first field.
} wf_bit_unpacker;

/// true when a group width is 8..64 and a multiple of 8.
static inline bool wf_group_width_valid(unsigned total_bits) {
    return total_bits >= 8U && total_bits <= WF_MAX_BITS && total_bits % 8U == 0U;
}

/// Starts a group of @p total_bits bits.
static inline void wf_packer_init(wf_bit_packer* packer, unsigned total_bits, wf_bit_order order) {
    packer->total_bits = total_bits;
    packer->used_bits = 0U;
    packer->order = order;
    packer->container = 0U;
}

/// true when the fields fill the whole group.
static inline bool wf_packer_complete(const wf_bit_packer* packer) {
    return wf_group_width_valid(packer->total_bits) && packer->used_bits == packer->total_bits;
}

/**
 * Appends the next unsigned field.
 * @return WF_OUT_OF_RANGE when @p value needs more than @p bits bits; WF_INVALID_STATE when the
 *         group width is invalid, @p bits is 0 or the field does not fit the group.
 */
static inline wf_status wf_packer_put(wf_bit_packer* packer, uint64_t value, unsigned bits) {
    if (!wf_group_width_valid(packer->total_bits) || bits == 0U ||
        bits > packer->total_bits - packer->used_bits) {
        return WF_INVALID_STATE;
    }
    if (!wf_fits_unsigned(value, bits)) {
        return WF_OUT_OF_RANGE;
    }
    const unsigned shift = packer->order == WF_MSB_FIRST
                               ? packer->total_bits - packer->used_bits - bits
                               : packer->used_bits;
    packer->container |= value << shift;
    packer->used_bits += bits;
    return WF_OK;
}

/// Appends the next signed field as @p bits-bit two's complement.
static inline wf_status wf_packer_put_signed(wf_bit_packer* packer, int64_t value, unsigned bits) {
    if (!wf_fits_signed(value, bits)) {
        return bits == 0U ? WF_INVALID_STATE : WF_OUT_OF_RANGE;
    }
    return wf_packer_put(packer, wf_to_twos_complement(value, bits), bits);
}

/// Starts the extraction of the fields of a group.
static inline void wf_unpacker_init(wf_bit_unpacker* unpacker, uint64_t container,
                                    unsigned total_bits, wf_bit_order order) {
    unpacker->container = container;
    unpacker->total_bits = total_bits;
    unpacker->used_bits = 0U;
    unpacker->order = order;
}

/**
 * Extracts the next unsigned field.
 * @param[out] value Field value; unchanged on failure.
 * @return WF_INVALID_STATE when the group is invalid, @p bits is 0 or the group is exhausted.
 */
static inline wf_status wf_unpacker_get(wf_bit_unpacker* unpacker, uint64_t* value, unsigned bits) {
    if (!wf_group_width_valid(unpacker->total_bits) || bits == 0U ||
        bits > unpacker->total_bits - unpacker->used_bits) {
        return WF_INVALID_STATE;
    }
    const unsigned shift = unpacker->order == WF_MSB_FIRST
                               ? unpacker->total_bits - unpacker->used_bits - bits
                               : unpacker->used_bits;
    *value = (unpacker->container >> shift) & wf_low_mask(bits);
    unpacker->used_bits += bits;
    return WF_OK;
}

/// Extracts the next field as @p bits-bit two's complement, sign-extended.
static inline wf_status wf_unpacker_get_signed(wf_bit_unpacker* unpacker, int64_t* value,
                                               unsigned bits) {
    uint64_t raw = 0U;
    const wf_status status = wf_unpacker_get(unpacker, &raw, bits);
    if (status == WF_OK) {
        *value = wf_sign_extend(raw, bits);
    }
    return status;
}

#endif /* WIREFORGE_C_BITS_H */

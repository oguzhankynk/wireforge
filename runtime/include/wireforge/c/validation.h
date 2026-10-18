/**
 * @file validation.h
 * @brief Value checks used by generated `validate()` functions.
 *
 * Every check returns WF_OK or the error code. Integer values are passed as int64_t (signed fields
 * and unsigned fields up to 32 bits) or as uint64_t (unsigned 64-bit fields); the bounds are
 * int64_t, as in the expressions of the model.
 */
#ifndef WIREFORGE_C_VALIDATION_H
#define WIREFORGE_C_VALIDATION_H

#include "wireforge/c/bits.h"
#include "wireforge/c/status.h"

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

/// WF_OUT_OF_RANGE when @p value is below @p low.
static inline wf_status wf_check_min_s(int64_t value, int64_t low) {
    return value < low ? WF_OUT_OF_RANGE : WF_OK;
}

/// WF_OUT_OF_RANGE when @p value is above @p high.
static inline wf_status wf_check_max_s(int64_t value, int64_t high) {
    return high < value ? WF_OUT_OF_RANGE : WF_OK;
}

/// WF_OUT_OF_RANGE unless @p low <= @p value <= @p high.
static inline wf_status wf_check_range_s(int64_t value, int64_t low, int64_t high) {
    return wf_check_min_s(value, low) == WF_OK ? wf_check_max_s(value, high) : WF_OUT_OF_RANGE;
}

/// WF_OUT_OF_RANGE when the unsigned @p value is below @p low.
static inline wf_status wf_check_min_u(uint64_t value, int64_t low) {
    return (low >= 0 && value < (uint64_t)low) ? WF_OUT_OF_RANGE : WF_OK;
}

/// WF_OUT_OF_RANGE when the unsigned @p value is above @p high.
static inline wf_status wf_check_max_u(uint64_t value, int64_t high) {
    return (high < 0 || value > (uint64_t)high) ? WF_OUT_OF_RANGE : WF_OK;
}

/// WF_OUT_OF_RANGE unless @p low <= @p value <= @p high, for an unsigned @p value.
static inline wf_status wf_check_range_u(uint64_t value, int64_t low, int64_t high) {
    return wf_check_min_u(value, low) == WF_OK ? wf_check_max_u(value, high) : WF_OUT_OF_RANGE;
}

/// WF_OUT_OF_RANGE when @p value is below @p low (NaN is out of range).
static inline wf_status wf_check_min_f(double value, double low) {
    return value >= low ? WF_OK : WF_OUT_OF_RANGE;
}

/// WF_OUT_OF_RANGE when @p value is above @p high (NaN is out of range).
static inline wf_status wf_check_max_f(double value, double high) {
    return value <= high ? WF_OK : WF_OUT_OF_RANGE;
}

/// WF_OUT_OF_RANGE unless @p low <= @p value <= @p high (NaN is out of range).
static inline wf_status wf_check_range_f(double value, double low, double high) {
    return wf_check_min_f(value, low) == WF_OK ? wf_check_max_f(value, high) : WF_OUT_OF_RANGE;
}

/// WF_OUT_OF_RANGE unless the signed @p value fits @p bits bits (two's complement).
static inline wf_status wf_check_bits_s(int64_t value, unsigned bits) {
    return wf_fits_signed(value, bits) ? WF_OK : WF_OUT_OF_RANGE;
}

/// WF_OUT_OF_RANGE unless the unsigned @p value fits @p bits bits.
static inline wf_status wf_check_bits_u(uint64_t value, unsigned bits) {
    return wf_fits_unsigned(value, bits) ? WF_OK : WF_OUT_OF_RANGE;
}

/// WF_OUT_OF_RANGE when @p valid is false (enum membership and similar checks).
static inline wf_status wf_check_true(bool valid) {
    return valid ? WF_OK : WF_OUT_OF_RANGE;
}

/// WF_OK when @p holds, otherwise @p error.
static inline wf_status wf_status_if(bool holds, wf_status error) {
    return holds ? WF_OK : error;
}

/// @p size when @p status is WF_OK, otherwise 0.
static inline size_t wf_size_if_ok(wf_status status, size_t size) {
    return status == WF_OK ? size : 0U;
}

/// The first of two statuses that is not WF_OK, or WF_OK: `status = wf_first_error(status, check)`.
static inline wf_status wf_first_error(wf_status current, wf_status next) {
    return current == WF_OK ? next : current;
}

/// WF_COUNT_EXCEEDS_MAX unless @p low <= @p count <= @p high.
static inline wf_status wf_check_count(size_t count, size_t low, size_t high) {
    return count >= low && count <= high ? WF_OK : WF_COUNT_EXCEEDS_MAX;
}

/// WF_COUNT_EXCEEDS_MAX unless @p count equals @p expected.
static inline wf_status wf_check_count_equals(size_t count, int64_t expected) {
    return expected >= 0 && (uint64_t)count == (uint64_t)expected ? WF_OK : WF_COUNT_EXCEEDS_MAX;
}

#endif /* WIREFORGE_C_VALIDATION_H */

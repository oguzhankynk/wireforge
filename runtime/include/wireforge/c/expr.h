/**
 * @file expr.h
 * @brief Integer helper functions used by generated size and count expressions.
 *
 * All operands are int64_t.
 */
#ifndef WIREFORGE_C_EXPR_H
#define WIREFORGE_C_EXPR_H

#include <stdint.h>

/// The larger of two sizes, usable in constant expressions.
#define WF_MAX_SIZE(a, b) (((a) > (b)) ? (a) : (b))

/// Smaller of @p lhs and @p rhs.
static inline int64_t wf_expr_min(int64_t lhs, int64_t rhs) {
    return rhs < lhs ? rhs : lhs;
}

/// Larger of @p lhs and @p rhs.
static inline int64_t wf_expr_max(int64_t lhs, int64_t rhs) {
    return lhs < rhs ? rhs : lhs;
}

/**
 * Quotient rounded toward positive infinity.
 * @param lhs Dividend.
 * @param rhs Divisor; 0 gives 0 (an expression of the model that divides by zero has no size).
 */
static inline int64_t wf_expr_ceil_div(int64_t lhs, int64_t rhs) {
    if (rhs == 0) {
        return 0;
    }
    const int64_t quotient = lhs / rhs;
    const int exact = quotient * rhs == lhs;
    return (exact || ((lhs < 0) != (rhs < 0))) ? quotient : quotient + 1;
}

#endif /* WIREFORGE_C_EXPR_H */

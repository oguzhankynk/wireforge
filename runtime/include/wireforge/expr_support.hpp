/**
 * @file expr_support.hpp
 * @brief Integer helper functions used by generated size and count expressions.
 *
 * All operands are `std::int64_t`. In constant expressions an overflow or a division by zero is a
 * compile error.
 */
#ifndef WIREFORGE_EXPR_SUPPORT_HPP
#define WIREFORGE_EXPR_SUPPORT_HPP

#include <cstdint>

namespace wireforge::expr {

/// @brief Smaller of @p lhs and @p rhs.
[[nodiscard]] constexpr std::int64_t min(std::int64_t lhs, std::int64_t rhs) noexcept {
    return rhs < lhs ? rhs : lhs;
}

/// @brief Larger of @p lhs and @p rhs.
[[nodiscard]] constexpr std::int64_t max(std::int64_t lhs, std::int64_t rhs) noexcept {
    return lhs < rhs ? rhs : lhs;
}

/**
 * @brief Quotient rounded toward positive infinity.
 * @param lhs Dividend.
 * @param rhs Divisor, must not be 0.
 */
[[nodiscard]] constexpr std::int64_t ceil_div(std::int64_t lhs, std::int64_t rhs) noexcept {
    const std::int64_t quotient = lhs / rhs;
    const bool exact = quotient * rhs == lhs;
    return exact || ((lhs < 0) != (rhs < 0)) ? quotient : quotient + 1;
}

} // namespace wireforge::expr

#endif // WIREFORGE_EXPR_SUPPORT_HPP

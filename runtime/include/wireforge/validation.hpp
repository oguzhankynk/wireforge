/**
 * @file validation.hpp
 * @brief Value checks used by generated `validate()` functions.
 *
 * Every check returns Status::Ok or the error code; first_error() combines several checks so that
 * generated functions stay free of branches.
 */
#ifndef WIREFORGE_VALIDATION_HPP
#define WIREFORGE_VALIDATION_HPP

#include "wireforge/status.hpp"

#include <cstddef>
#include <cstdint>
#include <type_traits>

namespace wireforge {

/**
 * @brief `lhs < rhs` for any two arithmetic values, correct across signed and unsigned integers.
 */
template <typename A, typename B> [[nodiscard]] constexpr bool less(A lhs, B rhs) noexcept {
    static_assert(std::is_arithmetic_v<A> && std::is_arithmetic_v<B>, "arithmetic values only");
    if constexpr (std::is_integral_v<A> && std::is_integral_v<B>) {
        if constexpr (std::is_signed_v<A> == std::is_signed_v<B>) {
            return lhs < rhs;
        } else if constexpr (std::is_signed_v<A>) {
            return lhs < 0 || static_cast<std::make_unsigned_t<A>>(lhs) < rhs;
        } else {
            return rhs >= 0 && lhs < static_cast<std::make_unsigned_t<B>>(rhs);
        }
    } else {
        return static_cast<double>(lhs) < static_cast<double>(rhs);
    }
}

/// @brief Status::OutOfRange when @p value is below @p low.
template <typename V, typename L>
[[nodiscard]] constexpr Status check_min(V value, L low) noexcept {
    if constexpr (std::is_floating_point_v<V>) {
        return value >= static_cast<V>(low) ? Status::Ok : Status::OutOfRange;
    } else {
        return less(value, low) ? Status::OutOfRange : Status::Ok;
    }
}

/// @brief Status::OutOfRange when @p value is above @p high.
template <typename V, typename H>
[[nodiscard]] constexpr Status check_max(V value, H high) noexcept {
    if constexpr (std::is_floating_point_v<V>) {
        return value <= static_cast<V>(high) ? Status::Ok : Status::OutOfRange;
    } else {
        return less(high, value) ? Status::OutOfRange : Status::Ok;
    }
}

/// @brief Status::OutOfRange unless @p low <= @p value <= @p high (NaN is out of range).
template <typename V, typename L, typename H>
[[nodiscard]] constexpr Status check_range(V value, L low, H high) noexcept {
    return check_min(value, low) == Status::Ok ? check_max(value, high) : Status::OutOfRange;
}

/// @brief Status::OutOfRange unless @p value fits @p bits bits (unsigned or two's complement).
template <typename V> [[nodiscard]] constexpr Status check_bits(V value, unsigned bits) noexcept {
    static_assert(std::is_integral_v<V>, "bit-fields are integers");
    using Unsigned = std::make_unsigned_t<V>;
    constexpr unsigned kWidth = sizeof(V) * 8U;
    // The shift count is taken modulo 64, so it is never 64 (undefined) even in the branch that
    // is not selected for a 64-bit value; MSVC reports such a shift as C4293.
    constexpr std::uint64_t kMask =
        kWidth >= 64U ? ~std::uint64_t{0U} : ((std::uint64_t{1U} << (kWidth % 64U)) - 1U);
    const std::uint64_t raw = static_cast<std::uint64_t>(static_cast<Unsigned>(value)) & kMask;
    if constexpr (std::is_signed_v<V>) {
        // In range when every bit from the sign bit of the field upwards equals the sign.
        const std::uint64_t upper = raw >> (bits - 1U);
        return upper == 0U || upper == (kMask >> (bits - 1U)) ? Status::Ok : Status::OutOfRange;
    } else {
        return (raw >> bits) == 0U ? Status::Ok : Status::OutOfRange;
    }
}

/// @brief Status::OutOfRange when @p valid is false (enum membership and similar checks).
[[nodiscard]] constexpr Status check_true(bool valid) noexcept {
    return valid ? Status::Ok : Status::OutOfRange;
}

/// @brief Status::CountExceedsMax unless @p low <= @p count <= @p high.
[[nodiscard]] constexpr Status check_count(std::size_t count, std::size_t low,
                                           std::size_t high) noexcept {
    return count >= low && count <= high ? Status::Ok : Status::CountExceedsMax;
}

/// @brief Status::CountExceedsMax unless @p count equals @p expected.
[[nodiscard]] constexpr Status check_count_equals(std::size_t count,
                                                  std::int64_t expected) noexcept {
    return expected >= 0 && count == static_cast<std::size_t>(expected) ? Status::Ok
                                                                        : Status::CountExceedsMax;
}

/// @brief Status::Ok when @p holds, otherwise @p error.
[[nodiscard]] constexpr Status status_if(bool holds, Status error) noexcept {
    return holds ? Status::Ok : error;
}

/// @brief @p size when @p status is Status::Ok, otherwise 0.
[[nodiscard]] constexpr std::size_t size_if_ok(Status status, std::size_t size) noexcept {
    return status == Status::Ok ? size : 0U;
}

/// @brief The first of @p statuses that is not Status::Ok, or Status::Ok.
template <typename... Statuses>
[[nodiscard]] constexpr Status first_error(Statuses... statuses) noexcept {
    static_assert((std::is_same_v<Statuses, Status> && ...), "arguments must be Status values");
    Status result = Status::Ok;
    static_cast<void>(((result = result == Status::Ok ? statuses : result), ...));
    return result;
}

/// @brief Identifies a type in overloads of generated free functions (found through ADL).
template <typename T> struct TypeTag {};

/// @brief The TypeTag value of @p T, e.g. `max_wire_size(wireforge::type_tag<Msg>)`.
template <typename T> inline constexpr TypeTag<T> type_tag{};

/// @brief Calls the generated `validate(element)` on every element; returns the first error.
template <typename Container>
[[nodiscard]] constexpr Status validate_each(const Container& container) noexcept {
    Status result = Status::Ok;
    for (const auto& element : container) {
        result = validate(element);
        if (result != Status::Ok) {
            break;
        }
    }
    return result;
}

/// @brief Applies @p check to every element; returns the first error.
template <typename Container, typename Check>
[[nodiscard]] constexpr Status check_each(const Container& container, Check check) noexcept {
    Status result = Status::Ok;
    for (const auto& element : container) {
        result = check(element);
        if (result != Status::Ok) {
            break;
        }
    }
    return result;
}

} // namespace wireforge

#endif // WIREFORGE_VALIDATION_HPP

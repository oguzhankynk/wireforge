/**
 * @file array_access.hpp
 * @brief Element access of std::array without the exception path of std::array::at().
 */
#ifndef WIREFORGE_ARRAY_ACCESS_HPP
#define WIREFORGE_ARRAY_ACCESS_HPP

#include <array>
#include <cstddef>
#include <iterator>

namespace wireforge {

/**
 * @brief Element @p index of @p array.
 *
 * The caller guarantees `index < N` (the runtime only uses indices that are bounded by
 * construction). Unlike std::array::at() this references no exception-throwing library code, so
 * builds without exceptions and without heap stay free of them.
 */
template <typename T, std::size_t N>
[[nodiscard]] constexpr T& array_at(std::array<T, N>& array, std::size_t index) noexcept {
    return *std::next(array.begin(), static_cast<std::ptrdiff_t>(index));
}

/// @brief Element @p index of a const @p array (see the non-const overload).
template <typename T, std::size_t N>
[[nodiscard]] constexpr const T& array_at(const std::array<T, N>& array,
                                          std::size_t index) noexcept {
    return *std::next(array.begin(), static_cast<std::ptrdiff_t>(index));
}

} // namespace wireforge

#endif // WIREFORGE_ARRAY_ACCESS_HPP

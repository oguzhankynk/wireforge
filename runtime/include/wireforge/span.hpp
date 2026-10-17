/**
 * @file span.hpp
 * @brief Non-owning view of contiguous memory.
 */
#ifndef WIREFORGE_SPAN_HPP
#define WIREFORGE_SPAN_HPP

#include <array>
#include <cstddef>
#include <iterator>
#include <type_traits>

namespace wireforge {

/**
 * @brief Non-owning view of a contiguous sequence, similar to C++20 `std::span` with a dynamic
 *        extent.
 *
 * Element access is unchecked; check size() first. All constructors are explicit.
 * @tparam T Element type; use `const T` for a read-only view.
 */
template <typename T> class Span {
public:
    using element_type = T;                 ///< Element type, including const.
    using value_type = std::remove_cv_t<T>; ///< Element type without cv-qualifiers.
    using size_type = std::size_t;          ///< Type of sizes and indices.
    using pointer = T*;                     ///< Pointer to an element.
    using reference = T&;                   ///< Reference to an element.
    using iterator = T*;                    ///< Random-access iterator.

    /// @brief Creates an empty view.
    constexpr Span() noexcept = default;

    /**
     * @brief Creates a view of @p size elements starting at @p data.
     * @param data First element (may be null when @p size is 0).
     * @param size Number of elements.
     */
    constexpr Span(T* data, std::size_t size) noexcept : data_(data), size_(size) {}

    /// @brief Creates a view of all elements of @p array.
    template <std::size_t N>
    explicit constexpr Span(std::array<value_type, N>& array) noexcept
        : data_(array.data()), size_(N) {}

    /// @brief Creates a read-only view of all elements of @p array.
    template <std::size_t N, typename U = T, typename = std::enable_if_t<std::is_const_v<U>>>
    explicit constexpr Span(const std::array<value_type, N>& array) noexcept
        : data_(array.data()), size_(N) {}

    /// @brief Creates a read-only view from a mutable view of the same elements.
    template <typename U,
              typename = std::enable_if_t<std::is_same_v<const U, T> && !std::is_same_v<U, T>>>
    explicit constexpr Span(Span<U> other) noexcept : data_(other.data()), size_(other.size()) {}

    /// @brief Pointer to the first element.
    [[nodiscard]] constexpr T* data() const noexcept { return data_; }
    /// @brief Number of elements.
    [[nodiscard]] constexpr std::size_t size() const noexcept { return size_; }
    /// @brief true when the view has no elements.
    [[nodiscard]] constexpr bool empty() const noexcept { return size_ == 0U; }

    /**
     * @brief Element access without bounds check.
     * @param index Position, must be less than size().
     */
    [[nodiscard]] constexpr T& operator[](std::size_t index) const noexcept {
        return *std::next(data_, static_cast<std::ptrdiff_t>(index));
    }

    /// @brief Iterator to the first element.
    [[nodiscard]] constexpr iterator begin() const noexcept { return data_; }
    /// @brief Iterator past the last element.
    [[nodiscard]] constexpr iterator end() const noexcept {
        return std::next(data_, static_cast<std::ptrdiff_t>(size_));
    }

    /**
     * @brief View of the elements [offset, offset + count), clamped to this view.
     * @param offset First element of the result; values past the end give an empty view.
     * @param count Maximum number of elements.
     */
    [[nodiscard]] constexpr Span subspan(std::size_t offset, std::size_t count) const noexcept {
        const std::size_t start = offset < size_ ? offset : size_;
        const std::size_t available = size_ - start;
        return Span(std::next(data_, static_cast<std::ptrdiff_t>(start)),
                    count < available ? count : available);
    }
    /// @brief View of the elements from @p offset to the end, clamped to this view.
    [[nodiscard]] constexpr Span subspan(std::size_t offset) const noexcept {
        return subspan(offset, size_);
    }
    /// @brief View of the first @p count elements, clamped to this view.
    [[nodiscard]] constexpr Span first(std::size_t count) const noexcept {
        return subspan(0U, count);
    }

private:
    T* data_ = nullptr;
    std::size_t size_ = 0U;
};

/// @brief Creates a mutable view of all elements of @p array.
template <typename T, std::size_t N> constexpr Span<T> make_span(std::array<T, N>& array) noexcept {
    return Span<T>(array);
}

/// @brief Creates a read-only view of all elements of @p array.
template <typename T, std::size_t N>
constexpr Span<const T> make_span(const std::array<T, N>& array) noexcept {
    return Span<const T>(array);
}

} // namespace wireforge

#endif // WIREFORGE_SPAN_HPP

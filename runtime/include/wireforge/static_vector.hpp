/**
 * @file static_vector.hpp
 * @brief Fixed-capacity containers: StaticVector and FixedString.
 */
#ifndef WIREFORGE_STATIC_VECTOR_HPP
#define WIREFORGE_STATIC_VECTOR_HPP

#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <array>
#include <cstddef>
#include <initializer_list>
#include <iterator>
#include <string_view>

namespace wireforge {

/**
 * @brief Vector with a fixed capacity and in-place storage; never allocates.
 *
 * Holds an element count plus a `std::array<T, N>`. An optional field is a StaticVector<T, 1>, a
 * bounded array a StaticVector<T, N>. Elements beyond size() keep their previous values. Element
 * access is unchecked.
 * @tparam T Element type, must be default-constructible.
 * @tparam N Capacity.
 */
template <typename T, std::size_t N> class StaticVector {
public:
    using value_type = T;            ///< Element type.
    using iterator = T*;             ///< Mutable iterator.
    using const_iterator = const T*; ///< Read-only iterator.

    /// @brief Creates an empty vector; the storage is value-initialized.
    constexpr StaticVector() noexcept = default;

    /**
     * @brief Creates a vector holding @p values; values beyond the capacity are dropped.
     */
    explicit constexpr StaticVector(std::initializer_list<T> values) noexcept {
        for (const T& value : values) {
            static_cast<void>(push_back(value)); // values beyond the capacity are dropped
        }
    }

    /// @brief Maximum number of elements (N).
    [[nodiscard]] static constexpr std::size_t capacity() noexcept { return N; }
    /// @brief Current number of elements.
    [[nodiscard]] constexpr std::size_t size() const noexcept { return size_; }
    /// @brief true when size() is 0.
    [[nodiscard]] constexpr bool empty() const noexcept { return size_ == 0U; }
    /// @brief true when size() equals capacity().
    [[nodiscard]] constexpr bool full() const noexcept { return size_ == N; }

    /**
     * @brief Sets the element count; elements added by growing are value-initialized.
     * @param count New size.
     * @return Status::CountExceedsMax (and no change) when @p count > N, otherwise Status::Ok.
     */
    constexpr Status resize(std::size_t count) noexcept {
        if (count > N) {
            return Status::CountExceedsMax;
        }
        for (std::size_t index = size_; index < count; ++index) {
            *slot(index) = T{};
        }
        size_ = count;
        return Status::Ok;
    }

    /**
     * @brief Appends a copy of @p value.
     * @return Status::CountExceedsMax when the vector is full, otherwise Status::Ok.
     */
    constexpr Status push_back(const T& value) noexcept {
        if (size_ == N) {
            return Status::CountExceedsMax;
        }
        *slot(size_) = value;
        ++size_;
        return Status::Ok;
    }

    /// @brief Sets the size to 0 (the storage is not modified).
    constexpr void clear() noexcept { size_ = 0U; }

    /// @brief Element access without bounds check; @p index must be less than capacity().
    [[nodiscard]] constexpr T& operator[](std::size_t index) noexcept { return *slot(index); }
    /// @brief Read-only element access without bounds check.
    [[nodiscard]] constexpr const T& operator[](std::size_t index) const noexcept {
        return *slot(index);
    }

    /// @brief Pointer to the storage.
    [[nodiscard]] constexpr T* data() noexcept { return storage_.data(); }
    /// @brief Read-only pointer to the storage.
    [[nodiscard]] constexpr const T* data() const noexcept { return storage_.data(); }
    /// @brief Iterator to the first element.
    [[nodiscard]] constexpr iterator begin() noexcept { return data(); }
    /// @brief Iterator past the last element.
    [[nodiscard]] constexpr iterator end() noexcept { return slot(size_); }
    /// @brief Read-only iterator to the first element.
    [[nodiscard]] constexpr const_iterator begin() const noexcept { return data(); }
    /// @brief Read-only iterator past the last element.
    [[nodiscard]] constexpr const_iterator end() const noexcept { return slot(size_); }

    /// @brief View of the first size() elements.
    [[nodiscard]] constexpr Span<T> span() noexcept { return Span<T>(data(), size_); }
    /// @brief Read-only view of the first size() elements.
    [[nodiscard]] constexpr Span<const T> span() const noexcept {
        return Span<const T>(data(), size_);
    }

    /// @brief Equal when the sizes and the first size() elements are equal.
    [[nodiscard]] friend constexpr bool operator==(const StaticVector& lhs,
                                                   const StaticVector& rhs) noexcept {
        if (lhs.size_ != rhs.size_) {
            return false;
        }
        for (std::size_t index = 0U; index < lhs.size_; ++index) {
            if (!(lhs[index] == rhs[index])) {
                return false;
            }
        }
        return true;
    }
    /// @brief Negation of operator==.
    [[nodiscard]] friend constexpr bool operator!=(const StaticVector& lhs,
                                                   const StaticVector& rhs) noexcept {
        return !(lhs == rhs);
    }

private:
    [[nodiscard]] constexpr T* slot(std::size_t index) noexcept {
        return std::next(storage_.data(), static_cast<std::ptrdiff_t>(index));
    }
    [[nodiscard]] constexpr const T* slot(std::size_t index) const noexcept {
        return std::next(storage_.data(), static_cast<std::ptrdiff_t>(index));
    }

    std::array<T, N> storage_{};
    std::size_t size_ = 0U;
};

/**
 * @brief Bounded character string with in-place storage; never allocates.
 *
 * Holds up to N characters plus a terminating zero, so c_str() is always valid. Only size()
 * characters are transmitted.
 * @tparam N Maximum number of characters.
 */
template <std::size_t N> class FixedString {
public:
    /// @brief Creates an empty string.
    constexpr FixedString() noexcept = default;

    /// @brief Creates a string holding @p text, truncated to N characters.
    explicit constexpr FixedString(std::string_view text) noexcept {
        static_cast<void>(assign(text.substr(0U, text.size() < N ? text.size() : N)));
    }

    /// @brief Maximum number of characters (N).
    [[nodiscard]] static constexpr std::size_t capacity() noexcept { return N; }
    /// @brief Current number of characters.
    [[nodiscard]] constexpr std::size_t size() const noexcept { return size_; }
    /// @brief true when size() is 0.
    [[nodiscard]] constexpr bool empty() const noexcept { return size_ == 0U; }

    /**
     * @brief Replaces the content with @p text.
     * @return Status::CountExceedsMax (and no change) when @p text is longer than N.
     */
    constexpr Status assign(std::string_view text) noexcept {
        if (text.size() > N) {
            return Status::CountExceedsMax;
        }
        for (std::size_t index = 0U; index < text.size(); ++index) {
            *slot(index) = text[index];
        }
        size_ = text.size();
        *slot(size_) = '\0';
        return Status::Ok;
    }

    /**
     * @brief Sets the length; characters added by growing are zero.
     * @return Status::CountExceedsMax (and no change) when @p count > N.
     */
    constexpr Status resize(std::size_t count) noexcept {
        if (count > N) {
            return Status::CountExceedsMax;
        }
        for (std::size_t index = size_; index <= count; ++index) {
            *slot(index) = '\0';
        }
        size_ = count;
        *slot(size_) = '\0';
        return Status::Ok;
    }

    /// @brief Makes the string empty.
    constexpr void clear() noexcept {
        size_ = 0U;
        *slot(0U) = '\0';
    }

    /// @brief Character access without bounds check; @p index must be at most capacity().
    [[nodiscard]] constexpr char& operator[](std::size_t index) noexcept { return *slot(index); }
    /// @brief Read-only character access without bounds check.
    [[nodiscard]] constexpr const char& operator[](std::size_t index) const noexcept {
        return *slot(index);
    }

    /// @brief Pointer to the characters.
    [[nodiscard]] constexpr char* data() noexcept { return chars_.data(); }
    /// @brief Read-only pointer to the characters.
    [[nodiscard]] constexpr const char* data() const noexcept { return chars_.data(); }
    /// @brief Zero-terminated content.
    [[nodiscard]] constexpr const char* c_str() const noexcept { return chars_.data(); }
    /// @brief Content as a string view of size() characters.
    [[nodiscard]] constexpr std::string_view view() const noexcept {
        return std::string_view(chars_.data(), size_);
    }

    /// @brief Equal when the contents are equal.
    [[nodiscard]] friend constexpr bool operator==(const FixedString& lhs,
                                                   const FixedString& rhs) noexcept {
        return lhs.view() == rhs.view();
    }
    /// @brief Negation of operator==.
    [[nodiscard]] friend constexpr bool operator!=(const FixedString& lhs,
                                                   const FixedString& rhs) noexcept {
        return !(lhs == rhs);
    }

private:
    [[nodiscard]] constexpr char* slot(std::size_t index) noexcept {
        return std::next(chars_.data(), static_cast<std::ptrdiff_t>(index));
    }
    [[nodiscard]] constexpr const char* slot(std::size_t index) const noexcept {
        return std::next(chars_.data(), static_cast<std::ptrdiff_t>(index));
    }

    std::array<char, N + 1U> chars_{};
    std::size_t size_ = 0U;
};

/**
 * @brief Returns an array whose elements are all @p value.
 * @tparam T Element type.
 * @tparam N Number of elements.
 */
template <typename T, std::size_t N>
[[nodiscard]] constexpr std::array<T, N> filled(T value) noexcept {
    std::array<T, N> result{};
    for (std::size_t index = 0U; index < N; ++index) {
        *std::next(result.data(), static_cast<std::ptrdiff_t>(index)) = value;
    }
    return result;
}

} // namespace wireforge

#endif // WIREFORGE_STATIC_VECTOR_HPP

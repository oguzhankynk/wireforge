/**
 * @file ring_buffer.hpp
 * @brief Byte ring buffers: lock-free single-producer/single-consumer and lock-protected variants.
 */
#ifndef WIREFORGE_RING_BUFFER_HPP
#define WIREFORGE_RING_BUFFER_HPP

#include "wireforge/lock.hpp"
#include "wireforge/span.hpp"

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace wireforge {

/**
 * @brief Lock-free single-producer/single-consumer byte ring over caller-provided storage.
 *
 * Thread safety: write() from exactly one producer thread; peek(), read() and consume() from
 * exactly one consumer thread; size queries from either. reset() requires both sides to be idle.
 * The whole storage is usable (no sentinel byte). Data written by the producer is visible to the
 * consumer once write() returns.
 */
class SpscByteRing {
public:
    /// @brief Uses @p storage as the ring memory; it must outlive the ring.
    explicit SpscByteRing(Span<std::uint8_t> storage) noexcept : storage_(storage) {}
    ~SpscByteRing() = default;
    SpscByteRing(const SpscByteRing&) = delete;
    SpscByteRing& operator=(const SpscByteRing&) = delete;
    SpscByteRing(SpscByteRing&&) = delete;
    SpscByteRing& operator=(SpscByteRing&&) = delete;

    /// @brief Size of the storage in bytes.
    [[nodiscard]] std::size_t capacity() const noexcept { return storage_.size(); }

    /// @brief Bytes available to the consumer.
    [[nodiscard]] std::size_t size() const noexcept {
        return distance(head_.load(std::memory_order_acquire),
                        tail_.load(std::memory_order_acquire));
    }
    /// @brief Bytes the producer can still write.
    [[nodiscard]] std::size_t free_space() const noexcept { return capacity() - size(); }
    /// @brief true when no bytes are available.
    [[nodiscard]] bool empty() const noexcept { return size() == 0U; }

    /**
     * @brief Producer: appends as many bytes of @p bytes as fit.
     * @return Number of bytes stored (less than bytes.size() when the ring is full).
     */
    std::size_t write(Span<const std::uint8_t> bytes) noexcept {
        const std::size_t tail = tail_.load(std::memory_order_relaxed);
        const std::size_t head = head_.load(std::memory_order_acquire);
        const std::size_t space = capacity() - distance(head, tail);
        const std::size_t count = bytes.size() < space ? bytes.size() : space;
        std::size_t slot = wrap(tail);
        for (std::size_t index = 0U; index < count; ++index) {
            storage_[slot] = bytes[index];
            slot = slot + 1U == capacity() ? 0U : slot + 1U;
        }
        tail_.store(advance(tail, count), std::memory_order_release);
        return count;
    }

    /**
     * @brief Consumer: copies bytes without consuming them.
     * @param out Destination; up to out.size() bytes are copied.
     * @param offset Number of available bytes to skip before copying.
     * @return Number of bytes copied.
     */
    std::size_t peek(Span<std::uint8_t> out, std::size_t offset = 0U) const noexcept {
        const std::size_t head = head_.load(std::memory_order_relaxed);
        const std::size_t tail = tail_.load(std::memory_order_acquire);
        const std::size_t used = distance(head, tail);
        if (offset >= used) {
            return 0U;
        }
        const std::size_t available = used - offset;
        const std::size_t count = out.size() < available ? out.size() : available;
        std::size_t slot = wrap(advance(head, offset));
        for (std::size_t index = 0U; index < count; ++index) {
            out[index] = storage_[slot];
            slot = slot + 1U == capacity() ? 0U : slot + 1U;
        }
        return count;
    }

    /**
     * @brief Consumer: discards up to @p count bytes.
     * @return Number of bytes discarded.
     */
    std::size_t consume(std::size_t count) noexcept {
        const std::size_t head = head_.load(std::memory_order_relaxed);
        const std::size_t tail = tail_.load(std::memory_order_acquire);
        const std::size_t used = distance(head, tail);
        const std::size_t discarded = count < used ? count : used;
        head_.store(advance(head, discarded), std::memory_order_release);
        return discarded;
    }

    /**
     * @brief Consumer: copies and consumes up to out.size() bytes.
     * @return Number of bytes read.
     */
    std::size_t read(Span<std::uint8_t> out) noexcept { return consume(peek(out)); }

    /// @brief Drops all content. Neither producer nor consumer may run concurrently.
    void reset() noexcept {
        head_.store(0U, std::memory_order_relaxed);
        tail_.store(0U, std::memory_order_release);
    }

private:
    // Indices run in [0, 2 * capacity) so that "full" and "empty" are distinguishable.
    [[nodiscard]] std::size_t distance(std::size_t head, std::size_t tail) const noexcept {
        return tail >= head ? tail - head : (2U * capacity()) - head + tail;
    }
    [[nodiscard]] std::size_t advance(std::size_t index, std::size_t count) const noexcept {
        const std::size_t limit = 2U * capacity();
        const std::size_t next = index + count;
        return next >= limit ? next - limit : next;
    }
    [[nodiscard]] std::size_t wrap(std::size_t index) const noexcept {
        return index >= capacity() ? index - capacity() : index;
    }

    Span<std::uint8_t> storage_;
    std::atomic<std::size_t> head_{0U};
    std::atomic<std::size_t> tail_{0U};
};

/**
 * @brief Byte ring for any number of producers and consumers.
 *
 * Every operation of an SpscByteRing runs under lock policy @p L.
 * @tparam L Lock policy (see lock.hpp).
 */
template <typename L = DefaultLock> class LockedByteRing {
    static_assert(is_lock_policy_v<L>, "L must satisfy the lock policy contract");

public:
    /// @brief Uses @p storage as the ring memory; it must outlive the ring.
    explicit LockedByteRing(Span<std::uint8_t> storage) noexcept : ring_(storage) {}

    /// @copydoc SpscByteRing::capacity
    [[nodiscard]] std::size_t capacity() const noexcept { return ring_.capacity(); }
    /// @copydoc SpscByteRing::size
    [[nodiscard]] std::size_t size() const noexcept {
        const ScopedLock<L> guard(lock_);
        return ring_.size();
    }
    /// @copydoc SpscByteRing::free_space
    [[nodiscard]] std::size_t free_space() const noexcept {
        const ScopedLock<L> guard(lock_);
        return ring_.free_space();
    }
    /// @copydoc SpscByteRing::empty
    [[nodiscard]] bool empty() const noexcept { return size() == 0U; }

    /// @copydoc SpscByteRing::write
    std::size_t write(Span<const std::uint8_t> bytes) noexcept {
        const ScopedLock<L> guard(lock_);
        return ring_.write(bytes);
    }
    /**
     * @brief Stores all of @p bytes or nothing, so records of concurrent producers stay whole.
     * @return true when the bytes were stored.
     */
    bool write_all(Span<const std::uint8_t> bytes) noexcept {
        const ScopedLock<L> guard(lock_);
        if (ring_.free_space() < bytes.size()) {
            return false;
        }
        return ring_.write(bytes) == bytes.size();
    }
    /// @copydoc SpscByteRing::peek
    std::size_t peek(Span<std::uint8_t> out, std::size_t offset = 0U) const noexcept {
        const ScopedLock<L> guard(lock_);
        return ring_.peek(out, offset);
    }
    /// @copydoc SpscByteRing::consume
    std::size_t consume(std::size_t count) noexcept {
        const ScopedLock<L> guard(lock_);
        return ring_.consume(count);
    }
    /// @copydoc SpscByteRing::read
    std::size_t read(Span<std::uint8_t> out) noexcept {
        const ScopedLock<L> guard(lock_);
        return ring_.read(out);
    }
    /// @brief Drops all content.
    void reset() noexcept {
        const ScopedLock<L> guard(lock_);
        ring_.reset();
    }

private:
    mutable L lock_;
    SpscByteRing ring_;
};

/// @cond INTERNAL
namespace detail {
template <std::size_t N> struct RingStorage {
    std::array<std::uint8_t, N> bytes{};
};
} // namespace detail
/// @endcond

/**
 * @brief SpscByteRing with built-in storage of @p N bytes.
 * @tparam N Capacity in bytes.
 */
template <std::size_t N>
class StaticSpscByteRing : private detail::RingStorage<N>, public SpscByteRing {
public:
    /// @brief Creates an empty ring.
    StaticSpscByteRing() noexcept
        : detail::RingStorage<N>(), SpscByteRing(Span<std::uint8_t>(this->bytes)) {}
};

} // namespace wireforge

#endif // WIREFORGE_RING_BUFFER_HPP

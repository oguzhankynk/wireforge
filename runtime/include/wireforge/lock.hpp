/**
 * @file lock.hpp
 * @brief Lock policies, a scoped guard and a statistics counter.
 *
 * A lock policy is a default-constructible type with `void lock() noexcept` and
 * `void unlock() noexcept`. Runtime classes take the policy as a template parameter, so a port to
 * an RTOS supplies its own type and never needs `<mutex>`:
 * @code
 * struct RtosLock {
 *     void lock() noexcept { take(semaphore); }
 *     void unlock() noexcept { give(semaphore); }
 *     Semaphore semaphore;
 * };
 * @endcode
 */
#ifndef WIREFORGE_LOCK_HPP
#define WIREFORGE_LOCK_HPP

#include "wireforge/config.hpp"

#include <atomic>
#include <cstdint>
#include <type_traits>
#include <utility>

#if !defined(WIREFORGE_NO_STD_MUTEX)
#include <mutex>
#endif

namespace wireforge {

/// @brief Detects whether @p L satisfies the lock policy requirements.
template <typename L, typename = void> struct is_lock_policy : std::false_type {};

/// @cond INTERNAL
template <typename L>
struct is_lock_policy<
    L, std::void_t<decltype(std::declval<L&>().lock()), decltype(std::declval<L&>().unlock())>>
    : std::bool_constant<std::is_default_constructible_v<L> &&
                         noexcept(std::declval<L&>().lock()) &&
                         noexcept(std::declval<L&>().unlock())> {};
/// @endcond

/// @brief true when @p L is a lock policy.
template <typename L> inline constexpr bool is_lock_policy_v = is_lock_policy<L>::value;

/// @brief Lock policy that does nothing: for single-threaded use or externally serialized access.
class NullLock {
public:
    /// @brief Does nothing.
    static constexpr void lock() noexcept {}
    /// @brief Does nothing.
    static constexpr void unlock() noexcept {}
};

#if !defined(WIREFORGE_NO_STD_MUTEX)
/**
 * @brief Lock policy backed by `std::mutex`.
 * @note A failing `std::mutex::lock` calls `std::terminate` because lock() is noexcept.
 */
class StdMutexLock {
public:
    /// @brief Creates an unlocked mutex.
    StdMutexLock() noexcept = default;
    ~StdMutexLock() = default;
    StdMutexLock(const StdMutexLock&) = delete;
    StdMutexLock& operator=(const StdMutexLock&) = delete;
    StdMutexLock(StdMutexLock&&) = delete;
    StdMutexLock& operator=(StdMutexLock&&) = delete;

    /// @brief Blocks until the mutex is acquired.
    void lock() noexcept { mutex_.lock(); }
    /// @brief Releases the mutex.
    void unlock() noexcept { mutex_.unlock(); }

private:
    std::mutex mutex_;
};
#endif

#if defined(WIREFORGE_DEFAULT_LOCK)
using DefaultLock = WIREFORGE_DEFAULT_LOCK;
#elif !defined(WIREFORGE_NO_STD_MUTEX)
/**
 * @brief Lock policy used when none is given.
 *
 * `WIREFORGE_DEFAULT_LOCK` when defined, otherwise StdMutexLock, or NullLock when
 * `WIREFORGE_NO_STD_MUTEX` is defined.
 */
using DefaultLock = StdMutexLock;
#else
using DefaultLock = NullLock;
#endif

static_assert(is_lock_policy_v<NullLock>, "NullLock must satisfy the lock policy contract");
static_assert(is_lock_policy_v<DefaultLock>, "DefaultLock must satisfy the lock policy contract");

/**
 * @brief Acquires a lock on construction and releases it on destruction.
 * @tparam L Lock policy.
 */
template <typename L> class ScopedLock {
    static_assert(is_lock_policy_v<L>, "L must satisfy the lock policy contract");

public:
    /// @brief Locks @p lock until this guard is destroyed.
    explicit ScopedLock(L& lock) noexcept : lock_(lock) { lock_.lock(); }
    ~ScopedLock() { lock_.unlock(); }
    ScopedLock(const ScopedLock&) = delete;
    ScopedLock& operator=(const ScopedLock&) = delete;
    ScopedLock(ScopedLock&&) = delete;
    ScopedLock& operator=(ScopedLock&&) = delete;

private:
    L& lock_;
};

/**
 * @brief 32-bit counter that one thread updates and any thread reads.
 *
 * Uses relaxed atomics: the value is informational (statistics) and does not order other memory
 * accesses.
 */
class RelaxedCounter {
public:
    /// @brief Adds one.
    void increment() noexcept { value_.fetch_add(1U, std::memory_order_relaxed); }
    /// @brief Adds @p amount, an unsigned integer (wraps around modulo 2^32).
    template <typename U> void add(U amount) noexcept {
        static_assert(std::is_unsigned_v<U>, "counters add unsigned amounts");
        value_.fetch_add(static_cast<std::uint32_t>(amount), std::memory_order_relaxed);
    }
    /// @brief Sets the counter to zero.
    void reset() noexcept { value_.store(0U, std::memory_order_relaxed); }
    /// @brief Current value.
    [[nodiscard]] std::uint32_t load() const noexcept {
        return value_.load(std::memory_order_relaxed);
    }

private:
    std::atomic<std::uint32_t> value_{0U};
};

} // namespace wireforge

#endif // WIREFORGE_LOCK_HPP

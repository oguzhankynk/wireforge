/**
 * @file dispatcher.hpp
 * @brief Routes received frames or payloads to message handlers.
 */
#ifndef WIREFORGE_DISPATCHER_HPP
#define WIREFORGE_DISPATCHER_HPP

#include "wireforge/array_access.hpp"
#include "wireforge/byte_io.hpp"
#include "wireforge/delegate.hpp"
#include "wireforge/frame_layout.hpp"
#include "wireforge/lock.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <iterator>

namespace wireforge {

/**
 * @brief A received message before decoding.
 *
 * Both spans point into the caller's buffer (for a Link: its receive frame buffer) and are valid
 * only while the handler runs.
 */
struct RawMessage {
    Span<const std::uint8_t> payload; ///< Bytes between header and trailer.
    Span<const std::uint8_t> frame;   ///< The whole frame: header, payload and trailer.
};

/// @brief Handler of one message type; receives the raw message and returns the decode result.
using MessageHandler = Delegate<Status(const RawMessage&)>;

/// @brief Handler for message ids without a registered handler; receives the id and the raw
/// message.
using FallbackHandler = Delegate<Status(std::uint64_t, const RawMessage&)>;

/// @brief Where the message id and the payload are inside a frame.
struct HeaderLayout {
    std::size_t header_size;  ///< Header bytes; the payload starts right after them.
    std::size_t trailer_size; ///< Trailer bytes at the end of the frame.
    FieldLocation message_id; ///< Message id field inside the header.
    Endian endian;            ///< Byte order of the message id field.
};

/// @brief Statistics of a dispatcher; readable from any thread.
struct DispatchCounters {
    RelaxedCounter dispatched;  ///< Payloads passed to a registered handler.
    RelaxedCounter fallback;    ///< Payloads passed to the fallback handler.
    RelaxedCounter unknown_ids; ///< Payloads dropped because no handler matched.
};

/**
 * @brief Header-based dispatcher with a fixed-capacity handler table.
 *
 * Usage: register handlers with add() (and optionally set_fallback()), then call start(). After
 * start() the table is read-only: dispatch() and dispatch_frame() take no lock and may be called
 * from several threads at once, while add() and set_fallback() fail with Status::InvalidState.
 * Registration calls are serialized by lock policy @p L.
 * @tparam N Maximum number of message types.
 * @tparam L Lock policy protecting registration.
 */
template <std::size_t N, typename L = DefaultLock> class Dispatcher {
    static_assert(N > 0U, "a dispatcher needs room for at least one handler");
    static_assert(is_lock_policy_v<L>, "L must satisfy the lock policy contract");

public:
    /// @brief Creates an empty dispatcher for frames laid out as @p layout.
    explicit Dispatcher(const HeaderLayout& layout) noexcept : layout_(layout) {}

    /**
     * @brief Registers @p handler for message @p message_id.
     * @return Status::InvalidState after start(), for an empty handler or an id registered twice;
     *         Status::CountExceedsMax when the table is full.
     */
    Status add(std::uint64_t message_id, MessageHandler handler) noexcept {
        const ScopedLock<L> guard(lock_);
        if (started_.load(std::memory_order_relaxed) || !handler || find(message_id) != nullptr) {
            return Status::InvalidState;
        }
        if (count_ == N) {
            return Status::CountExceedsMax;
        }
        array_at(entries_, count_) = Entry{message_id, handler};
        ++count_;
        return Status::Ok;
    }

    /**
     * @brief Sets the handler for ids without a registered handler.
     * @return Status::InvalidState after start().
     */
    Status set_fallback(FallbackHandler handler) noexcept {
        const ScopedLock<L> guard(lock_);
        if (started_.load(std::memory_order_relaxed)) {
            return Status::InvalidState;
        }
        fallback_ = handler;
        return Status::Ok;
    }

    /**
     * @brief Freezes the table; dispatching is possible afterwards.
     * @return Status::InvalidState when already started.
     */
    Status start() noexcept {
        const ScopedLock<L> guard(lock_);
        if (started_.load(std::memory_order_relaxed)) {
            return Status::InvalidState;
        }
        sort_entries();
        started_.store(true, std::memory_order_release);
        return Status::Ok;
    }

    /// @brief true after start().
    [[nodiscard]] bool started() const noexcept { return started_.load(std::memory_order_acquire); }

    /// @brief Number of registered handlers.
    [[nodiscard]] std::size_t size() const noexcept {
        const ScopedLock<L> guard(lock_);
        return count_;
    }

    /**
     * @brief Passes @p payload to the handler of message @p message_id.
     * @return The handler's result; Status::UnknownMessageId when no handler (and no fallback)
     *         matches; Status::InvalidState before start().
     */
    Status dispatch(std::uint64_t message_id, Span<const std::uint8_t> payload) const noexcept {
        return dispatch(message_id, RawMessage{payload, payload});
    }

    /**
     * @brief Passes @p message to the handler of message @p message_id.
     * @return The handler's result; Status::UnknownMessageId when no handler (and no fallback)
     *         matches; Status::InvalidState before start().
     */
    Status dispatch(std::uint64_t message_id, const RawMessage& message) const noexcept {
        if (!started()) {
            return Status::InvalidState;
        }
        const Entry* entry = find(message_id);
        if (entry != nullptr) {
            counters_.dispatched.increment();
            return entry->handler(message);
        }
        if (fallback_) {
            counters_.fallback.increment();
            return fallback_(message_id, message);
        }
        counters_.unknown_ids.increment();
        return Status::UnknownMessageId;
    }

    /**
     * @brief Reads the message id from a complete frame and dispatches its payload.
     * @param frame Header, payload and trailer, as delivered by FrameDecoder.
     * @return Status::Truncated when @p frame is shorter than header and trailer,
     * Status::InvalidState when the message id field is outside the header; otherwise as
     * dispatch().
     */
    Status dispatch_frame(Span<const std::uint8_t> frame) const noexcept {
        const FieldLocation& field = layout_.message_id;
        if (!valid_width(field.width) || field.offset > layout_.header_size ||
            layout_.header_size - field.offset < field.width) {
            return Status::InvalidState;
        }
        const std::size_t overhead = layout_.header_size + layout_.trailer_size;
        if (frame.size() < overhead) {
            return Status::Truncated;
        }
        const std::uint64_t message_id =
            load_unsigned(frame.subspan(field.offset), field.width, layout_.endian);
        return dispatch(
            message_id,
            RawMessage{frame.subspan(layout_.header_size, frame.size() - overhead), frame});
    }

    /// @brief Statistics since construction.
    [[nodiscard]] const DispatchCounters& counters() const noexcept { return counters_; }

private:
    struct Entry {
        std::uint64_t message_id = 0U;
        MessageHandler handler;
    };

    // Before start(): linear search over the unsorted entries; afterwards: binary search.
    [[nodiscard]] const Entry* find(std::uint64_t message_id) const noexcept {
        if (!started_.load(std::memory_order_acquire)) {
            for (std::size_t index = 0U; index < count_; ++index) {
                if (array_at(entries_, index).message_id == message_id) {
                    return &array_at(entries_, index);
                }
            }
            return nullptr;
        }
        std::size_t low = 0U;
        std::size_t high = count_;
        while (low < high) {
            const std::size_t middle = low + ((high - low) / 2U);
            if (array_at(entries_, middle).message_id < message_id) {
                low = middle + 1U;
            } else {
                high = middle;
            }
        }
        return low < count_ && array_at(entries_, low).message_id == message_id
                   ? &array_at(entries_, low)
                   : nullptr;
    }

    // Insertion sort by message id: each entry is rotated into place behind the smaller ids.
    void sort_entries() noexcept {
        for (std::size_t index = 1U; index < count_; ++index) {
            const std::uint64_t moving_id = array_at(entries_, index).message_id;
            std::size_t position = index;
            while (position > 0U && array_at(entries_, position - 1U).message_id > moving_id) {
                --position;
            }
            const auto first = entries_.begin();
            std::rotate(std::next(first, static_cast<std::ptrdiff_t>(position)),
                        std::next(first, static_cast<std::ptrdiff_t>(index)),
                        std::next(first, static_cast<std::ptrdiff_t>(index + 1U)));
        }
    }

    HeaderLayout layout_;
    std::array<Entry, N> entries_{};
    std::size_t count_ = 0U;
    FallbackHandler fallback_;
    std::atomic<bool> started_{false};
    mutable L lock_;
    mutable DispatchCounters counters_;
};

/**
 * @brief Dispatcher for interfaces without a header: every payload goes to one handler.
 *
 * Same life cycle as Dispatcher: set_handler(), then start(), then dispatch() from any thread.
 * @tparam L Lock policy protecting registration.
 */
template <typename L = DefaultLock> class RawDispatcher {
    static_assert(is_lock_policy_v<L>, "L must satisfy the lock policy contract");

public:
    /**
     * @brief Creates a dispatcher for frames with @p header_size header bytes before and
     *        @p trailer_size trailer bytes after the payload (e.g. a checksum).
     */
    explicit RawDispatcher(std::size_t trailer_size = 0U, std::size_t header_size = 0U) noexcept
        : trailer_size_(trailer_size), header_size_(header_size) {}

    /**
     * @brief Sets the handler of all payloads.
     * @return Status::InvalidState after start() or for an empty handler.
     */
    Status set_handler(MessageHandler handler) noexcept {
        const ScopedLock<L> guard(lock_);
        if (started_.load(std::memory_order_relaxed) || !handler) {
            return Status::InvalidState;
        }
        handler_ = handler;
        return Status::Ok;
    }

    /**
     * @brief Freezes the handler; dispatching is possible afterwards.
     * @return Status::InvalidState when already started.
     */
    Status start() noexcept {
        const ScopedLock<L> guard(lock_);
        if (started_.load(std::memory_order_relaxed)) {
            return Status::InvalidState;
        }
        started_.store(true, std::memory_order_release);
        return Status::Ok;
    }

    /**
     * @brief Passes @p payload to the handler.
     * @return The handler's result; Status::UnknownMessageId without a handler;
     *         Status::InvalidState before start().
     */
    Status dispatch(Span<const std::uint8_t> payload) const noexcept {
        return dispatch(RawMessage{payload, payload});
    }

    /**
     * @brief Passes @p message to the handler.
     * @return The handler's result; Status::UnknownMessageId without a handler;
     *         Status::InvalidState before start().
     */
    Status dispatch(const RawMessage& message) const noexcept {
        if (!started_.load(std::memory_order_acquire)) {
            return Status::InvalidState;
        }
        if (!handler_) {
            counters_.unknown_ids.increment();
            return Status::UnknownMessageId;
        }
        counters_.dispatched.increment();
        return handler_(message);
    }

    /**
     * @brief Dispatches a complete frame: everything between header and trailer is the payload.
     * @return Status::Truncated when @p frame is shorter than header and trailer; otherwise as
     *         dispatch().
     */
    Status dispatch_frame(Span<const std::uint8_t> frame) const noexcept {
        if (frame.size() < header_size_ + trailer_size_) {
            return Status::Truncated;
        }
        return dispatch(RawMessage{
            frame.subspan(header_size_, frame.size() - header_size_ - trailer_size_), frame});
    }

    /// @brief Statistics since construction.
    [[nodiscard]] const DispatchCounters& counters() const noexcept { return counters_; }

private:
    std::size_t trailer_size_;
    std::size_t header_size_;
    MessageHandler handler_;
    std::atomic<bool> started_{false};
    mutable L lock_;
    mutable DispatchCounters counters_;
};

} // namespace wireforge

#endif // WIREFORGE_DISPATCHER_HPP

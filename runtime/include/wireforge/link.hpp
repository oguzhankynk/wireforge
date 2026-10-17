/**
 * @file link.hpp
 * @brief Connects a transport, a frame decoder and a dispatcher with preallocated buffers.
 */
#ifndef WIREFORGE_LINK_HPP
#define WIREFORGE_LINK_HPP

#include "wireforge/lock.hpp"
#include "wireforge/ring_buffer.hpp"
#include "wireforge/span.hpp"
#include "wireforge/status.hpp"
#include "wireforge/transport.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <type_traits>
#include <utility>

namespace wireforge {

/// @brief Decoder type for interfaces without framing: every transport read is one frame.
struct Unframed {};

/// @brief Outcome of one Link::poll() call.
struct PollResult {
    std::size_t frames = 0U;        ///< Frames dispatched successfully.
    std::size_t errors = 0U;        ///< Frames rejected by the decoder or the dispatcher.
    Status last_error = Status::Ok; ///< The most recent error, Status::Ok when none.
};

/// @brief Statistics of a link; readable from any thread.
struct LinkCounters {
    RelaxedCounter rx_bytes;  ///< Bytes received from the transport.
    RelaxedCounter tx_bytes;  ///< Bytes accepted by the transport.
    RelaxedCounter tx_frames; ///< Frames sent completely.
    RelaxedCounter tx_errors; ///< Frames not sent (encoder error or transport full).
};

/**
 * @brief One communication link: receive path transport -> ring -> decoder -> dispatcher, and a
 *        transmit path that encodes into a preallocated buffer and hands it to the transport.
 *
 * Thread safety: poll() runs on a single receiving thread. send() and send_frame() may be called
 * from several threads; they are serialized by lock policy @p L. The transport, decoder and
 * dispatcher are referenced and must outlive the link.
 * @tparam Transport Type satisfying the transport requirements (or ITransport).
 * @tparam Decoder FrameDecoder<...> or Unframed.
 * @tparam Dispatch Dispatcher<...> or RawDispatcher<...> (anything with dispatch_frame()).
 * @tparam MaxFrame Largest frame in bytes (size of the rx and tx frame buffers).
 * @tparam RxCapacity Capacity of the receive ring in bytes (at least MaxFrame).
 * @tparam L Lock policy of the transmit path.
 */
template <typename Transport, typename Decoder, typename Dispatch, std::size_t MaxFrame,
          std::size_t RxCapacity = 2U * MaxFrame, typename L = DefaultLock>
class Link {
    static_assert(is_transport_v<Transport>, "Transport must satisfy the transport requirements");
    static_assert(RxCapacity >= MaxFrame, "the receive ring must hold a whole frame");
    static_assert(is_lock_policy_v<L>, "L must satisfy the lock policy contract");
    static constexpr bool kUnframed = std::is_same_v<Decoder, Unframed>;

public:
    /**
     * @param transport Byte transport.
     * @param decoder Frame decoder (ignored for Unframed).
     * @param dispatcher Receives every complete frame.
     */
    Link(Transport& transport, Decoder& decoder, const Dispatch& dispatcher) noexcept
        : transport_(&transport), decoder_(&decoder), dispatcher_(&dispatcher) {}

    /**
     * @brief Reads everything the transport has and dispatches every complete frame.
     *
     * Call regularly from the receiving thread. For Unframed links each non-empty transport read
     * is dispatched as one frame; a read with more than MaxFrame bytes is rejected as
     * Status::InvalidLength and its bytes are dropped.
     */
    PollResult poll() noexcept {
        PollResult result;
        if constexpr (kUnframed) {
            const std::size_t received = transport_->receive(Span<std::uint8_t>(rx_frame_));
            counters_.rx_bytes.add(received);
            if (received == MaxFrame && discard_excess() > 0U) {
                record(result, Status::InvalidLength);
            } else if (received > 0U) {
                record(result, dispatcher_->dispatch_frame(
                                   Span<const std::uint8_t>(rx_frame_.data(), received)));
            }
        } else {
            for (;;) {
                const std::size_t received = fill_ring();
                drain_ring(result);
                if (received == 0U) {
                    break;
                }
            }
        }
        return result;
    }

    /**
     * @brief Encodes a frame into the transmit buffer and sends it.
     * @param encode Callable `Status(Span<std::uint8_t> buffer, std::size_t& size)` that writes
     *               one frame into @p buffer and sets @p size; called under the transmit lock.
     * @return The encoder's error, Status::BufferTooSmall when the transport did not accept the
     *         whole frame, otherwise Status::Ok.
     */
    template <typename Encode> Status send(Encode&& encode) noexcept {
        const ScopedLock<L> guard(tx_lock_);
        std::size_t size = 0U;
        const Status status = std::forward<Encode>(encode)(Span<std::uint8_t>(tx_frame_), size);
        if (status != Status::Ok || size > MaxFrame) {
            counters_.tx_errors.increment();
            return status != Status::Ok ? status : Status::BufferTooSmall;
        }
        return transmit(Span<const std::uint8_t>(tx_frame_.data(), size));
    }

    /**
     * @brief Sends an already encoded frame.
     * @return Status::BufferTooSmall when the transport did not accept the whole frame.
     */
    Status send_frame(Span<const std::uint8_t> frame) noexcept {
        const ScopedLock<L> guard(tx_lock_);
        return transmit(frame);
    }

    /// @brief Statistics since construction.
    [[nodiscard]] const LinkCounters& counters() const noexcept { return counters_; }

private:
    static void record(PollResult& result, Status status) noexcept {
        if (status == Status::Ok) {
            ++result.frames;
        } else {
            ++result.errors;
            result.last_error = status;
        }
    }

    // Drops what the transport still holds after a read that filled the frame buffer: an
    // unframed read longer than MaxFrame is one oversized frame. Returns the dropped count.
    std::size_t discard_excess() noexcept {
        std::array<std::uint8_t, kChunk> chunk{};
        std::size_t total = 0U;
        for (;;) {
            const std::size_t received = transport_->receive(Span<std::uint8_t>(chunk));
            if (received == 0U) {
                return total;
            }
            counters_.rx_bytes.add(received);
            total += received;
        }
    }

    // Moves bytes from the transport into the ring until either is exhausted; returns the count.
    std::size_t fill_ring() noexcept {
        std::array<std::uint8_t, kChunk> chunk{};
        std::size_t total = 0U;
        while (rx_ring_.free_space() > 0U) {
            const std::size_t wanted =
                rx_ring_.free_space() < chunk.size() ? rx_ring_.free_space() : chunk.size();
            const std::size_t received =
                transport_->receive(Span<std::uint8_t>(chunk).first(wanted));
            if (received == 0U) {
                break;
            }
            counters_.rx_bytes.add(received);
            static_cast<void>(rx_ring_.write(Span<const std::uint8_t>(chunk.data(), received)));
            total += received;
        }
        return total;
    }

    void drain_ring(PollResult& result) noexcept {
        for (;;) {
            std::size_t size = 0U;
            const Status status = decoder_->next(rx_ring_, Span<std::uint8_t>(rx_frame_), size);
            if (status == Status::Truncated) {
                return;
            }
            if (status != Status::Ok) {
                record(result, status);
                continue;
            }
            record(result,
                   dispatcher_->dispatch_frame(Span<const std::uint8_t>(rx_frame_.data(), size)));
        }
    }

    Status transmit(Span<const std::uint8_t> frame) noexcept {
        std::size_t sent = 0U;
        while (sent < frame.size()) {
            const std::size_t accepted = transport_->send(frame.subspan(sent));
            if (accepted == 0U) {
                break;
            }
            sent += accepted;
        }
        counters_.tx_bytes.add(sent);
        if (sent != frame.size()) {
            counters_.tx_errors.increment();
            return Status::BufferTooSmall;
        }
        counters_.tx_frames.increment();
        return Status::Ok;
    }

    static constexpr std::size_t kChunk = 64U;

    Transport* transport_;
    Decoder* decoder_;
    const Dispatch* dispatcher_;
    StaticSpscByteRing<RxCapacity> rx_ring_;
    std::array<std::uint8_t, MaxFrame> rx_frame_{};
    std::array<std::uint8_t, MaxFrame> tx_frame_{};
    L tx_lock_;
    LinkCounters counters_;
};

} // namespace wireforge

#endif // WIREFORGE_LINK_HPP

/**
 * @file transport.hpp
 * @brief Byte transport requirements, a polymorphic transport base and an in-memory loopback.
 *
 * A transport is any type with two non-blocking member functions:
 * @code
 * std::size_t send(wireforge::Span<const std::uint8_t> bytes) noexcept;   // bytes accepted
 * std::size_t receive(wireforge::Span<std::uint8_t> buffer) noexcept;     // bytes read, 0 = none
 * @endcode
 * An existing driver interface is connected by a thin adapter class with these two functions,
 * either as a plain type (no virtual calls) or derived from ITransport.
 */
#ifndef WIREFORGE_TRANSPORT_HPP
#define WIREFORGE_TRANSPORT_HPP

#include "wireforge/ring_buffer.hpp"
#include "wireforge/span.hpp"

#include <cstddef>
#include <cstdint>
#include <type_traits>
#include <utility>

namespace wireforge {

/// @brief Detects whether @p T satisfies the transport requirements.
template <typename T, typename = void> struct is_transport : std::false_type {};

/// @cond INTERNAL
template <typename T>
struct is_transport<
    T, std::void_t<decltype(std::declval<T&>().send(std::declval<Span<const std::uint8_t>>())),
                   decltype(std::declval<T&>().receive(std::declval<Span<std::uint8_t>>()))>>
    : std::bool_constant<
          std::is_same_v<decltype(std::declval<T&>().send(
                             std::declval<Span<const std::uint8_t>>())),
                         std::size_t> &&
          std::is_same_v<decltype(std::declval<T&>().receive(std::declval<Span<std::uint8_t>>())),
                         std::size_t>> {};
/// @endcond

/// @brief true when @p T is a transport.
template <typename T> inline constexpr bool is_transport_v = is_transport<T>::value;

/// @brief Base class for transports selected at run time.
class ITransport {
public:
    /**
     * @brief Hands bytes to the driver without blocking.
     * @return Number of bytes accepted; 0 when the driver cannot take any now.
     */
    virtual std::size_t send(Span<const std::uint8_t> bytes) noexcept = 0;

    /**
     * @brief Copies received bytes into @p buffer without blocking.
     * @return Number of bytes copied; 0 when nothing was received.
     */
    virtual std::size_t receive(Span<std::uint8_t> buffer) noexcept = 0;

protected:
    ITransport() = default;                             ///< Only derived classes construct.
    ITransport(const ITransport&) = default;            ///< Copy for derived classes.
    ITransport& operator=(const ITransport&) = default; ///< Copy for derived classes.
    ITransport(ITransport&&) = default;                 ///< Move for derived classes.
    ITransport& operator=(ITransport&&) = default;      ///< Move for derived classes.
    /// @brief Not virtual: transports are never deleted through an ITransport pointer.
    ~ITransport() = default;
};

static_assert(is_transport_v<ITransport>, "ITransport must satisfy the transport requirements");

/**
 * @brief Transport whose sent bytes are received again, for tests and local links.
 *
 * One thread may send while another receives.
 * @tparam N Capacity in bytes; send() accepts only what fits.
 */
template <std::size_t N> class LoopbackTransport {
public:
    /// @brief Stores as many bytes as fit; returns how many.
    std::size_t send(Span<const std::uint8_t> bytes) noexcept { return ring_.write(bytes); }
    /// @brief Returns bytes in the order they were sent.
    std::size_t receive(Span<std::uint8_t> buffer) noexcept { return ring_.read(buffer); }
    /// @brief Bytes that can still be sent.
    [[nodiscard]] std::size_t free_space() const noexcept { return ring_.free_space(); }
    /// @brief Bytes waiting to be received.
    [[nodiscard]] std::size_t pending() const noexcept { return ring_.size(); }

private:
    StaticSpscByteRing<N> ring_;
};

} // namespace wireforge

#endif // WIREFORGE_TRANSPORT_HPP

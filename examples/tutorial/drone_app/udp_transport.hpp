// A WireForge transport over a UDP socket (POSIX): every datagram the link sends is one frame, and
// received datagrams are handed to the link as they come. Both calls never block.
#ifndef DRONE_APP_UDP_TRANSPORT_HPP
#define DRONE_APP_UDP_TRANSPORT_HPP

#include <arpa/inet.h>
#include <cstddef>
#include <cstdint>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#include <wireforge/span.hpp>

class UdpTransport {
public:
    /// Listens on @p local_port of 127.0.0.1 and sends to @p remote_port of 127.0.0.1.
    UdpTransport(std::uint16_t local_port, std::uint16_t remote_port) noexcept
        : socket_(::socket(AF_INET, SOCK_DGRAM, 0)), remote_(address(remote_port)) {
        const sockaddr_in local = address(local_port);
        const bool bound =
            socket_ >= 0 &&
            ::bind(socket_, reinterpret_cast<const sockaddr*>(&local), sizeof local) == 0 &&
            ::fcntl(socket_, F_SETFL, O_NONBLOCK) == 0;
        if (!bound && socket_ >= 0) {
            ::close(socket_);
            socket_ = -1;
        }
    }
    UdpTransport(const UdpTransport&) = delete;
    UdpTransport& operator=(const UdpTransport&) = delete;
    ~UdpTransport() {
        if (socket_ >= 0) {
            ::close(socket_);
        }
    }

    [[nodiscard]] bool ok() const noexcept { return socket_ >= 0; }

    std::size_t send(wireforge::Span<const std::uint8_t> bytes) noexcept {
        const ssize_t sent = ::sendto(socket_, bytes.data(), bytes.size(), 0,
                                      reinterpret_cast<const sockaddr*>(&remote_), sizeof remote_);
        return sent < 0 ? 0U : static_cast<std::size_t>(sent);
    }

    std::size_t receive(wireforge::Span<std::uint8_t> buffer) noexcept {
        const ssize_t got = ::recv(socket_, buffer.data(), buffer.size(), 0);
        return got < 0 ? 0U : static_cast<std::size_t>(got);
    }

private:
    static sockaddr_in address(std::uint16_t port) noexcept {
        sockaddr_in result{};
        result.sin_family = AF_INET;
        result.sin_port = htons(port);
        result.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        return result;
    }

    int socket_;
    sockaddr_in remote_;
};

#endif // DRONE_APP_UDP_TRANSPORT_HPP

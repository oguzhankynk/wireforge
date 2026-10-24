// The vehicle of the WireForge tutorial. It speaks MAVLink 2 over UDP with the code generated from
// the tutorial's model: HEARTBEAT once a second, ATTITUDE ten times and VFR_HUD four times a second
// to 127.0.0.1:14550 (where ground stations listen), and a COMMAND_ACK for every COMMAND_LONG it
// receives on port 14551. Usage: drone_sim [seconds]   (default 10)
#include "drone/interfaces/link.hpp"
#include "drone/model.hpp"
#include "udp_transport.hpp"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <thread>
#include <wireforge/span.hpp>
#include <wireforge/status.hpp>

namespace {

namespace mav = drone::mavlink2;
namespace link = mav::link;

constexpr std::uint16_t kVehiclePort = 14551;
constexpr std::uint16_t kGroundStationPort = 14550;
constexpr std::uint16_t kArmDisarm = 400; // MAV_CMD_COMPONENT_ARM_DISARM
constexpr std::uint8_t kQuadrotor = 2;    // MAV_TYPE_QUADROTOR
constexpr std::uint8_t kArmed = 128;      // MAV_MODE_FLAG_SAFETY_ARMED
constexpr std::uint8_t kActive = 4;       // MAV_STATE_ACTIVE

// Receives what the ground station sends. Every message of the model has a callback.
class Vehicle final : public link::Handler {
public:
    void onHeartbeatReceived(const mav::Heartbeat& /*message*/) noexcept override {
        ground_station_seen_ = true;
    }
    void onCommandLongReceived(const mav::CommandLong& command) noexcept override {
        if (command.command == kArmDisarm) {
            armed_ = command.param1 > 0.5F;
        }
        std::printf("vehicle: COMMAND_LONG %u (param1 %.0f), answering COMMAND_ACK\n",
                    static_cast<unsigned>(command.command), static_cast<double>(command.param1));
        pending_ack_ = command.command;
        has_pending_ack_ = true;
    }
    void onGpsRawIntReceived(const mav::GpsRawInt& /*message*/) noexcept override {}
    void onAttitudeReceived(const mav::Attitude& /*message*/) noexcept override {}
    void onGlobalPositionIntReceived(const mav::GlobalPositionInt& /*message*/) noexcept override {}
    void onCommandAckReceived(const mav::CommandAck& /*message*/) noexcept override {}
    void onVfrHudReceived(const mav::VfrHud& /*message*/) noexcept override {}

    [[nodiscard]] bool armed() const noexcept { return armed_; }
    [[nodiscard]] bool ground_station_seen() const noexcept { return ground_station_seen_; }
    // The command to acknowledge, once.
    [[nodiscard]] bool take_ack(std::uint16_t& command) noexcept {
        command = pending_ack_;
        const bool pending = has_pending_ack_;
        has_pending_ack_ = false;
        return pending;
    }

private:
    bool armed_ = false;
    bool ground_station_seen_ = false;
    bool has_pending_ack_ = false;
    std::uint16_t pending_ack_ = 0U;
};

} // namespace

int main(int argc, char** argv) {
    const long seconds = argc > 1 ? std::strtol(argv[1], nullptr, 10) : 10L;
    UdpTransport transport(kVehiclePort, kGroundStationPort);
    if (!transport.ok()) {
        std::fprintf(stderr, "vehicle: cannot open UDP port %u\n", kVehiclePort);
        return 1;
    }
    Vehicle vehicle;
    link::Endpoint endpoint(vehicle);
    link::Link<UdpTransport> channel(transport, endpoint.decoder(), endpoint.dispatcher());

    link::Header header{};
    header.systemId = 1U;
    header.componentId = 1U;
    // Encodes one message as a frame and sends it; the sequence number counts every frame.
    const auto send = [&](const auto& message) {
        const wireforge::Status status =
            channel.send([&](wireforge::Span<std::uint8_t> buffer, std::size_t& size) {
                return link::encode_frame(message, buffer, size, header);
            });
        header.sequence = static_cast<std::uint8_t>(header.sequence + 1U);
        return status;
    };

    std::printf("vehicle: MAVLink 2 on UDP, sending to 127.0.0.1:%u for %ld s\n",
                kGroundStationPort, seconds);
    const auto start = std::chrono::steady_clock::now();
    unsigned tick = 0U; // 10 ms ticks
    while (std::chrono::steady_clock::now() - start < std::chrono::seconds(seconds)) {
        const double t = 0.01 * tick;
        if (tick % 100U == 0U) {
            mav::Heartbeat heartbeat{};
            heartbeat.type = kQuadrotor;
            heartbeat.baseMode = vehicle.armed() ? kArmed : 0U;
            heartbeat.systemStatus = kActive;
            static_cast<void>(send(heartbeat));
        }
        if (tick % 10U == 0U) {
            mav::Attitude attitude{};
            attitude.timeBootMs = 10U * tick;
            attitude.roll = static_cast<float>(0.2 * std::sin(t));
            attitude.pitch = static_cast<float>(0.1 * std::cos(t));
            static_cast<void>(send(attitude));
        }
        if (tick % 25U == 0U) {
            mav::VfrHud hud{};
            hud.groundspeed = vehicle.armed() ? 4.5F : 0.0F;
            hud.alt = static_cast<float>(10.0 + std::sin(0.5 * t));
            hud.heading = static_cast<std::int16_t>(static_cast<int>(10.0 * t) % 360);
            static_cast<void>(send(hud));
        }
        static_cast<void>(channel.poll());
        std::uint16_t command = 0U;
        if (vehicle.take_ack(command)) {
            mav::CommandAck ack{};
            ack.command = command;
            ack.result = 0U; // MAV_RESULT_ACCEPTED; the extension fields stay 0 and are not sent
            static_cast<void>(send(ack));
        }
        ++tick;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    std::printf("vehicle: done, %u frames sent, ground station %s\n",
                static_cast<unsigned>(header.sequence),
                vehicle.ground_station_seen() ? "seen" : "not seen");
    return 0;
}

// Uses the code generated from examples/01_basic_telemetry: fill a SensorReading, send it as a
// frame through a loopback link and receive it in a handler.
#include "basic_telemetry/interfaces/sensor_to_us.hpp"
#include "basic_telemetry/model.hpp"

#include <cstdint>
#include <cstdio>
#include <wireforge/span.hpp>
#include <wireforge/status.hpp>
#include <wireforge/transport.hpp>

namespace {

namespace telemetry = demo::telemetry;
namespace link = telemetry::sensor_to_us;

class Application final : public link::Handler {
public:
    void onSensorReadingReceived(const telemetry::SensorReading& reading) noexcept override {
        received_ = reading.sensorId;
        samples_ = reading.samples.size();
    }
    [[nodiscard]] std::uint8_t received() const noexcept { return received_; }
    [[nodiscard]] std::size_t samples() const noexcept { return samples_; }

private:
    std::uint8_t received_ = 0U;
    std::size_t samples_ = 0U;
};

} // namespace

int main() {
    telemetry::SensorReading reading{};
    reading.sensorId = 7U;
    reading.temperature = 21.5F;
    static_cast<void>(reading.samples.push_back(100U));
    static_cast<void>(reading.samples.push_back(200U));
    if (validate(reading) != wireforge::Status::Ok) {
        static_cast<void>(std::fputs("invalid reading\n", stderr));
        return 1;
    }

    Application application;
    link::Endpoint endpoint(application);
    wireforge::LoopbackTransport<256> transport;
    link::Link<wireforge::LoopbackTransport<256>> channel(transport, endpoint.decoder(),
                                                          endpoint.dispatcher());
    const wireforge::Status sent =
        channel.send([&reading](wireforge::Span<std::uint8_t> buffer, std::size_t& size) {
            return link::encode_frame(reading, buffer, size);
        });
    const wireforge::PollResult polled = channel.poll();
    const bool ok = sent == wireforge::Status::Ok && polled.frames == 1U &&
                    application.received() == 7U && application.samples() == 2U;
    static_cast<void>(std::fputs(ok ? "consumer: frame received\n" : "consumer: FAILED\n", stdout));
    return ok ? 0 : 1;
}

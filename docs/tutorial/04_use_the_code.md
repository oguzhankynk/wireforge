# 4. Use the code

**What we will do:** build a small vehicle that speaks MAVLink 2 over UDP with the code generated from `drone`, and
check it with pymavlink, an independent MAVLink implementation. The vehicle sends HEARTBEAT, ATTITUDE and VFR_HUD and
answers COMMAND_LONG with COMMAND_ACK.

## Get the example
The application is in the WireForge repository, `examples/tutorial/drone_app/`: three files, `CMakeLists.txt`,
`main.cpp` and `udp_transport.hpp`, plus `ground_station.py`.
```sh
git clone --depth 1 https://github.com/oguzhankynk/wireforge.git wireforge-src
cp -r wireforge-src/examples/tutorial/drone_app .
```
<!-- tutorial: hidden
cp -r "$WIREFORGE_REPO/examples/tutorial/drone_app" .
-->

## Build it
<!-- tutorial: run -->
```sh
cmake -S drone_app -B build/drone_app -DDRONE_PROJECT="$PWD/drone" -DWireForge_DIR="$(wireforge cmake-dir)"
cmake --build build/drone_app
```
`find_package(WireForge)` finds the CMake package that comes with WireForge, and one line in `CMakeLists.txt` generates
the code from your model when CMake configures the build:
```cmake
wireforge_generate(drone_model PROJECT "${DRONE_PROJECT}")
target_link_libraries(drone_sim PRIVATE drone_model)
```
Change the model and build again: the code follows.

## Fly it
<!-- tutorial: run -->
```sh
python -m pip install pymavlink
build/drone_app/drone_sim 6 & python drone_app/ground_station.py; wait
```
The vehicle sends for six seconds; the ground station waits for its HEARTBEAT, sends COMMAND_LONG 400 (arm), and checks
that COMMAND_ACK, ATTITUDE and VFR_HUD arrive:
```text
ground station: HEARTBEAT from system 1, type 2
ground station: ATTITUDE {'time_boot_ms': 1000, 'roll': 0.168..., 'pitch': 0.054..., 'yaw': 0.0, ...}
ground station: VFR_HUD {'airspeed': 0.0, 'groundspeed': 0.0, 'heading': 10, 'throttle': 0, 'alt': 10.47..., ...}
vehicle: COMMAND_LONG 400 (param1 1), answering COMMAND_ACK
ground station: COMMAND_ACK {'command': 400, 'result': 0, 'progress': 0, ...}
ground station: all messages arrived
```
pymavlink was not written with WireForge, so this is a real interoperability check: the frames, the trimmed payloads and
the checksums are the ones every MAVLink tool expects. Ground station software such as QGroundControl listens on the
same UDP port 14550.

## How the code is used
The handler receives decoded messages. Every message of the model has a callback, so adding a message to the model
asks you, at compile time, what the application does with it:
```cpp
namespace mav = drone::mavlink2;
namespace link = mav::link;

class Vehicle final : public link::Handler {
public:
    void onCommandLongReceived(const mav::CommandLong& command) noexcept override { /* ... */ }
    void onVfrHudReceived(const mav::VfrHud& /*message*/) noexcept override {}
    // ... one callback per message
};
```
The endpoint joins the frame decoder, the dispatcher and the handler; the link reads a transport and sends frames:
```cpp
UdpTransport transport(14551, 14550);
Vehicle vehicle;
link::Endpoint endpoint(vehicle);
link::Link<UdpTransport> channel(transport, endpoint.decoder(), endpoint.dispatcher());

mav::Attitude attitude{};
attitude.roll = 0.2F;
channel.send([&](wireforge::Span<std::uint8_t> buffer, std::size_t& size) {
    return link::encode_frame(attitude, buffer, size, header);
});
channel.poll();  // reads what arrived and calls the handler
```
A transport is any class with a non-blocking `send` and `receive` (`udp_transport.hpp` is 60 lines); on a
microcontroller it is a UART or a CAN driver. Nothing allocates memory, nothing throws, and trimming the trailing zeros
of MAVLink 2 is done by the generated code: the application never sees it.

For C, generate with `--language c`: the same model gives `model.h`/`model.c` and a handler struct of callbacks.

**More:** [generated code](../CODEGEN.md), [runtime](../RUNTIME.md), `examples/cmake_consumer/`.

**If something goes wrong:** `Could not find a package configuration file provided by "WireForge"`: pass
`-DWireForge_DIR="$(wireforge cmake-dir)"`. Port 14550 or 14551 in use: another ground station or simulator is running.

Next: [5. Tests](05_tests.md)

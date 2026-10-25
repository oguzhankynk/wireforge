# 4. Use the code

**What we will do:** build a small vehicle that speaks MAVLink 2 over UDP with the code generated from `drone`, and
check it with pymavlink, an independent MAVLink implementation. The vehicle sends HEARTBEAT, ATTITUDE and VFR_HUD and
answers COMMAND_LONG with COMMAND_ACK.

## Get the example
The application is in the WireForge repository, `examples/tutorial/drone_app/`: three files, `CMakeLists.txt`,
`main.cpp` and `udp_transport.hpp`, plus `ground_station.py`. Its C version and the firmware of chapter 5 are next to
it.
```sh
git clone --depth 1 https://github.com/oguzhankynk/wireforge.git wireforge-src
```
<!-- tutorial: hidden
ln -s "$WIREFORGE_REPO" wireforge-src
-->
<!-- tutorial: run -->
```sh
cp -r wireforge-src/examples/tutorial/drone_app wireforge-src/examples/tutorial/drone_app_c .
```

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

## The same in C
`drone_app_c/` is the same vehicle in C99. One option of `wireforge_generate` makes the generated code C, and the
library becomes a static library of the generated sources:
```cmake
wireforge_generate(drone_model PROJECT "${DRONE_PROJECT}" LANGUAGE c)
```
<!-- tutorial: run -->
```sh
cmake -S drone_app_c -B build/drone_app_c -DDRONE_PROJECT="$PWD/drone" -DWireForge_DIR="$(wireforge cmake-dir)"
cmake --build build/drone_app_c
build/drone_app_c/drone_sim_c 6 & python drone_app/ground_station.py; wait
```
The ground station sees the same messages. In C the handler is a struct of callbacks, and a callback left `NULL`
means "decode and ignore", so the vehicle sets only the two it acts on:
```c
self->handler.context = self;
self->handler.on_Heartbeat_received = on_heartbeat;
self->handler.on_CommandLong_received = on_command_long;
drone_mavlink2_link_link_init(&self->link, transport, &self->handler);
```
The vehicle itself (`vehicle.c`) knows nothing about UDP: `main.c` gives it a transport and calls `vehicle_tick()`
every 10 ms. That is what lets the next chapter run it on a microcontroller.

## Against ArduPilot
So far both ends were ours. ArduPilot's simulator (SITL) runs the real ArduCopter firmware on your computer, and
`ardupilot_gcs/` is a small ground station in C for it, built from the import of the whole `ardupilotmega.xml` (every
message of chapter 3's dialect, not just two). Get a SITL build of ArduCopter for Linux from ArduPilot's firmware server
and its default parameters, and start it; it waits for a ground station on TCP port 5760:
```sh
curl -fLO https://firmware.ardupilot.org/Copter/stable/SITL_x86_64_linux_gnu/arducopter && chmod +x arducopter
curl -fLO https://raw.githubusercontent.com/ArduPilot/ardupilot/master/Tools/autotest/default_params/copter.parm
./arducopter --model quad --defaults copter.parm -I0 &
```
Then import the dialect, build the ground station and run it:
```sh
cp -r wireforge-src/examples/tutorial/ardupilot_gcs .
wireforge import-mavlink "$DIALECTS/ardupilotmega.xml" ardupilot --name ardupilot
cmake -S ardupilot_gcs -B build/ardupilot_gcs -DARDUPILOT_PROJECT="$PWD/ardupilot" -DWireForge_DIR="$(wireforge cmake-dir)"
cmake --build build/ardupilot_gcs
build/ardupilot_gcs/ardupilot_gcs 15
```
It sends a HEARTBEAT every second, asks for the telemetry streams, the AUTOPILOT_VERSION (with a COMMAND_LONG) and the
parameter list, and counts what the autopilot sends back. With ArduCopter 4.7.2:
```text
ardupilot_gcs: 2992 frames decoded, 0 rejected
ardupilot_gcs: HEARTBEAT 15 (type 2), ATTITUDE 59 (roll 0.001), GLOBAL_POSITION_INT 59, VFR_HUD 59 (alt 584.08)
ardupilot_gcs: AUTOPILOT_VERSION 1 (firmware 4.7.2), COMMAND_ACK 1 (command 512, result 0)
ardupilot_gcs: PARAM_VALUE 1369 of 1369
```
Every frame of the autopilot, of more than thirty kinds, is decoded and its checksum verified; none is rejected. The
handler has callbacks for seven messages, and the others are decoded and ignored. Stop the simulator with `kill %1`.

**More:** [generated code](../CODEGEN.md) (C++ and C), [runtime](../RUNTIME.md), `examples/cmake_consumer/`.

**If something goes wrong:** `Could not find a package configuration file provided by "WireForge"`: pass
`-DWireForge_DIR="$(wireforge cmake-dir)"`. Port 14550 or 14551 in use: another ground station or simulator is running.

Next: [5. On a microcontroller](05_on_a_microcontroller.md)

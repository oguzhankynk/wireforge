# 1. Install and the first project

**What we will do:** install WireForge, create a project from the published MAVLink 2 model, encode a few frames and
run the tests WireForge generates for it.

## Install
```sh
python -m pip install "wireforge[server,mcp]"
wireforge --version
```
`server` adds the web UI of chapter 2, `mcp` the agent server of chapter 8. Make a directory for the tutorial and work
in it:
```sh
mkdir wireforge-tutorial && cd wireforge-tutorial
```

## Start from MAVLink 2
<!-- tutorial: run -->
```sh
wireforge init drone --protocol mavlink2
wireforge validate drone
```
`drone/` is now a working project: `project.yaml` describes the MAVLink 2 frame and six messages of the common message
set (HEARTBEAT, GPS_RAW_INT, ATTITUDE, GLOBAL_POSITION_INT, COMMAND_LONG, COMMAND_ACK). `drone/README.md` says where the
model comes from, what it covers and how it was checked against pymavlink. `wireforge init --list` shows the other
templates and protocols (Modbus, CRSF, J1939, UBX, MAVLink 1).

Open `drone/project.yaml` and look around. The header of the interface `link` is the MAVLink 2 header, field by field;
`trim_trailing_zeros: true` says that MAVLink 2 leaves out the zero bytes at the end of a payload.

## Encode a frame
<!-- tutorial: run -->
```sh
wireforge encode drone Heartbeat '{ type: 2, systemStatus: 4 }' --interface link
wireforge encode drone CommandAck '{ command: 400 }' --interface link
```
Each command prints a whole frame in hex. The first one is a HEARTBEAT of a quadrotor (`type: 2`) that is active
(`systemStatus: 4`):
```text
fd09000000010100000000000000020000040386ca
```
`fd` is the MAVLink 2 start byte, `09` the payload length, then flags, sequence, system and component id, the
three-byte message id (`000000`, HEARTBEAT), the payload and the checksum.

The second one shows MAVLink 2's trimming:
```text
fd0200000001014d00009001a058
```
COMMAND_ACK has ten payload bytes, but only the command (400 = `9001`, little endian) is not zero, so the frame carries
two payload bytes (`02`). A receiver reads the missing bytes as zeros. Without `--interface` you get the payload alone.

Decode it again:
<!-- tutorial: run -->
```sh
wireforge decode drone fd0200000001014d00009001a058 --interface link
```
The answer names the message, every field (the eight that were not sent are 0), the header and the checksum.

## Look at the layout
<!-- tutorial: run -->
```sh
wireforge analyze drone
```
The report lists every message with the offset and size of each field, and the frame and buffer sizes of the
interface: everything a C programmer would otherwise count by hand.

## Generate the code and run its tests
<!-- tutorial: run -->
```sh
wireforge test drone -o build/drone
```
This generates C++17 for the model (`build/drone/drone/model.hpp` and `interfaces/link.hpp`), plus a GoogleTest project
that checks every message and frame against WireForge's reference codec, builds it and runs it. All tests pass. For C99
code instead, add `--language c`.

**What happened:** the model was validated, the code generated (no heap, no exceptions, no RTTI), and the tests were
derived from the model itself, not written by hand. Chapter 5 shows what they prove.

**More:** [User guide](../GUIDE.md), [command line](../CLI.md), [definition format](../SCHEMA_SPEC.md).

**If something goes wrong:** `wireforge test` needs CMake and a C++ compiler on the `PATH`; the message says which tool
is missing. `wireforge validate` reports every problem with file, line and JSON pointer.

Next: [2. The web UI](02_web_ui.md)

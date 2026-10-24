# Wireshark dissector

`wireforge wireshark <project> -o <file>.lua` writes one self-contained Lua plugin that dissects every interface of the
project in Wireshark and tshark. The plugin has two parts. The first is a data table generated from the model: every
profile's header, trailer, framing and messages, with fields expanded, bit positions, ranges and checksums. The second
is a fixed interpreter (`codegen/wireforge/wireshark_runtime.lua`) that decodes frames by the same rules as the
reference codec (docs/SCHEMA_SPEC.md, section 16). Code: `wireforge.wireshark`.

The plugin is tested with Wireshark 3.6, 4.2 and 4.6, on Lua 5.2 and Lua 5.4.

## Install and bind
- **Wireshark:** copy the file into the personal Lua plugins folder, then restart Wireshark or reload Lua plugins
  (Ctrl+Shift+L). To find the folder, open *Help > About Wireshark > Folders*. On Linux it is
  `~/.local/lib/wireshark/plugins`; on Windows it is `%APPDATA%\Wireshark\plugins`.
- **tshark:** run `tshark -X lua_script:<file>.lua ...`.
- **Binding:** each interface is one protocol. Its filter name is `<project>_<interface>`, in lower case, with
  characters other than letters, digits and `_` replaced by `_`. Bind it to traffic in one of two ways:
  - Set its preferences under *Edit > Preferences > Protocols*: *UDP port*, *TCP port*, or *User DLT* (USER0..USER15,
    for captures of serial links and other raw byte streams). On the command line:
    `-o basic_telemetry_sensor_to_us.udp_port:5000`.
  - Use *Decode As* (UDP or TCP port).
- **Profiles:** in a project with profiles, the *Profile* preference selects the layout of the captured traffic
  (`-o <protocol>.profile:platformB`).

## What is shown
- **Field tree:** one tree per frame, with Header, the message and Trailer. Structs and arrays become subtrees; array
  elements are labelled `[i]`. Bit-fields show their bit mask; enums show the member name. Units are added to the
  label (`temperature [degC]`) and descriptions become the field's blurb.
- **Display filters:**
  - message fields: `<protocol>.<Message>.<path>` (e.g. `basic_telemetry_sensor_to_us.SensorReading.accel.x > 1.5`);
  - header and trailer fields: `<protocol>.header.<name>`, `<protocol>.trailer.<name>`;
  - one message type: `<protocol>.<Message>`.
- **Frames:** the framing of the interface splits the stream into frames:
  - `none`: one frame per packet;
  - `fixed`: frames of `frame_size` bytes;
  - `length_prefixed` and `sync_length`: frame size from the length field, as counted by `length_counts`.

  With `trim_trailing_zeros` a payload shorter than its message is dissected after being extended with zeros: the
  fields refer to a separate *Zero-extended payload* data source, and the message item says how many zero bytes were
  not sent.

  Over TCP, a frame split across segments is reassembled. A packet may also hold several frames. With
  `resync: true`, bytes before the next sync pattern are shown as *Skipped bytes* (warning `[Resync]`).
- **Text:** `char` arrays are Latin-1 and shown as UTF-8; NUL bytes inside dynamic strings are shown as `\0`.

## Decode problems
Every problem is expert info whose text starts with the status the generated C++ `deserialize` and the reference
codec return. The problems are:
- `[Truncated]`
- `[OutOfRange]`
- `[ConstMismatch]`
- `[CountExceedsMax]`
- `[ChecksumMismatch]`
- `[InvalidLength]`
- `[TrailingBytes]`
- `[UnknownMessageId]`
- `[InvalidState]`

All of them have severity *Error*, so `_ws.expert.severity == error` finds bad frames. Unlike the codec, the dissector
keeps going after a bad value, so one frame can show several problems. When `range_check.decode` is off for a
message, a value outside its range is only a *Note*, the same as the codec, which accepts it.

## Checksums
CRCs (`crc32` and parametric `crc` up to 64 bits) are verified, and a correct value is marked `[correct]`.

A custom checksum is verified only when another Lua plugin provides it. Wireshark runs every plugin in its own
environment, so the hook must be set on `_G`:
```lua
_G.wireforge_checksums = _G.wireforge_checksums or {}
_G.wireforge_checksums["vendor::SumChecksum"] = function(bytes)  -- bytes: Lua string
  local sum = 0
  for i = 1, #bytes do sum = (sum + bytes:byte(i)) % 65536 end
  return sum
end
```
Without a hook the field gets a *Note* `Checksum not verified`. The MAVLink 2 model brings its hook
(`protocols/mavlink2/wireshark/mav2sum.lua`).

## Limits
- There is no heuristic detection: bind a protocol by port, user DLT or Decode As.
- Over UDP, a frame that does not fit the datagram is `[Truncated]`; frames are never reassembled across datagrams.
- Wireshark 3.6 is the oldest version the plugin is tested with. Older versions are not tested.

## Tests
`tests/python/test_wireshark.py` checks the plugin against the reference codec in real tshark, for every example
project and `tests/python/wireshark/project.yaml`. That project covers bit groups wider than 32 bits, 64-bit ranges,
count expressions, CRC-64, fixed framing and frame-counted lengths. Each profile and interface is tested three ways:
- Golden frames must show exactly the decoded values.
- Every frame with one byte changed (three changes per byte), cut short or extended must be flagged exactly when the
  codec rejects it. With framing `none`, the codec's status must be among the reported problems.
- Frames split over TCP segments, with garbage between frames on resync interfaces, must all be dissected.

`quality/run_wireshark.sh` runs the tests against tshark 3.6 (Ubuntu 22.04), 4.2 (24.04) and 4.6 (26.04, Lua 5.4).
`quality/fetch_tshark.sh <release>` downloads each tshark from the signed Ubuntu archive without root and runs it with
its own C library. Without tshark (`$WIREFORGE_TSHARK`, the `PATH` or `build/tshark-jammy`), the tshark tests are
skipped; the CI job `wireshark` always runs them.

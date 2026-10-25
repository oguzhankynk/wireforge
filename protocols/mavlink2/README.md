# MAVLink 2: the frame and six messages of the common message set

Source: the public MAVLink serialization rules (the MAVLink 2 frame, the field order with extension fields, the
removal of trailing zero bytes of a payload, the checksum with the CRC_EXTRA byte) and the definitions of the common
message set. The model is written from those descriptions; no text or table of them is copied.

Covered: the MAVLink 2 frame (start byte 0xFD, incompatibility and compatibility flags, the 24-bit message id) and the
messages HEARTBEAT, GPS_RAW_INT (with its six extension fields), ATTITUDE, GLOBAL_POSITION_INT (the angles have
`scale`), COMMAND_LONG and COMMAND_ACK (with its four extension fields).

Checked against an independent implementation: `pymavlink` (its MAVLink 2 dialect) packs the same messages to the same
frames, including the frames whose trailing zero bytes are left out and the checksum with the CRC_EXTRA byte, reads the
frames the model writes as the same field values, and refuses a broken checksum like the model does
(`tests/python/test_mavlink2.py`). The Wireshark dissector generated from the model shows the same values
(`tests/python/test_wireshark.py`).

How the model says what MAVLink 2 does:
- The base fields of a message are listed in the order they are sent: the largest type first, as the serialization
  rules reorder them. The extension fields follow in their declared order.
- `trim_trailing_zeros: true` on the interface: a sender leaves out the zero bytes at the end of a payload (the first
  byte always stays), so a frame is often shorter than its message, and a receiver reads the missing bytes as zeros.
  This is also how extension fields stay compatible: a sender that does not know them sends a shorter payload.
- The component id and the message id share one little-endian 32-bit bit group: the component id is the low byte, the
  three-byte message id follows. Generated code reads and writes them as fields of one and three bytes.
- The checksum is CRC-16/MCRF4XX over the frame without the start byte, followed by the CRC_EXTRA byte of the message.
  `mav2sum::Crc` (`app/include/mav2sum/crc.hpp`, `app/include/mav2sum/crc.h` for C, `plugins/mavlink2_crc.py`) skips the
  start byte, keeps the message id when it passes by and adds the CRC_EXTRA byte of that message last. The CRC_EXTRA
  bytes are one list, `app/include/mav2sum/crc_extra.h`, that the C, C++ and Python code and the Wireshark hook all
  read: a new message needs its line there only.
- `incompatFlags` is the constant 0, so a signed frame (flag 0x01) is refused with `ConstMismatch`.

Wireshark: `wireforge wireshark <project> -o mavlink2.lua` writes the dissector; load `wireshark/mav2sum.lua` from the
project as another Lua plugin and the dissector also verifies the checksum. The hook finds the CRC_EXTRA list from its
place in the project; when you copy it elsewhere, copy `crc_extra.h` next to it.

Not covered: message signing (signed frames are refused) and the other messages of the common set; for all of them,
or for ArduPilot's or your own dialect, `wireforge import-mavlink <dialect.xml>` writes a project with the same frame
(docs/IMPORT.md). A
payload longer than its message in this model (extension fields that a newer message definition added) is refused with
`TrailingBytes`; add the new fields at the end of the message to read them.

MAVLink is a project of the Dronecode Foundation; this project is not affiliated with or endorsed by it.

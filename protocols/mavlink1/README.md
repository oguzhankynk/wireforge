# MAVLink 1: the frame and four messages of the common message set

Source: the public MAVLink serialization rules (the frame, the field order, the checksum with the CRC_EXTRA byte) and
the definitions of the common message set. The model is written from those descriptions; no text or table of them is
copied.

Covered: the MAVLink 1 frame and the messages HEARTBEAT, ATTITUDE, GLOBAL_POSITION_INT (the angles have `scale`) and
COMMAND_LONG.

Checked against an independent implementation: `pymavlink` (its MAVLink 1 dialect) packs the same messages to the same
frames, including the checksum and the CRC_EXTRA byte, reads the frames the model writes as the same field values, and
refuses a broken checksum like the model does (`tests/python/test_mavlink.py`).

How the model says what MAVLink does:
- The fields of a message are listed in the order they are sent: the largest type first, as the serialization rules
  reorder them.
- The checksum is CRC-16/MCRF4XX over the frame without the start byte, followed by the CRC_EXTRA byte of the message.
  `mavsum::Crc` (`app/include/mavsum/crc.hpp`, `plugins/mavlink_crc.py`) skips the start byte, keeps the message id
  when it passes by (it is the sixth byte of the frame) and adds the CRC_EXTRA byte of that message last. The table of
  the CRC_EXTRA bytes has the four messages of the model; a new message needs its byte in both files.

Not covered: MAVLink 2, which has a model of its own (`protocols/mavlink2`), the other messages, arrays and strings.

MAVLink is a project of the Dronecode Foundation; this project is not affiliated with or endorsed by it.

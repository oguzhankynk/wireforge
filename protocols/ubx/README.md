# u-blox UBX: the frame and five messages of the binary protocol of u-blox GNSS receivers

Source: the public u-blox receiver descriptions (the frame structure, the 8-bit Fletcher checksum and the fields of
the messages). The model is written from those documents; no text or table of them is copied.

Covered: the frame (sync characters, class, message id, length, payload, checksum) and the messages ACK-ACK, ACK-NAK,
CFG-RATE, NAV-PVT and MON-VER. The vector is the frame that sets one measurement per second, which is used as the
example of the checksum. NAV-PVT has `scale` on the angles, so `wireforge decode` shows degrees as well as the raw
values.

Checked against an independent implementation: `pyubx2` builds the same frames from the same payloads, reads the flags,
scaled angles and strings of the payloads as the model does, and refuses a broken checksum like the model does
(`tests/python/test_ubx.py`).

How the model says what UBX does:
- The message id on the wire is the class byte and then the id byte. The interface is little endian, so the id field
  reads as `class | id << 8`: NAV-PVT (class 0x01, id 0x07) is 0x0701.
- The checksum covers class, id, length and payload, not the two sync characters. `ubxsum::Fletcher8`
  (`app/include/ubx/fletcher8.hpp`, `plugins/ubx_fletcher.py`) skips the first two bytes it is given.

Not covered: the other messages and the configuration key/value messages; the poll requests (a message with the same
class and id and no payload cannot be told apart from the message itself on one interface).

UBX is a protocol of u-blox AG; this project is not affiliated with or endorsed by it.

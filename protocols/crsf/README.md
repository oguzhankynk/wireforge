# CRSF: the frame and three telemetry messages of the serial link between a radio receiver and a flight controller

Source: the public CRSF protocol descriptions (the frame structure, the CRC-8 and the telemetry payloads). The model is
written from those descriptions; no text or table of them is copied.

Covered: the frame (sync byte, length, type, payload, CRC-8 with the polynomial 0xD5) and the messages HEARTBEAT,
BATTERY_SENSOR (voltage and current with `scale`) and LINK_STATISTICS.

Checked against an independent implementation: `crsf-parser` builds the same frames from the same values, reads the
frames the model writes as the same values and finds the same CRC error (`tests/python/test_crsf.py`).

How the model says what CRSF does:
- The length counts the type byte, which comes after it, so no header field can dispatch on the type (the same limit as
  the length of Modbus TCP). Each message is the payload of its own interface and has the type as its first field, a
  constant. `length_counts: payload_and_trailer` then counts the type, the payload and the CRC.

Not covered: the RC channels message (16 channels of 11 bits in 22 bytes: a bit group is limited to 64 bits, so it
cannot be written as bit-fields), the messages with a destination and an origin address (types above 0x27), GPS,
attitude and the other telemetry messages.

CRSF is a protocol of Team BlackSheep and the ExpressLRS project; this project is not affiliated with or endorsed by
them.

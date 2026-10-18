# Modbus RTU: four function codes as a client and a server see them

Source: "MODBUS Application Protocol Specification V1.1b3" (function codes, data bytes, exception responses) and
"MODBUS over Serial Line V1.02" (the frame and the CRC-16/MODBUS, sent low byte first). The model is written from
those documents; no text or table of them is copied.

Covered: read holding registers (03), write single register (06), the exception responses of both; one interface for
the client side (`to_server`) and one for the server side. The vectors in `vectors/` are the examples of the
specification, not produced by WireForge.

Checked against an independent implementation: `pymodbus` encodes the same requests and responses to the same bytes
and agrees on what a broken CRC is (`tests/python/test_independent.py`).

Not covered: RTU frames end at a silence of 3.5 character times, not at a length. Every read of the transport is one
frame here (`framing: none`), which is how a serial driver delivers them. The other function codes are not modelled.

Modbus is a trademark of Schneider Electric USA; this project is not affiliated with or endorsed by it.

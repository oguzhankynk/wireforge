# Modbus TCP: the MBAP header and the same function codes, one message per interface

Source: "MODBUS Application Protocol Specification V1.1b3" and "MODBUS Messaging on TCP/IP Implementation Guide
V1.0b" (the MBAP header: transaction identifier, protocol identifier 0, a length that counts the unit identifier and
the PDU, unit identifier). The model is written from those documents; no text or table of them is copied.

Covered: the read holding registers (03) request and response, one on each interface, with the vectors of the examples
of section 6.3 of the application protocol specification.

Not covered: the other function codes and the exception responses; the function code sits inside the bytes the length
counts, so a header-based dispatch by function code is not possible. The connection handling is the application's: the
TCP stream is delivered to the decoder, which finds the frames by their length.

Modbus is a trademark of Schneider Electric USA; this project is not affiliated with or endorsed by it.

# How WireForge compares

WireForge is for one situation: **the wire format is given** (by an ICD, a standard, a device or the other team) and you
need C++ or C code for it that does not allocate, plus evidence that the code does what the format says. The tools
below solve neighbouring problems; several of them are the better choice when your situation is different.

| Tool | Who decides the wire format | Generates | Fits best when |
|------|-----------------------------|-----------|----------------|
| **WireForge** | you describe an existing format, field by field | C++17 or C99 encoders, decoders, validation, framing and dispatch; a GoogleTest project with coverage gates; a Wireshark dissector; layout reports and lifecycle documents | the format is fixed by someone else, the target is embedded C or C++, and the code must be proven, not only written |
| Protocol Buffers | protobuf (tag, varint, length) | many languages | both ends are yours and you want schema evolution across languages |
| nanopb | protobuf | C for microcontrollers, static allocation | as protobuf, on small targets |
| FlatBuffers, Cap'n Proto | the library (zero-copy layout) | many languages | large messages read in place, both ends yours |
| Kaitai Struct | you describe an existing format | parsers in many languages (serialization is limited) | reading and exploring existing binary formats and files |
| mavgen (MAVLink) | MAVLink | complete MAVLink libraries from the XML dialects | you need message signing, the standard microservices or languages other than C and C++ |
| cantools | CAN databases (DBC) | Python encoding and decoding, C source for signals | CAN analysis and tooling in Python |
| ASN.1 compilers | ASN.1 encoding rules (BER, PER, ...) | many languages | the protocol is specified in ASN.1 |
| Packed structs and `memcpy` | the compiler | nothing | never for a wire format: byte order, alignment and padding differ between targets |

## In more detail

**Protocol Buffers, nanopb, FlatBuffers.** These define their own encoding. They cannot express a format that someone
else already fixed (a sync word, a 3-byte id, a CRC over the header and payload, bit-fields packed LSB first), and
they are not meant to. If both ends are yours and you can choose the format, they are mature and well supported in many
languages, which WireForge is not: it generates C++17 and C99 only.

**Kaitai Struct** is the closest in spirit: it also describes an existing format field by field, and it targets many
languages. It focuses on parsing; WireForge generates both directions, the framing of a byte stream, the dispatch of
messages to handlers, and the tests that check the generated code against an independent reference codec. Kaitai is
the better tool for reverse engineering file formats and for languages other than C and C++.

**mavgen and the official MAVLink libraries.** They are the reference: message signing, the MAVLink microservices
and many languages. WireForge reads the same XML dialects (`wireforge import-mavlink common.xml`, every message,
checked byte for byte against pymavlink) and generates allocation-free C++17 or C99 with its own tests, coverage gates,
a Wireshark dissector and traceability; it refuses signed frames. It fits when MAVLink is one of several protocols of a
system that WireForge already describes, or when the generated code must come with evidence.

**cantools** imports CAN databases and encodes and decodes in Python. WireForge imports the same DBC files
(`wireforge import-dbc`) and generates C or C++ for the target, with the same tests as any other model.

**Hand-written code** is how most of these links are implemented today. It works until a field moves, a target has
a different byte order, or nobody remembers which side changed. The point of a model is that the code, the tests,
the dissector and the documents change together, and `wireforge compat` tells whether a change breaks the other side.

## What WireForge does not do
- Languages other than C++17 and C99 (Python has the reference codec, not generated code).
- Self-describing or schema-evolving encodings: there are no tags on the wire unless your format has them.
- Encryption, authentication or message signing.
- Production use without a commercial license (development, evaluation and CI are free; the generated code and its
  runtime are yours under MIT). See the README.

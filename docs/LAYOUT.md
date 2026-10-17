# Layout analyzer and ICD export

`python -m wireforge.layout <project> [--profile P] [--format markdown|csv|json] [-o FILE]` validates the project
and reports, per profile (or once without profiles):

- **Messages:** minimum and maximum encoded size, and one row per field: byte offset (a range after variable-size
  fields), size range, bit positions inside the bit-group container (bit 0 = least significant bit, following the
  message's `bit_order`), occurrence (`3`, `0..5, count trackCount`, `fill, 0..16`), range, default/const, role
  (`const`, `reserved`, `checksum <name>`, header roles), `if:` condition, units and description. Fields of nested
  structs are flattened (`accel.x`); fields of array elements are shown for the first element (`tracks[].id`).
- **Interfaces:** header and trailer field tables, effective message ids (per-interface overrides applied), payload and
  frame size ranges, the largest payload, the frame buffer size (`frame_size`/`max_frame_size` or header + largest
  payload + trailer) and the RAM of the generated link: receive ring (`buffers.rx_bytes`, default twice the frame
  buffer) + receive frame + transmit frame buffer. The per-profile total is the sum over all interfaces.

Excluded from the RAM budget: the decoded message object on the receiving stack (its size depends on the compiler),
decoder/dispatcher bookkeeping (a few words) and the transport's own buffers.

Exports: Markdown is an ICD document (one section per profile, interface and message), CSV has one row per field of
every message, header and trailer (`profile, owner, path, type, offset_min, offset_max, size_min, size_max, bit_high,
bit_low, group_size, occurs, range, default, role, condition, units, description`), JSON is the full report
(`wireforge.layout.analyze(project).to_dict()`).

The numbers equal the generated code: the generated test project checks `min_wire_size()`, `max_wire_size()`,
`kHeaderSize`, `kTrailerSize`, `max_payload_size()`, `kMaxFrameSize`, `kRxBufferBytes` and `kTxBufferBytes` of every
message and interface against the analyzer with `static_assert`s, for every profile.

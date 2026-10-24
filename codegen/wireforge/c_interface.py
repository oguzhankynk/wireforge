"""C99 code of one interface: ids, encodings, frame encoders, handler, dispatcher and link.

The rules are those of the C++ code (`cpp_interface.py`) and the C++ runtime (`frame_decoder.hpp`,
`dispatcher.hpp`, `link.hpp`); what differs is the shape. A handler is a struct of callbacks (a
null callback means "decode and forward", or "ignore" when there is no callback for the decoded
message either), the dispatcher is one function, the link owns its buffers and is set up with
`<prefix>_<interface>_link_init()`.

Supported subset: header fields are byte-aligned integers (no occurs or `if:`; a bit-field only of
whole bytes in a header whose bit order follows its byte order, see `narrow_field_ok`); the trailer
holds at most one checksum plus constants; the header holds no checksum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wireforge.c_model import CCodec, CModel, encoding_initializer
from wireforge.cpp import Writer, guard
from wireforge.cpp_interface import (
    InterfaceError,
    checks_trailer,
    header_is_written,
    is_automatic,
    is_checked_constant,
    narrow_field_ok,
    needs_frame_check,
)
from wireforge.model import BUILTIN_BITS, INTEGER_TYPES, Field, Interface, Project

if TYPE_CHECKING:
    from wireforge.generate import Banner

BYTE_BITS = 8
FRAMING_CONSTANTS = {
    "fixed": "WF_FRAMING_FIXED",
    "sync_length": "WF_FRAMING_SYNC_LENGTH",
    "length_prefixed": "WF_FRAMING_LENGTH_PREFIXED",
}
LENGTH_CONSTANTS = {
    "payload": "WF_LENGTH_PAYLOAD",
    "payload_and_trailer": "WF_LENGTH_PAYLOAD_AND_TRAILER",
    "frame": "WF_LENGTH_FRAME",
}
LENGTH_VALUES = {
    "payload": "payload_size",
    "payload_and_trailer": "payload_size + {ip}_TRAILER_SIZE",
    "frame": "total",
}


@dataclass(frozen=True)
class _Placed:
    """A header or trailer field with its byte offset and width."""

    field: Field
    offset: int
    width: int


@dataclass(frozen=True)
class _Entry:
    """One message of the interface."""

    name: str
    identifier: int | None
    condition: str | None


def _doc(text: str) -> str:
    return f"/** {text} */"


class CInterface:
    """Writes `<project>/interfaces/<interface>.h` and `.c`."""

    def __init__(
        self, project: Project, interface: Interface, banner: Banner, model: CModel
    ) -> None:
        """Check the supported subset and prepare the field layout."""
        self.project = project
        self.interface = interface
        self.banner = banner
        self.model = model
        self.names = model.names
        self.codec = CCodec(model)
        self.ip = f"{self.names.prefix}_{interface.name}"
        self.header = self._place(interface.header or (), "header")
        self.trailer = self._place(interface.trailer, "trailer")
        self._check_trailer()
        self.entries = [self._entry(m.name, m.id, m.condition) for m in interface.messages]
        self.out = Writer()

    # --- layout ---------------------------------------------------------------------------------

    def _entry(self, name: str, override: int | None, condition: str | None) -> _Entry:
        info = self.project.messages[name].message
        identifier = override if override is not None else (info.id if info else None)
        return _Entry(name, identifier, condition)

    def _place(self, fields: tuple[Field, ...], part: str) -> list[_Placed]:
        placed: list[_Placed] = []
        offset = 0
        for item in fields:
            base = self.names.base(item.type)
            scalar = base in INTEGER_TYPES or base == "bool"
            narrow = item.bits is not None and narrow_field_ok(self.project, self.interface, item)
            if (
                (item.bits is not None and not narrow)
                or item.occurs is not None
                or item.condition
                or not scalar
            ):
                message = (
                    f"interface {self.interface.name}: {part} field {item.name} must be a "
                    "byte-aligned integer without occurs or if: (bits only in whole bytes)"
                )
                raise InterfaceError(message)
            width = (item.bits or BUILTIN_BITS[base]) // BYTE_BITS
            placed.append(_Placed(item, offset, width))
            offset += width
        return placed

    def _check_trailer(self) -> None:
        sums = [p for p in self.trailer if p.field.checksum is not None]
        others = [p for p in self.trailer if p.field.checksum is None and p.field.const is None]
        if len(sums) > 1 or others or any(p.field.checksum for p in self.header):
            message = (
                f"interface {self.interface.name}: the trailer may hold one checksum and consts "
                "only, and the header no checksum"
            )
            raise InterfaceError(message)

    def _role(self, name: str) -> _Placed | None:
        return next((item for item in self.header if item.field.role == name), None)

    def _trailer_checksum(self) -> _Placed | None:
        return next((p for p in self.trailer if p.field.checksum is not None), None)

    def _endian(self) -> str:
        endian = self.interface.endian or self.project.defaults.get("endian") or "big"
        return "WF_BIG_ENDIAN" if endian == "big" else "WF_LITTLE_ENDIAN"

    def _literal(self, item: Field) -> str:
        """Value written for a const or protocol_version field."""
        if item.const is not None:
            return self.names.literal(item, item.const)
        return f"{self.interface.version.split('.')[0]}U"

    def _lines(self, lines: list[str], condition: str | None = None) -> None:
        if condition is not None:
            self.out.line(f"#if {self.names.condition(condition)}")
        for text in lines:
            self.out.line(text)
        if condition is not None:
            self.out.line("#endif")

    def _id_type(self) -> str:
        id_field = self._role("message_id")
        return self.names.scalar(id_field.field.type) if id_field else "uint32_t"

    def _overhead(self) -> bool:
        return bool(self.header or self.trailer)

    def _framed(self) -> bool:
        return self.interface.framing.kind != "none"

    def _handler_type(self) -> str:
        return f"{self.ip}_handler"

    def _type(self, entry: _Entry) -> str:
        return self.names.tname(entry.name)

    # --- header ---------------------------------------------------------------------------------

    def render_header(self) -> str:
        """Return the text of the interface header."""
        project, iface = self.project, self.interface
        self.out = Writer()
        out = self.out
        name_guard = guard(project.name, iface.name, "H")
        self._lines(
            [
                "/**",
                f" * @file {project.name}/interfaces/{iface.name}.h",
                f" * @brief Interface {iface.name} {iface.version} ({iface.direction}), C99.",
                " *",
                *self.banner.lines(),
                " */",
                f"#ifndef {name_guard}",
                f"#define {name_guard}",
                "",
                f'#include "{project.name}/model.h"',
                "",
                '#include "wireforge/c/expr.h"',
                '#include "wireforge/c/link.h"',
                "",
            ]
        )
        self._header_constants()
        self._header_sizes()
        self._header_struct()
        self._header_functions()
        self._header_handler()
        self._header_link()
        self._lines([f"#endif /* {name_guard} */"])
        return out.text()

    def _header_constants(self) -> None:
        ip = self.ip
        major, minor, patch = self.interface.version.split(".")
        header_size = sum(item.width for item in self.header)
        trailer_size = sum(item.width for item in self.trailer)
        self._lines(
            [
                f"#define {ip}_VERSION_MAJOR {major}U {_doc('ICD major version.')}",
                f"#define {ip}_VERSION_MINOR {minor}U {_doc('ICD minor version.')}",
                f"#define {ip}_VERSION_PATCH {patch}U {_doc('ICD patch version.')}",
                f"#define {ip}_HEADER_SIZE {header_size}U {_doc('Header bytes.')}",
                f"#define {ip}_TRAILER_SIZE {trailer_size}U {_doc('Trailer bytes.')}",
                f"#define {ip}_MESSAGE_COUNT {max(len(self.entries), 1)}U",
                "",
                _doc("Type of the message id field."),
                f"typedef {self._id_type()} {ip}_MessageId;",
                "",
            ]
        )
        for entry in self.entries:
            lines = [_doc(f"Encoding of {entry.name} on this interface.")]
            lines.append(f"extern const struct wf_encoding {ip}_{entry.name}_encoding;")
            if entry.identifier is not None:
                lines += [
                    _doc(f"Message id of {entry.name} on this interface."),
                    f"#define {ip}_{entry.name}_ID (({ip}_MessageId)({entry.identifier}U))",
                ]
            self._lines(lines, entry.condition)
        self._lines(
            ["", _doc("Encoding of the header and trailer fields."),
             f"extern const struct wf_encoding {ip}_frame_encoding;", ""]
        )  # fmt: skip

    def _header_sizes(self) -> None:
        ip, iface = self.ip, self.interface
        framing = iface.framing
        previous = "0U"
        out = self.out
        for index, entry in enumerate(self.entries):
            step = f"{ip}_PAYLOAD_{index}"
            size = f"{self._type(entry)}_MAX_WIRE_SIZE"
            if entry.condition is not None:
                out.line(f"#if {self.names.condition(entry.condition)}")
                out.line(f"#define {step} WF_MAX_SIZE({previous}, {size})")
                out.line("#else")
                out.line(f"#define {step} ({previous})")
                out.line("#endif")
            else:
                out.line(f"#define {step} WF_MAX_SIZE({previous}, {size})")
            previous = step
        limit = framing.frame_size if framing.kind == "fixed" else framing.max_frame_size
        frame = (
            f"({ip}_HEADER_SIZE + {ip}_MAX_PAYLOAD_SIZE + {ip}_TRAILER_SIZE)"
            if limit is None
            else self.names.capacity(limit)
        )
        buffers = iface.buffers
        rx = self.names.capacity(buffers["rx_bytes"]) if "rx_bytes" in buffers else None
        tx = self.names.capacity(buffers["tx_bytes"]) if "tx_bytes" in buffers else None
        self._lines(
            [
                _doc("Largest payload of any message of this interface."),
                f"#define {ip}_MAX_PAYLOAD_SIZE ((size_t)({previous}))",
                _doc("Largest frame (size of the frame buffers)."),
                f"#define {ip}_MAX_FRAME_SIZE ((size_t)({frame}))",
                _doc("Size of the receive ring buffer."),
                f"#define {ip}_RX_BUFFER_BYTES ((size_t)({rx or f'2U * {ip}_MAX_FRAME_SIZE'}))",
                _doc("Size of the transmit buffer."),
                f"#define {ip}_TX_BUFFER_BYTES ((size_t)({tx or f'{ip}_MAX_FRAME_SIZE'}))",
                (
                    f"typedef char {ip}_buffers_hold_a_frame[({ip}_RX_BUFFER_BYTES >= "
                    f"{ip}_MAX_FRAME_SIZE && {ip}_TX_BUFFER_BYTES >= {ip}_MAX_FRAME_SIZE) "
                    "? 1 : -1];"
                ),
                "",
            ]
        )

    def _header_struct(self) -> None:
        ip, names = self.ip, self.names
        lines = [_doc("Header fields set by the sender (ids, lengths, constants are automatic)."),
                 f"typedef struct {ip}_header {{"]  # fmt: skip
        members = [p.field for p in self.header if not is_automatic(p.field)]
        if not members:
            lines.append("    uint8_t unused_; /**< Placeholder: a C struct needs a member. */")
        for item in members:
            doc = item.description or item.name
            lines.append(f"    {names.scalar(item.type)} {item.name}; /**< {doc} */")
        lines += [f"}} {ip}_header;", "", _doc("Sets the header fields to their defaults."),
                  f"void {ip}_header_init({ip}_header *header);", ""]  # fmt: skip
        self._lines(lines)

    def _header_functions(self) -> None:
        ip = self.ip
        lines = [
            _doc(
                "Reads and checks the header of a complete frame: WF_CONST_MISMATCH for a wrong "
                "sync, constant or protocol version, WF_TRUNCATED for a short frame."
            ),
            (
                f"wf_status {ip}_decode_header(const uint8_t *frame, size_t size, "
                f"{ip}_header *header);"
            ),
            "",
        ]
        self._lines(lines)
        for entry in self.entries:
            self._lines(
                [
                    _doc(f"Encodes a complete {entry.name} frame (header, payload, trailer)."),
                    (
                        f"wf_status {ip}_encode_{entry.name}(const {self._type(entry)} *message, "
                        f"const {ip}_header *header,"
                    ),
                    (
                        "                                  uint8_t *buffer, size_t capacity, "
                        "size_t *size);"
                    ),
                    "",
                ],
                entry.condition,
            )

    def _header_handler(self) -> None:
        ip = self.ip
        lines = [
            _doc(
                "Receives the messages of this interface, decoded or as raw bytes. A callback may "
                "be null: a null raw callback decodes the payload and calls the received callback "
                "(the default), a null received callback ignores the message."
            ),
            f"typedef struct {ip}_handler {{",
            "    void *context; /**< Passed to every callback. */",
        ]
        self._lines(lines)
        for entry in self.entries:
            self._lines(
                [
                    (
                        f"    /** Called for every {entry.name} frame before its payload is "
                        "decoded; "
                        "@p raw is valid only during the call. */"
                    ),
                    (
                        f"    wf_status (*on_{entry.name}_raw)(void *context, "
                        "const wf_raw_message *raw);"
                    ),
                    f"    /** Called for every valid {entry.name} received on this interface. */",
                    (
                        f"    void (*on_{entry.name}_received)(void *context, "
                        f"const {self._type(entry)} *message);"
                    ),
                ],
                entry.condition,
            )
        self._lines([f"}} {ip}_handler;", ""])
        for entry in self.entries:
            self._lines(
                [
                    _doc(
                        f"The default of on_{entry.name}_raw: decodes the payload and calls "
                        f"on_{entry.name}_received when it is valid."
                    ),
                    (
                        f"wf_status {ip}_{entry.name}_default_raw(const {ip}_handler *handler, "
                        "const wf_raw_message *raw);"
                    ),
                    "",
                ],
                entry.condition,
            )
        self._lines(
            [
                _doc(
                    "Checks a complete frame, finds the handler of its message id and calls it; "
                    "WF_UNKNOWN_MESSAGE_ID for an id of no message of the interface."
                ),
                (
                    f"wf_status {ip}_dispatch_frame(const {ip}_handler *handler, "
                    "const uint8_t *frame, size_t size);"
                ),
                "",
            ]
        )

    def _header_link(self) -> None:
        ip = self.ip
        self._lines(
            [
                _doc(
                    "A link of this interface with its buffers: a transport, a handler, a decoder."
                ),
                f"typedef struct {ip}_link {{",
                "    wf_link link;                                 /**< Generic part. */",
                f"    uint8_t rx_storage[{ip}_RX_BUFFER_BYTES]; /**< Received bytes. */",
                f"    uint8_t rx_frame[{ip}_MAX_FRAME_SIZE];   /**< One received frame. */",
                f"    uint8_t tx_frame[{ip}_MAX_FRAME_SIZE];   /**< One frame to send. */",
                f"}} {ip}_link;",
                "",
                _doc("Sets up a link over @p transport; both it and @p handler must outlive it."),
                (
                    f"void {ip}_link_init({ip}_link *link, const wf_transport *transport, "
                    f"const {ip}_handler *handler);"
                ),
                _doc("Reads everything the transport has and dispatches every complete frame."),
                f"wf_poll_result {ip}_link_poll({ip}_link *link);",
                _doc("Sends an already encoded frame."),
                (
                    f"wf_status {ip}_link_send_frame({ip}_link *link, const uint8_t *frame, "
                    "size_t size);"
                ),
                "",
            ]
        )
        for entry in self.entries:
            self._lines(
                [
                    _doc(f"Encodes a {entry.name} frame and sends it."),
                    (
                        f"wf_status {ip}_link_send_{entry.name}({ip}_link *link, "
                        f"const {self._type(entry)} *message, const {ip}_header *header);"
                    ),
                    "",
                ],
                entry.condition,
            )

    # --- source ---------------------------------------------------------------------------------

    def render_source(self) -> str:
        """Return the text of the interface source file."""
        project, iface = self.project, self.interface
        self.out = Writer()
        self._lines(
            [
                "/**",
                f" * @file {project.name}/interfaces/{iface.name}.c",
                f" * @brief Frames, dispatch and link of interface {iface.name} (C99).",
                " *",
                *self.banner.lines(),
                " */",
                f'#include "{project.name}/interfaces/{iface.name}.h"',
                "",
                '#include "wireforge/c/wireforge.h"',
                "#include <string.h>",
                "",
            ]
        )
        self._source_encodings()
        self._source_header_init()
        self._source_frame_writer()
        self._source_encoders()
        self._source_header_decoder()
        self._source_frame_check()
        self._source_receivers()
        self._source_dispatch()
        self._source_link()
        return self.out.text()

    def _source_encodings(self) -> None:
        ip = self.ip
        for entry in self.entries:
            item = self.project.messages[entry.name]
            init = encoding_initializer(self.project, item, self.interface)
            self._lines(
                [f"const wf_encoding {ip}_{entry.name}_encoding = {init};"], entry.condition
            )
        self._lines(
            [
                (
                    f"const wf_encoding {ip}_frame_encoding = "
                    f"{{{self._endian()}, WF_MSB_FIRST, true, true, false}};"
                ),
                "",
            ]
        )

    def _source_header_init(self) -> None:
        ip, names = self.ip, self.names
        lines = [f"void {ip}_header_init({ip}_header *header)", "{",
                 "    (void)memset(header, 0, sizeof(*header));"]  # fmt: skip
        for placed in self.header:
            item = placed.field
            if is_automatic(item):
                continue
            if item.default is not None and not isinstance(item.default, list):
                lines.append(f"    header->{item.name} = {names.literal(item, item.default)};")
        lines += ["}", ""]
        self._lines(lines)

    # --- transmit side --------------------------------------------------------------------------

    def _header_write(self, item: Field) -> str:
        ip = self.ip
        if item.bits is not None:
            value = "message_id" if item.role == "message_id" else f"header->{item.name}"
            return f"wf_enc_unsigned(&head, (uint64_t)({value}), {item.bits // BYTE_BITS}U);"
        if item.role == "message_id":
            return self.codec.write_statement(item, "message_id", "&head")
        if item.role == "payload_length":
            length = LENGTH_VALUES[self.interface.framing.length_counts].replace("{ip}", ip)
            base = self.names.base(item.type)
            limit = self.codec.count_limit(base)
            return f"wf_enc_count(&head, {length}, {limit}, {self.codec.width(item)}U);"
        if item.role == "protocol_version" or item.const is not None:
            return self.codec.write_statement(item, self._literal(item), "&head")
        return self.codec.write_statement(item, f"header->{item.name}", "&head")

    def _trailer_write(self, item: Field) -> str:
        ip = self.ip
        use = item.checksum
        if use is None:
            return self.codec.write_statement(item, self._literal(item), "&tail")
        function = self.names.checksum_function(use.use)
        if use.scope == "payload":
            covered = f"{function}(&buffer[{ip}_HEADER_SIZE], payload_size)"
        else:
            covered = f"{function}(buffer, {ip}_HEADER_SIZE + payload_size)"
        return self.codec.write_statement(item, covered, "&tail")

    def _trailer_lines(self) -> list[str]:
        ip = self.ip
        if not self.trailer:
            return []
        return [
            "wf_encoder tail;",
            (
                f"wf_encoder_init(&tail, &buffer[{ip}_HEADER_SIZE + payload_size], "
                f"{ip}_TRAILER_SIZE, "
                f"&{ip}_frame_encoding);"
            ),
            *[self._trailer_write(placed.field) for placed in self.trailer],
            "wf_enc_check(&head, tail.status);",
        ]

    def _size_check(self) -> list[str]:
        framing = self.interface.framing
        ip = self.ip
        if framing.kind == "fixed":
            return [(f"wf_enc_check(&head, wf_status_if(total == {ip}_MAX_FRAME_SIZE, "
                    "WF_INVALID_LENGTH));")]  # fmt: skip
        if framing.max_frame_size is not None:
            return [(f"wf_enc_check(&head, wf_status_if(total <= {ip}_MAX_FRAME_SIZE, "
                    "WF_INVALID_LENGTH));")]  # fmt: skip
        return []

    def _source_frame_writer(self) -> None:
        ip = self.ip
        size_check = self._size_check()
        plain = not self.header and not self.trailer and not size_check
        body = ["*size = 0U;", "(void)header;", "(void)message_id;"]
        if self._overhead():
            body += [
                f"if (capacity < {ip}_HEADER_SIZE + {ip}_TRAILER_SIZE) {{",
                "    return WF_BUFFER_TOO_SMALL;",
                "}",
            ]
        body += [
            "size_t payload_size = 0U;",
            f"const size_t room = capacity - {ip}_HEADER_SIZE - {ip}_TRAILER_SIZE;",
            (
                f"const wf_status status = payload(message, &buffer[{ip}_HEADER_SIZE], room, "
                "&payload_size);"
            ),
            "if (status != WF_OK) {",
            "    return status;",
            "}",
        ]
        if self.interface.trim_trailing_zeros:
            # The trailing zero bytes of the payload are not sent.
            body += [f"payload_size = wf_trimmed_size(&buffer[{ip}_HEADER_SIZE], payload_size);"]
        body += [
            f"const size_t total = {ip}_HEADER_SIZE + payload_size + {ip}_TRAILER_SIZE;",
        ]
        if plain:
            body += ["*size = total;", "return WF_OK;"]
        else:
            body += [
                "wf_encoder head;",
                f"wf_encoder_init(&head, buffer, {ip}_HEADER_SIZE, &{ip}_frame_encoding);",
                *[self._header_write(placed.field) for placed in self.header],
                *self._trailer_lines(),
                *size_check,
                "*size = wf_size_if_ok(head.status, total);",
                "return head.status;",
            ]
        self._lines(
            [
                (
                    f"typedef wf_status (*{ip}_payload_fn)(const void *message, uint8_t *buffer, "
                    "size_t capacity, size_t *size);"
                ),
                "",
                (
                    f"static wf_status {ip}_write_frame(const {ip}_header *header, "
                    f"{ip}_MessageId message_id,"
                ),
                "                                  uint8_t *buffer, size_t capacity, size_t *size,",
                f"                                  {ip}_payload_fn payload, const void *message)",
                "{",
                *[f"    {line}" for line in body],
                "}",
                "",
            ]
        )

    def _source_encoders(self) -> None:
        ip = self.ip
        for entry in self.entries:
            name, type_name = entry.name, self._type(entry)
            serialize = self.names.function(name, "serialize_with")
            identifier = f"{ip}_{name}_ID" if entry.identifier is not None else "0U"
            self._lines(
                [
                    (
                        f"static wf_status {ip}_serialize_{name}(const void *message, "
                        "uint8_t *buffer, "
                        "size_t capacity, size_t *size)"
                    ),
                    "{",
                    (
                        f"    return {serialize}((const {type_name} *)message, buffer, "
                        "capacity, size, "
                        f"&{ip}_{name}_encoding);"
                    ),
                    "}",
                    "",
                    (
                        f"wf_status {ip}_encode_{name}(const {type_name} *message, "
                        f"const {ip}_header *header,"
                    ),
                    (
                        "                                  uint8_t *buffer, size_t capacity, "
                        "size_t *size)"
                    ),
                    "{",
                    (
                        f"    return {ip}_write_frame(header, {identifier}, buffer, capacity, "
                        "size, "
                        f"{ip}_serialize_{name}, message);"
                    ),
                    "}",
                    "",
                ],
                entry.condition,
            )

    # --- receive side ---------------------------------------------------------------------------

    def _header_read(self, placed: _Placed) -> str:
        item = placed.field
        if item.role == "protocol_version" or item.const is not None:
            return self.codec.expect_statement(item, self._literal(item), "&decoder")
        if is_automatic(item):
            return f"wf_dec_skip(&decoder, {placed.width}U);"
        if item.bits is not None:
            ctype = self.names.scalar(item.type)
            return f"header->{item.name} = ({ctype})wf_dec_unsigned(&decoder, {placed.width}U);"
        return f"header->{item.name} = {self.codec.read_expression(item, '&decoder')};"

    def _source_header_decoder(self) -> None:
        ip = self.ip
        signature = (
            f"wf_status {ip}_decode_header(const uint8_t *frame, size_t size, {ip}_header *header)"
        )
        if not self._overhead():
            body = ["(void)frame;", "(void)size;", "(void)header;", "return WF_OK;"]
        else:
            if header_is_written(placed.field for placed in self.header):
                unused = []
            else:
                unused = ["(void)header;"]
            body = [
                *unused,
                f"const bool complete = size >= {ip}_HEADER_SIZE + {ip}_TRAILER_SIZE;",
                "wf_decoder decoder;",
                (
                    f"wf_decoder_init(&decoder, frame, size < {ip}_HEADER_SIZE ? size : "
                    f"{ip}_HEADER_SIZE, &{ip}_frame_encoding);"
                ),
                "wf_dec_check(&decoder, wf_status_if(complete, WF_TRUNCATED));",
                *[self._header_read(placed) for placed in self.header],
                "return decoder.status;",
            ]
        self._lines([signature, "{", *[f"    {line}" for line in body], "}", ""])

    def _checked_constants(self) -> list[_Placed]:
        framed = self._framed()
        return [p for p in self.header if is_checked_constant(p.field, framed=framed)]

    def _trailer_read(self, item: Field) -> list[str]:
        if item.checksum is None:
            return [self.codec.expect_statement(item, self._literal(item), "&tail")]
        return [f"received = (uint64_t){self.codec.read_expression(item, '&tail')};"]

    def _trailer_check(self, use_scope: str | None, name: str) -> list[str]:
        ip = self.ip
        function = self.names.checksum_function(name)
        if use_scope == "payload":
            covered = (
                f"{function}(&frame[{ip}_HEADER_SIZE], size - {ip}_HEADER_SIZE - {ip}_TRAILER_SIZE)"
            )
        else:
            covered = f"{function}(frame, size - {ip}_TRAILER_SIZE)"
        return [
            "wf_decoder tail;",
            (
                f"wf_decoder_init(&tail, &frame[size - {ip}_TRAILER_SIZE], {ip}_TRAILER_SIZE, "
                f"&{ip}_frame_encoding);"
            ),
            "uint64_t received = 0U;",
            *[line for placed in self.trailer for line in self._trailer_read(placed.field)],
            f"wf_dec_check(&tail, wf_status_if({covered} == received, WF_CHECKSUM_MISMATCH));",
            "return tail.status;",
        ]

    def _source_frame_check(self) -> None:
        """Check what the frame decoder leaves unchecked, before a message is handed over."""
        if not needs_frame_check(self.interface):
            return
        ip = self.ip
        checksum = self._trailer_checksum()
        use = checksum.field.checksum if checksum is not None else None
        trailer = checksum is not None and use is not None and checks_trailer(self.interface)
        body: list[str] = []
        if self._checked_constants():
            body.append(f"{ip}_header header;")
            body.append(f"{ip}_header_init(&header);")
            if trailer:
                body += [
                    f"const wf_status header_status = {ip}_decode_header(frame, size, &header);",
                    "if (header_status != WF_OK) {",
                    "    return header_status;",
                    "}",
                ]
            else:
                body.append(f"return {ip}_decode_header(frame, size, &header);")
        if checksum is not None and use is not None and trailer:
            body += self._trailer_check(use.scope, use.use)
        self._lines(
            [
                f"static wf_status {ip}_check_frame(const uint8_t *frame, size_t size)",
                "{",
                *[f"    {line}" for line in body],
                "}",
                "",
            ]
        )

    def _payload_lines(self, entry: _Entry) -> list[str]:
        """Extend a trimmed payload with the zeros that were not sent (trim_trailing_zeros)."""
        if not self.interface.trim_trailing_zeros:
            return []
        full = f"{self._type(entry)}_MAX_WIRE_SIZE"
        return [
            "    /* One byte more than the message, so that the array is never empty. */",
            f"    uint8_t padded[{full} + 1U];",
            "    size_t payload_size = 0U;",
            (
                "    const uint8_t *payload = wf_zero_extend(raw->payload, raw->payload_size, "
                f"padded, {full}, &payload_size);"
            ),
        ]

    def _source_receivers(self) -> None:
        ip = self.ip
        check = needs_frame_check(self.interface)
        for entry in self.entries:
            name, type_name = entry.name, self._type(entry)
            trimmed = self.interface.trim_trailing_zeros
            payload = "payload, payload_size" if trimmed else "raw->payload, raw->payload_size"
            deserialize = self.names.function(name, "deserialize_with")
            init = self.names.function(name, "init")
            lines = [
                (
                    f"wf_status {ip}_{name}_default_raw(const {ip}_handler *handler, "
                    "const wf_raw_message *raw)"
                ),
                "{",
                f"    {type_name} message;",
                f"    {init}(&message);",
                *self._payload_lines(entry),
                (
                    f"    const wf_status status = {deserialize}({payload}, "
                    f"&message, &{ip}_{name}_encoding);"
                ),
                f"    if (status == WF_OK && handler->on_{name}_received != NULL) {{",
                f"        handler->on_{name}_received(handler->context, &message);",
                "    }",
                "    return status;",
                "}",
                "",
                (
                    f"static wf_status {ip}_receive_{name}(const {ip}_handler *handler, "
                    "const wf_raw_message *raw)"
                ),
                "{",
            ]
            if check:
                lines += [
                    f"    const wf_status checked = {ip}_check_frame(raw->frame, raw->frame_size);",
                    "    if (checked != WF_OK) {",
                    "        return checked;",
                    "    }",
                ]
            lines += [
                f"    if (handler->on_{name}_raw != NULL) {{",
                f"        return handler->on_{name}_raw(handler->context, raw);",
                "    }",
                f"    return {ip}_{name}_default_raw(handler, raw);",
                "}",
                "",
            ]
            self._lines(lines, entry.condition)

    def _source_dispatch(self) -> None:
        ip = self.ip
        id_field = self._role("message_id")
        signature = (
            f"wf_status {ip}_dispatch_frame(const {ip}_handler *handler, "
            "const uint8_t *frame, size_t size)"
        )
        overhead = f"{ip}_HEADER_SIZE + {ip}_TRAILER_SIZE"
        raw = (
            f"const wf_raw_message raw = {{&frame[{ip}_HEADER_SIZE], size - ({overhead}), "
            "frame, size};"
        )
        lines: list[str] = []
        if self._overhead():
            lines += [f"if (size < {overhead}) {{", "    return WF_TRUNCATED;", "}"]
        if id_field is None:
            if not self.entries:
                lines += ["(void)handler;", "(void)frame;", "(void)size;", "return WF_OK;"]
            else:
                lines += [raw, f"return {ip}_receive_{self.entries[0].name}(handler, &raw);"]
            self._lines([signature, "{", *[f"    {line}" for line in lines], "}", ""])
            return
        lines += [
            (
                f"const uint64_t message_id = wf_load_unsigned(&frame[{id_field.offset}U], "
                f"{id_field.width}U, {self._endian()});"
            ),
            raw,
        ]
        self._lines([signature, "{", *[f"    {line}" for line in lines]])
        for entry in self.entries:
            self._lines(
                [
                    f"    if (message_id == (uint64_t){ip}_{entry.name}_ID) {{",
                    f"        return {ip}_receive_{entry.name}(handler, &raw);",
                    "    }",
                ],
                entry.condition,
            )
        self._lines(["    return WF_UNKNOWN_MESSAGE_ID;", "}", ""])

    # --- link -----------------------------------------------------------------------------------

    def _frame_format(self) -> str:
        framing = self.interface.framing
        ip = self.ip
        sync, length = self._role("sync"), self._role("payload_length")
        checksum = self._trailer_checksum()
        use = checksum.field.checksum if checksum else None
        sync_value = "0U"
        if sync is not None and sync.field.const is not None:
            sync_value = self.names.literal(sync.field, sync.field.const)
        covers = (
            "WF_COVERS_PAYLOAD"
            if use is not None and use.scope == "payload"
            else "WF_COVERS_HEADER_AND_PAYLOAD"
        )
        kind = FRAMING_CONSTANTS.get(framing.kind, "WF_FRAMING_FIXED")
        return (
            f"static const wf_frame_format {ip}_frame_format = {{\n"
            f"    {kind},\n"
            f"    {self._endian()},\n"
            f"    {ip}_HEADER_SIZE,\n"
            f"    {ip}_TRAILER_SIZE,\n"
            f"    {ip}_MAX_FRAME_SIZE,\n"
            f"    {{{sync.offset if sync else 0}U, {sync.width if sync else 0}U}},\n"
            f"    (uint64_t)({sync_value}),\n"
            f"    {{{length.offset if length else 0}U, {length.width if length else 0}U}},\n"
            f"    {LENGTH_CONSTANTS[framing.length_counts]},\n"
            f"    {checksum.offset if checksum else 0}U,\n"
            f"    {covers},\n"
            f"    {'true' if framing.resync else 'false'}\n"
            "};"
        )

    def _source_link(self) -> None:
        ip = self.ip
        checksum = self._trailer_checksum()
        use = checksum.field.checksum if checksum else None
        function = self.names.checksum_function(use.use) if use is not None else "NULL"
        size = checksum.width if checksum is not None else 0
        framed = "true" if self._framed() else "false"
        self._lines(
            [
                self._frame_format(),
                "",
                (
                    f"static wf_status {ip}_dispatch(const void *context, const uint8_t *frame, "
                    "size_t size)"
                ),
                "{",
                f"    return {ip}_dispatch_frame((const {ip}_handler *)context, frame, size);",
                "}",
                "",
                (
                    f"void {ip}_link_init({ip}_link *link, const wf_transport *transport, "
                    f"const {ip}_handler *handler)"
                ),
                "{",
                "    wf_frame_decoder decoder;",
                f"    wf_frame_decoder_init(&decoder, &{ip}_frame_format, {function}, {size}U);",
                f"    wf_link_init(&link->link, transport, {framed}, &decoder, link->rx_storage,",
                "                 sizeof(link->rx_storage), link->rx_frame, link->tx_frame,",
                f"                 {ip}_MAX_FRAME_SIZE, {ip}_dispatch, handler);",
                "}",
                "",
                f"wf_poll_result {ip}_link_poll({ip}_link *link)",
                "{",
                "    return wf_link_poll(&link->link);",
                "}",
                "",
                (
                    f"wf_status {ip}_link_send_frame({ip}_link *link, const uint8_t *frame, "
                    "size_t size)"
                ),
                "{",
                "    return wf_link_send_frame(&link->link, frame, size);",
                "}",
                "",
            ]
        )
        for entry in self.entries:
            self._lines(
                [
                    (
                        f"wf_status {ip}_link_send_{entry.name}({ip}_link *link, "
                        f"const {self._type(entry)} *message, const {ip}_header *header)"
                    ),
                    "{",
                    "    size_t size = 0U;",
                    (
                        f"    const wf_status status = {ip}_encode_{entry.name}(message, header, "
                        f"link->tx_frame, {ip}_MAX_FRAME_SIZE, &size);"
                    ),
                    "    return wf_link_send_encoded(&link->link, status, size);",
                    "}",
                    "",
                ],
                entry.condition,
            )


def render_interfaces(project: Project, banner: Banner, model: CModel) -> dict[str, str]:
    """Return the header and source files of every interface: relative path -> content."""
    files: dict[str, str] = {}
    for interface in project.interfaces.values():
        writer = CInterface(project, interface, banner, model)
        files[f"{project.name}/interfaces/{interface.name}.h"] = writer.render_header()
        files[f"{project.name}/interfaces/{interface.name}.c"] = writer.render_source()
    return files

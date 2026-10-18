"""C++ header of one interface: ids, encodings, frame layout, frame encoder, handler and receiver.

Supported subset: header fields are byte-aligned integers (no bits, occurs or `if:`); the trailer
holds at most one checksum plus constants; the header holds no checksum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wireforge.cpp import Names, Writer, guard
from wireforge.cpp_codec import encoding_literal
from wireforge.model import BUILTIN_BITS, INTEGER_TYPES, ChecksumUse, Field, Interface, Project

if TYPE_CHECKING:
    from collections.abc import Iterable

    from wireforge.generate import Banner

BYTE_BITS = 8
FRAMING_KINDS = {"fixed": "Fixed", "sync_length": "SyncLength", "length_prefixed": "LengthPrefixed"}
LENGTH_COUNTS = {
    "payload": "Payload",
    "payload_and_trailer": "PayloadAndTrailer",
    "frame": "Frame",
}
LENGTH_VALUES = {
    "payload": "payload_size",
    "payload_and_trailer": "payload_size + kTrailerSize",
    "frame": "total",
}
AUTOMATIC_ROLES = frozenset({"message_id", "payload_length", "sync", "protocol_version"})


def is_automatic(item: Field) -> bool:
    """Return whether the encoder fills a header field itself (ids, lengths, constants)."""
    return item.role in AUTOMATIC_ROLES or item.const is not None or item.checksum is not None


def header_is_written(fields: Iterable[Field]) -> bool:
    """Return whether a header field is read from the frame, so decoding stores into the header."""
    return any(not is_automatic(item) for item in fields)


SPAN = "wireforge::Span<std::uint8_t>"
CSPAN = "wireforge::Span<const std::uint8_t>"
STATUS = "wireforge::Status"


class InterfaceError(ValueError):
    """The interface uses a feature the C++ generator does not support."""


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


def is_checked_constant(item: Field, *, framed: bool) -> bool:
    """Whether a header field is a constant the receiver checks itself.

    The frame decoder checks the sync word of a framed link; every other constant and the
    protocol version (and, without framing, the sync word too) is checked before a message is
    handed to the application.
    """
    return (item.const is not None or item.role == "protocol_version") and not (
        framed and item.role == "sync"
    )


def checks_trailer(interface: Interface) -> bool:
    """Whether the receiver checks the checksum itself: only a link without a frame decoder does."""
    return interface.framing.kind == "none" and any(item.checksum for item in interface.trailer)


def needs_frame_check(interface: Interface) -> bool:
    """Whether the generated receiver checks a header constant or the checksum before a message."""
    framed = interface.framing.kind != "none"
    return checks_trailer(interface) or any(
        is_checked_constant(item, framed=framed) for item in interface.header or ()
    )


def version_namespace(version: str) -> str:
    """Namespace of an interface version: 1.2.0 -> v1_2."""
    major, minor, _ = version.split(".")
    return f"v{major}_{minor}"


def _doc(lines: list[str]) -> list[str]:
    return ["/**", *[f" * {line}".rstrip() for line in lines], " */"]


class InterfaceHeader:
    """Writes <project>/interfaces/<interface>.hpp."""

    def __init__(
        self, project: Project, interface: Interface, banner: Banner, notes: list[str]
    ) -> None:
        """Check the supported subset and prepare the field layout; `notes` trace requirements."""
        self.project = project
        self.notes = notes
        self.interface = interface
        self.names = Names(project)
        self.banner = banner
        self.out = Writer()
        self.header = self._place(interface.header or (), "header")
        self.trailer = self._place(interface.trailer, "trailer")
        self._check_trailer()
        self.entries = [self._entry(m.name, m.id, m.condition) for m in interface.messages]

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
            if item.bits is not None or item.occurs is not None or item.condition or not scalar:
                message = (
                    f"interface {self.interface.name}: {part} field {item.name} must be a "
                    "byte-aligned integer without bits, occurs or if:"
                )
                raise InterfaceError(message)
            width = BUILTIN_BITS[base] // BYTE_BITS
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
        return "Big" if endian == "big" else "Little"

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

    # --- file -----------------------------------------------------------------------------------

    def render(self) -> str:
        """Return the header text."""
        project, iface = self.project, self.interface
        name_guard = guard(project.name, iface.name, "HPP")
        namespace = f"{project.namespace or project.name}::{iface.name}"
        version = version_namespace(iface.version)
        includes = [
            f"#include <wireforge/{h}.hpp>" for h in ("dispatcher", "frame_decoder", "link")
        ]
        self._lines(
            [
                "/**",
                f" * @file {project.name}/interfaces/{iface.name}.hpp",
                f" * @brief Interface {iface.name} {iface.version} ({iface.direction}).",
                " *",
                *self.banner.lines(),
                " */",
                f"#ifndef {name_guard}",
                f"#define {name_guard}",
                "",
                f'#include "{project.name}/model.hpp"',
                "",
                *includes,
                "",
                "#include <algorithm>",
                "#include <cstddef>",
                "#include <cstdint>",
                "#include <utility>",
                "",
                f"/// @brief Interface {iface.name}.",
                *self.notes,
                f"namespace {namespace} {{",
                "",
                f"/// @brief Version {iface.version} of interface {iface.name}.",
                f"inline namespace {version} {{",
                "",
            ]
        )
        self._constants()
        self._sizes()
        self._frame_types()
        self._header_struct()
        self._frame_writer()
        self._frame_encoders()
        self._header_decoder()
        self._frame_check()
        self._handler()
        self._receiver()
        self._endpoint()
        self._lines(
            [
                f"}} // namespace {version}",
                "",
                f"}} // namespace {namespace}",
                "",
                f"#endif // {name_guard}",
            ]
        )
        return self.out.text()

    # --- constants ------------------------------------------------------------------------------

    def _constants(self) -> None:
        major, minor, patch = self.interface.version.split(".")
        id_field = self._role("message_id")
        id_type = self.names.scalar(id_field.field.type) if id_field else "std::uint32_t"
        header_size = sum(item.width for item in self.header)
        trailer_size = sum(item.width for item in self.trailer)
        self._lines(
            [
                f"inline constexpr std::uint32_t kVersionMajor = {major}U; ///< ICD major version.",
                f"inline constexpr std::uint32_t kVersionMinor = {minor}U; ///< ICD minor version.",
                f"inline constexpr std::uint32_t kVersionPatch = {patch}U; ///< ICD patch version.",
                f"inline constexpr std::size_t kHeaderSize = {header_size}U; ///< Header bytes.",
                f"inline constexpr std::size_t kTrailerSize = {trailer_size}U; ///< Trailer bytes.",
                "",
                "/// @brief Type of the message id field.",
                f"using MessageId = {id_type};",
                "",
                "/// @brief Number of message types (capacity of the dispatcher).",
                f"inline constexpr std::size_t kMessageCount = {max(len(self.entries), 1)}U;",
                "",
            ]
        )
        for entry in self.entries:
            item = self.project.messages[entry.name]
            literal = encoding_literal(self.project, item, self.interface)
            lines = [
                f"/// @brief Encoding of {entry.name} on this interface.",
                f"inline constexpr wireforge::Encoding k{entry.name}Encoding = {literal};",
            ]
            if entry.identifier is not None:
                lines += [
                    f"/// @brief Message id of {entry.name} on this interface.",
                    f"inline constexpr MessageId k{entry.name}Id = {entry.identifier}U;",
                ]
            self._lines(lines, entry.condition)
        self._lines(
            [
                "",
                "/// @brief Encoding of the header and trailer fields.",
                "inline constexpr wireforge::Encoding kFrameEncoding{",
                f"    wireforge::Endian::{self._endian()}, wireforge::BitOrder::MsbFirst,",
                "    true, true,",
                "    false};",
                "",
            ]
        )

    def _sizes(self) -> None:
        self._lines(
            [
                "/// @brief Largest payload of any message of this interface.",
                "[[nodiscard]] constexpr std::size_t max_payload_size() noexcept {",
                "    std::size_t size = 0U;",
            ]
        )
        for entry in self.entries:
            wire = f"max_wire_size(wireforge::type_tag<{entry.name}>)"
            self._lines([f"    size = std::max(size, {wire});"], entry.condition)
        framing = self.interface.framing
        limit = framing.frame_size if framing.kind == "fixed" else framing.max_frame_size
        frame = (
            "kHeaderSize + max_payload_size() + kTrailerSize"
            if limit is None
            else self.names.capacity(limit)
        )
        buffers = self.interface.buffers
        rx = self.names.capacity(buffers["rx_bytes"]) if "rx_bytes" in buffers else None
        tx = self.names.capacity(buffers["tx_bytes"]) if "tx_bytes" in buffers else None
        self._lines(
            [
                "    return size;",
                "}",
                "",
                "/// @brief Largest frame (size of frame buffers).",
                f"inline constexpr std::size_t kMaxFrameSize = {frame};",
                "/// @brief Size of the receive ring buffer.",
                f"inline constexpr std::size_t kRxBufferBytes = {rx or '2U * kMaxFrameSize'};",
                "/// @brief Size of the transmit buffer.",
                f"inline constexpr std::size_t kTxBufferBytes = {tx or 'kMaxFrameSize'};",
                "static_assert(kRxBufferBytes >= kMaxFrameSize && kTxBufferBytes >= kMaxFrameSize,",
                '              "buffers must hold a whole frame");',
                "",
            ]
        )

    # --- frame decoder, dispatcher, link --------------------------------------------------------

    def _frame_format(self) -> list[str]:
        framing = self.interface.framing
        sync, length = self._role("sync"), self._role("payload_length")
        checksum = self._trailer_checksum()
        use = checksum.field.checksum if checksum else None
        sync_value = "0U"
        if sync is not None and sync.field.const is not None:
            sync_value = self.names.literal(sync.field, sync.field.const)
        covers = "Payload" if use is not None and use.scope == "payload" else "HeaderAndPayload"
        algorithm = f"checksums::{use.use}" if use is not None else ""
        return [
            "/// @brief Frame layout for the frame decoder.",
            "inline constexpr wireforge::FrameFormat kFrameFormat{",
            f"    wireforge::FramingKind::{FRAMING_KINDS[framing.kind]},",
            f"    wireforge::Endian::{self._endian()},",
            "    kHeaderSize,",
            "    kTrailerSize,",
            "    kMaxFrameSize,",
            f"    {{{sync.offset if sync else 0}U, {sync.width if sync else 0}U}},",
            f"    {sync_value},",
            f"    {{{length.offset if length else 0}U, {length.width if length else 0}U}},",
            f"    wireforge::LengthCounts::{LENGTH_COUNTS[framing.length_counts]},",
            f"    {checksum.offset if checksum else 0}U,",
            f"    wireforge::ChecksumCoverage::{covers},",
            f"    {'true' if framing.resync else 'false'},",
            "};",
            "/// @brief Frame decoder of this interface.",
            f"using Decoder = wireforge::FrameDecoder<{algorithm}>;",
        ]

    def _frame_types(self) -> None:
        if self.interface.framing.kind == "none":
            lines = [
                "/// @brief Each transport read is one frame.",
                "using Decoder = wireforge::Unframed;",
            ]
        else:
            lines = self._frame_format()
        id_field = self._role("message_id")
        if id_field is not None:
            lines += [
                "/// @brief Where the message id is inside a frame.",
                "inline constexpr wireforge::HeaderLayout kHeaderLayout{",
                f"    kHeaderSize, kTrailerSize, {{{id_field.offset}U, {id_field.width}U}},",
                "    kFrameEncoding.endian};",
                "/// @brief Dispatcher type of this interface.",
                "using Dispatcher = wireforge::Dispatcher<kMessageCount>;",
            ]
        else:
            lines += [
                "/// @brief Dispatcher type of this interface (single message, no id).",
                "using Dispatcher = wireforge::RawDispatcher<>;",
            ]
        doc = _doc(
            [
                "@brief Link of this interface over @p Transport with preallocated buffers.",
                "@tparam Transport Byte transport (see wireforge/transport.hpp).",
                "@tparam L Lock policy of the transmit path.",
            ]
        )
        self._lines(
            [
                *lines,
                "",
                *doc,
                "template <typename Transport, typename L = wireforge::DefaultLock>",
                "using Link =",
                "    wireforge::Link<Transport, Decoder, Dispatcher, kMaxFrameSize,",
                "                    kRxBufferBytes, L>;",
                "",
            ]
        )

    # --- transmit side --------------------------------------------------------------------------

    def _is_automatic(self, item: Field) -> bool:
        return is_automatic(item)

    def _header_struct(self) -> None:
        lines = [
            "/// @brief Header fields set by the sender (ids, lengths, constants are automatic).",
            "struct Header {",
        ]
        for placed in self.header:
            item = placed.field
            if self._is_automatic(item):
                continue
            default = "{}"
            if item.default is not None and not isinstance(item.default, list):
                default = f"{{{self.names.literal(item, item.default)}}}"
            doc = item.description or item.name
            lines.append(f"    {self.names.scalar(item.type)} {item.name}{default}; ///< {doc}")
        self._lines([*lines, "};", ""])

    def _header_write(self, item: Field) -> str:
        wire = self.names.scalar(item.type)
        if item.role == "message_id":
            return "head.write(message_id);"
        if item.role == "payload_length":
            length = LENGTH_VALUES[self.interface.framing.length_counts]
            return f"head.write_count<{wire}>({length});"
        if item.role == "protocol_version" or item.const is not None:
            return f"head.write<{wire}>({self._literal(item)});"
        return f"head.write(header.{item.name});"

    def _trailer_write(self, item: Field) -> str:
        wire = self.names.scalar(item.type)
        use = item.checksum
        if use is None:
            return f"tail.write<{wire}>({self._literal(item)});"
        covered = (
            "buffer.subspan(kHeaderSize, payload_size)"
            if use.scope == "payload"
            else "buffer.first(kHeaderSize + payload_size)"
        )
        value = f"wireforge::compute_checksum<checksums::{use.use}>({CSPAN}({covered}))"
        return f"tail.write<{wire}>(static_cast<{wire}>({value}));"

    def _trailer_lines(self, tail: str) -> list[str]:
        if not self.trailer:
            return []
        return [
            f"wireforge::Encoder tail({tail}, kFrameEncoding);",
            *[self._trailer_write(placed.field) for placed in self.trailer],
            "head.check(tail.status());",
        ]

    def _overhead(self) -> bool:
        return bool(self.header or self.trailer)

    def _room_check(self) -> list[str]:
        if not self._overhead():
            return []
        return [
            "if (buffer.size() < kHeaderSize + kTrailerSize) {",
            "    return wireforge::Status::BufferTooSmall;",
            "}",
        ]

    def _head_lines(self, size_check: list[str]) -> list[str]:
        if not self.header and not self.trailer and not size_check:
            return ["size = total;", "return kOk;"]
        return [
            "wireforge::Encoder head(buffer.first(kHeaderSize), kFrameEncoding);",
            *[self._header_write(placed.field) for placed in self.header],
            *self._trailer_lines("buffer.subspan(kHeaderSize + payload_size, kTrailerSize)"),
            *size_check,
            "size = wireforge::size_if_ok(head.status(), total);",
            "return head.status();",
        ]

    def _frame_writer(self) -> None:
        framing = self.interface.framing
        size_check = []
        if framing.kind == "fixed":
            size_check = [
                (
                    "head.check(wireforge::status_if(total == kMaxFrameSize, "
                    "wireforge::Status::InvalidLength));"
                )
            ]
        elif framing.max_frame_size is not None:
            size_check = [
                (
                    "head.check(wireforge::status_if(total <= kMaxFrameSize, "
                    "wireforge::Status::InvalidLength));"
                )
            ]
        body = [
            "constexpr wireforge::Status kOk = wireforge::Status::Ok;",
            "size = 0U;",
            "static_cast<void>(header);",
            "static_cast<void>(message_id);",
            *self._room_check(),
            "std::size_t payload_size = 0U;",
            "const std::size_t room = buffer.size() - kHeaderSize - kTrailerSize;",
            f"const {STATUS} status =",
            "    std::forward<Payload>(payload)(buffer.subspan(kHeaderSize, room), payload_size);",
            "if (status != kOk) {",
            "    return status;",
            "}",
            "const std::size_t total = kHeaderSize + payload_size + kTrailerSize;",
            *self._head_lines(size_check),
        ]
        self._lines(
            [
                "/// @cond INTERNAL",
                "namespace detail {",
                "",
                "template <typename Payload>",
                f"[[nodiscard]] {STATUS} write_frame(const Header& header, MessageId message_id,",
                f"                                   {SPAN} buffer, std::size_t& size,",
                "                                   Payload&& payload) noexcept {",
                *[f"    {line}" for line in body],
                "}",
                "",
                "} // namespace detail",
                "/// @endcond",
                "",
            ]
        )

    def _frame_encoders(self) -> None:
        for entry in self.entries:
            identifier = f"k{entry.name}Id" if entry.identifier is not None else "MessageId{}"
            doc = _doc(
                [
                    f"@brief Encodes a complete {entry.name} frame (header, payload, trailer).",
                    "@param message Message to send.",
                    "@param buffer Destination of the frame.",
                    "@param[out] size Frame size in bytes (0 on error).",
                    "@param header Sender-controlled header fields.",
                    "@return The first error, or wireforge::Status::Ok.",
                ]
            )
            serialize = f"serialize(message, payload, payload_size, k{entry.name}Encoding)"
            lines = [
                *doc,
                f"[[nodiscard]] inline {STATUS} encode_frame(const {entry.name}& message,",
                f"                                            {SPAN} buffer, std::size_t& size,",
                "                                            const Header& header = Header{})",
                "    noexcept {",
                f"    return detail::write_frame(header, {identifier}, buffer, size,",
                f"        [&message]({SPAN} payload, std::size_t& payload_size) {{",
                f"            return {serialize};",
                "        });",
                "}",
                "",
            ]
            self._lines(lines, entry.condition)

    # --- receive side ---------------------------------------------------------------------------

    def _header_read(self, placed: _Placed) -> str:
        item = placed.field
        if item.role == "protocol_version" or item.const is not None:
            return f"decoder.expect<{self.names.scalar(item.type)}>({self._literal(item)});"
        if self._is_automatic(item):
            return f"decoder.skip({placed.width}U);"
        return f"decoder.read(header.{item.name});"

    def _header_decode_body(self) -> list[str]:
        if not self._overhead():
            return [
                "    static_cast<void>(frame);",
                "    static_cast<void>(header);",
                "    return wireforge::Status::Ok;",
            ]
        return [
            "    static_cast<void>(header);",
            "    const bool complete = frame.size() >= kHeaderSize + kTrailerSize;",
            "    wireforge::Decoder decoder(frame.first(kHeaderSize), kFrameEncoding);",
            "    decoder.check(complete ? wireforge::Status::Ok",
            "                           : wireforge::Status::Truncated);",
            *[f"    {self._header_read(placed)}" for placed in self.header],
            "    return decoder.status();",
        ]

    def _header_writes(self) -> bool:
        """Return whether some header field is read from the frame, so the decoder writes header."""
        return header_is_written(placed.field for placed in self.header)

    def _header_decoder(self) -> None:
        # The header parameter is an out-parameter only when a field is stored into it; otherwise
        # it is const, so an interface whose header is entirely automatic does not demand a
        # mutable header from its caller.
        if self._header_writes():
            parameter = "Header& header"
            doc_line = "@param[out] header Sender-controlled header fields."
        else:
            parameter = "const Header& header"
            doc_line = "@param header Frame header; every field of it is automatic."
        doc = _doc(
            [
                "@brief Reads and checks the header of a complete frame.",
                "@param frame Frame as delivered by the frame decoder.",
                doc_line,
                "@return ConstMismatch for a wrong sync, constant or protocol version,",
                "        Truncated for a short frame, otherwise wireforge::Status::Ok.",
            ]
        )
        self._lines(
            [
                *doc,
                f"[[nodiscard]] inline {STATUS} decode_header({CSPAN} frame,",
                " " * 45 + parameter + ") noexcept {",
                *self._header_decode_body(),
                "}",
                "",
            ]
        )

    def _handler(self) -> None:
        self._lines(
            [
                "/// @brief Receives the messages of this interface, decoded or as raw bytes.",
                "class Handler {",
                "public:",
            ]
        )
        for entry in self.entries:
            name = entry.name
            self._lines(
                [
                    f"    /// @brief Called for every valid {name} received on this interface.",
                    f"    virtual void on{name}Received(const {name}& message) noexcept = 0;",
                    "    /**",
                    f"     * @brief Called for every {name} frame before its payload is decoded.",
                    "     *",
                    "     * The default implementation decodes the payload and calls",
                    f"     * on{name}Received() when it is valid. Override it to take the",
                    "     * bytes without decoding: framing, the frame checksum and the message",
                    "     * id were checked, the payload was not. @p raw points into the receive",
                    "     * buffer and is valid only during this call.",
                    "     * @return Reported to the link; the default returns the decode status.",
                    "     */",
                    f"    virtual {STATUS} on{name}Raw(",
                    "        const wireforge::RawMessage& raw) noexcept {",
                    f"        {name} message{{}};",
                    f"        const {STATUS} status =",
                    f"            deserialize(raw.payload, message, k{name}Encoding);",
                    "        if (status == wireforge::Status::Ok) {",
                    f"            on{name}Received(message);",
                    "        }",
                    "        return status;",
                    "    }",
                ],
                entry.condition,
            )
        self._lines(
            [
                "",
                "protected:",
                "    Handler() = default;                          ///< Constructed by subclasses.",
                "    Handler(const Handler&) = default;            ///< Copy for derived classes.",
                "    Handler& operator=(const Handler&) = default; ///< Copy for derived classes.",
                "    Handler(Handler&&) = default;                 ///< Move for derived classes.",
                "    Handler& operator=(Handler&&) = default;      ///< Move for derived classes.",
                "    /// @brief Not virtual: handlers are never deleted through a Handler pointer.",
                "    ~Handler() = default;",
                "};",
                "",
            ]
        )

    def _attach_body(self) -> list[str]:
        if self._role("message_id") is None:
            if not self.entries:
                return [
                    "        static_cast<void>(dispatcher);",
                    "        return wireforge::Status::Ok;",
                ]
            name = self.entries[0].name
            handler = f"wireforge::MessageHandler::bind<&Receiver::receive{name}>(*this)"
            return [f"        return dispatcher.set_handler({handler});"]
        lines = ["        return wireforge::first_error("]
        for entry in self.entries:
            if entry.condition is not None:
                lines.append(f"#if {self.names.condition(entry.condition)}")
            handler = f"wireforge::MessageHandler::bind<&Receiver::receive{entry.name}>(*this)"
            lines.append(f"            dispatcher.add(k{entry.name}Id, {handler}),")
            if entry.condition is not None:
                lines.append("#endif")
        return [*lines, "            wireforge::Status::Ok);"]

    # --- checks of the frame that the frame decoder does not make ------------------------------

    def _checked_constants(self) -> list[_Placed]:
        """Header constants the frame decoder does not check (it checks only the sync word)."""
        framed = self.interface.framing.kind != "none"
        return [p for p in self.header if is_checked_constant(p.field, framed=framed)]

    def _checks_trailer(self) -> bool:
        return checks_trailer(self.interface)

    def _frame_check_needed(self) -> bool:
        return needs_frame_check(self.interface)

    def _trailer_read(self, item: Field) -> list[str]:
        wire = self.names.scalar(item.type)
        if item.checksum is None:
            return [f"tail.expect<{wire}>({self._literal(item)});"]
        return [f"{wire} received{{}};", "tail.read(received);"]

    def _trailer_check(self, checksum: _Placed, use: ChecksumUse) -> list[str]:
        wire = self.names.scalar(checksum.field.type)
        covered = (
            "frame.subspan(kHeaderSize, frame.size() - kHeaderSize - kTrailerSize)"
            if use.scope == "payload"
            else "frame.first(frame.size() - kTrailerSize)"
        )
        value = f"wireforge::compute_checksum<checksums::{use.use}>({covered})"
        return [
            "wireforge::Decoder tail(frame.subspan(frame.size() - kTrailerSize, kTrailerSize),",
            "                        kFrameEncoding);",
            *[line for placed in self.trailer for line in self._trailer_read(placed.field)],
            f"tail.check(static_cast<{wire}>({value}) == received ? {STATUS}::Ok",
            f"                                                  : {STATUS}::ChecksumMismatch);",
            "return tail.status();",
        ]

    def _frame_check(self) -> None:
        """Check what the frame decoder leaves unchecked, before a message is handed over."""
        if not self._frame_check_needed():
            return
        checksum = self._trailer_checksum()
        use = checksum.field.checksum if checksum is not None else None
        trailer = checksum is not None and use is not None and self._checks_trailer()
        body: list[str] = []
        if self._checked_constants():
            body.append("Header header{};" if self._header_writes() else "const Header header{};")
            if trailer:
                body += [
                    f"const {STATUS} header_status = decode_header(frame, header);",
                    f"if (header_status != {STATUS}::Ok) {{",
                    "    return header_status;",
                    "}",
                ]
            else:
                body.append("return decode_header(frame, header);")
        if checksum is not None and use is not None and trailer:
            body += self._trailer_check(checksum, use)
        doc = _doc(
            [
                "@brief Checks the header constants and, without a frame decoder, the checksum.",
                "@param frame A frame that is at least as long as header and trailer, as the",
                "             dispatcher guarantees.",
                "@return ConstMismatch, ChecksumMismatch or wireforge::Status::Ok.",
            ]
        )
        self._lines(
            [
                "/// @cond INTERNAL",
                "namespace detail {",
                "",
                *doc,
                f"[[nodiscard]] inline {STATUS} check_frame({CSPAN} frame) noexcept {{",
                *[f"    {line}" for line in body],
                "}",
                "",
                "} // namespace detail",
                "/// @endcond",
                "",
            ]
        )

    def _receiver(self) -> None:
        doc = _doc(
            [
                "@brief Registers this receiver with @p dispatcher (before `dispatcher.start()`).",
                "@return The first registration error, or wireforge::Status::Ok.",
            ]
        )
        self._lines(
            [
                "/// @brief Forwards the messages of this interface to a Handler.",
                "class Receiver {",
                "public:",
                "    /// @brief Forwards to @p handler, which must outlive the receiver.",
                "    explicit Receiver(Handler& handler) noexcept : handler_(&handler) {}",
                "",
                *[f"    {line}" for line in doc],
                f"    [[nodiscard]] {STATUS} attach(Dispatcher& dispatcher) noexcept {{",
                *self._attach_body(),
                "    }",
                "",
                "private:",
            ]
        )
        for entry in self.entries:
            name = entry.name
            self._lines(
                [
                    f"    {STATUS} receive{name}(const wireforge::RawMessage& raw) noexcept {{",
                    *self._receive_body(name),
                    "    }",
                    "",
                ],
                entry.condition,
            )
        self._lines(["    Handler* handler_;", "};", ""])

    def _receive_body(self, name: str) -> list[str]:
        if not self._frame_check_needed():
            return [f"        return handler_->on{name}Raw(raw);"]
        return [
            f"        const {STATUS} checked = detail::check_frame(raw.frame);",
            f"        return checked == {STATUS}::Ok ? handler_->on{name}Raw(raw) : checked;",
        ]

    def _endpoint(self) -> None:
        decoder = (
            "Decoder decoder_{};"
            if self.interface.framing.kind == "none"
            else ("Decoder decoder_{kFrameFormat};")
        )
        dispatcher = (
            "Dispatcher dispatcher_{kHeaderLayout};"
            if self._role("message_id") is not None
            else "Dispatcher dispatcher_{kTrailerSize, kHeaderSize};"
        )
        doc = _doc(
            [
                "@brief Receive side of this interface: frame decoder, dispatcher and receiver.",
                "",
                "Construct it, check status(), then pass decoder() and dispatcher() to a Link.",
            ]
        )
        self._lines(
            [
                *doc,
                "class Endpoint {",
                "public:",
                "    /// @brief Wires a receiver for @p handler and starts the dispatcher.",
                "    explicit Endpoint(Handler& handler) noexcept",
                "        : receiver_(handler), status_(wire(receiver_, dispatcher_)) {}",
                "",
                "    /// @brief Result of the registration; must be Ok before use.",
                f"    [[nodiscard]] {STATUS} status() const noexcept {{ return status_; }}",
                "    /// @brief Frame decoder for the Link.",
                "    [[nodiscard]] Decoder& decoder() noexcept { return decoder_; }",
                "    /// @brief Dispatcher for the Link.",
                "    [[nodiscard]] const Dispatcher& dispatcher() const noexcept {",
                "        return dispatcher_;",
                "    }",
                "",
                "private:",
                f"    static {STATUS} wire(Receiver& receiver, Dispatcher& dispatcher) noexcept {{",
                f"        const {STATUS} attached = receiver.attach(dispatcher);",
                "        return wireforge::first_error(attached, dispatcher.start());",
                "    }",
                "",
                f"    {decoder}",
                f"    {dispatcher}",
                "    Receiver receiver_;",
                f"    {STATUS} status_;",
                "};",
                "",
            ]
        )

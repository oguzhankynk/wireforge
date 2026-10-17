"""C++ encode/decode functions of generated types and messages (same rules as wireforge.codec)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wireforge.cpp import Names, Writer, plain_count, wire_only
from wireforge.model import BUILTIN_BITS, Checksum, Field, Interface, Project, Struct

if TYPE_CHECKING:
    from collections.abc import Callable

BYTE_BITS = 8


@dataclass(frozen=True)
class _Leaf:
    """One field of a bit group: the model field and the C++ expression of its value."""

    field: Field
    subject: str


def checksum_type(checksum: Checksum) -> str:
    """C++ type implementing a checksum declaration."""
    if checksum.algo == "crc32":
        return "wireforge::Crc32"
    if checksum.algo == "custom":
        return checksum.impl or ""
    refin = "true" if checksum.refin else "false"
    refout = "true" if checksum.refout else "false"
    return (
        f"wireforge::Crc<{checksum.width}U, {checksum.poly or 0:#x}U, {checksum.init:#x}U, "
        f"{refin}, {refout}, {checksum.xorout:#x}U>"
    )


def encoding_literal(project: Project, item: Struct, interface: Interface | None = None) -> str:
    """wireforge::Encoding of a message: message, then interface, then project defaults."""
    info = item.message
    defaults = project.defaults
    iface_endian = interface.endian if interface else None
    iface_order = interface.bit_order if interface else None
    endian = (info.endian if info else None) or iface_endian or defaults.get("endian") or "big"
    order = (info.bit_order if info else None) or iface_order or defaults.get("bit_order")
    order = order or "msb_first"
    checks: dict[str, object] = {"encode": True, "decode": True}
    configured = defaults.get("range_check")
    checks.update(configured if isinstance(configured, dict) else {})
    checks.update((info.range_check if info else None) or {})
    endian_name = "Big" if endian == "big" else "Little"
    order_name = "MsbFirst" if order == "msb_first" else "LsbFirst"
    flags = ", ".join("true" if checks[key] else "false" for key in ("encode", "decode"))
    trailing = "true" if interface is not None and interface.allow_trailing_bytes else "false"
    return (
        f"wireforge::Encoding{{wireforge::Endian::{endian_name}, "
        f"wireforge::BitOrder::{order_name}, {flags}, {trailing}}}"
    )


class CodecWriter:
    """Emits encode() and decode() of one struct into a Writer."""

    def __init__(self, project: Project, out: Writer) -> None:
        """Bind to the project and the output."""
        self.project = project
        self.names = Names(project)
        self.out = out

    # --- shared helpers -------------------------------------------------------------------------

    def _wire_type(self, item: Field) -> str:
        return self.names.scalar(item.type)

    def _zero_literal(self, item: Field) -> str:
        if item.default is not None and not isinstance(item.default, list):
            return self.names.literal(item, item.default)
        if item.type in self.project.enums:
            return f"static_cast<{item.type}>(0)"
        if item.type == "bool":
            return "false"
        return self.names.literal(item, 0.0 if item.type in {"f32", "f64"} else 0)

    def _const_literal(self, item: Field) -> str:
        if item.const is None:
            return self._zero_literal(item)
        return self.names.literal(item, item.const)

    def _value_checks(self, item: Field, subject: str, members: set[str]) -> list[str]:
        """Range then enum checks (order of the reference codec)."""
        checks = []
        low, high = item.min, item.max
        limits = self.names.type_limits(item)
        if limits is not None and isinstance(low, int) and low <= limits[0]:
            low = None
        if limits is not None and isinstance(high, int) and high >= limits[1]:
            high = None
        names = self.names
        if low is not None and high is not None:
            bounds = f"{names.bound(item, low, members)}, {names.bound(item, high, members)}"
            checks.append(f"wireforge::check_range({subject}, {bounds})")
        elif low is not None:
            checks.append(f"wireforge::check_min({subject}, {names.bound(item, low, members)})")
        elif high is not None:
            checks.append(f"wireforge::check_max({subject}, {names.bound(item, high, members)})")
        if item.type in self.project.enums:
            checks.append(f"wireforge::check_true(is_valid({subject}))")
        return checks

    def _group_bits(self, item: Field) -> int | None:
        if item.occurs is not None:
            return None
        if item.bits is not None:
            return item.bits
        inner = self.project.types.get(item.type)
        if inner is None or not inner.fields or any(f.bits is None for f in inner.fields):
            return None
        return sum(f.bits or 0 for f in inner.fields)

    def _groups(self, item: Struct) -> dict[str, list[_Leaf]]:
        """Bit groups keyed by the name of their first field."""
        result: dict[str, list[_Leaf]] = {}
        current: list[_Leaf] | None = None
        hidden = wire_only(item)
        for field in item.fields:
            if self._group_bits(field) is None:
                current = None
                continue
            if current is None:
                current = result.setdefault(field.name, [])
            current += self._leaves(field, hidden)
        return result

    def _leaves(self, field: Field, hidden: set[str]) -> list[_Leaf]:
        if field.bits is not None:
            return [_Leaf(field, "" if field.name in hidden else f"value.{field.name}")]
        inner = self.project.types[field.type]
        inner_hidden = wire_only(inner)
        return [
            _Leaf(leaf, "" if leaf.name in inner_hidden else f"value.{field.name}.{leaf.name}")
            for leaf in inner.fields
        ]

    def _element_size(self, item: Field) -> str:
        if self.names.has_codec(item):
            cpp_name = self.names.scalar(item.type)
            return f"max_wire_size(wireforge::type_tag<{cpp_name}>)"
        return f"{BUILTIN_BITS[self.names.base(item.type)] // BYTE_BITS}U"

    def _count_owner(self, item: Struct, counter: str) -> Field:
        return next(field for field in item.fields if plain_count(field) == counter)

    def _positions(self, item: Struct) -> tuple[set[str], set[str], bool]:
        """Fields whose start / end offsets checksums need, and whether `start` is needed."""
        starts: set[str] = set()
        ends: set[str] = set()
        preceding = False
        for field in item.fields:
            use = field.checksum
            if use is None:
                continue
            if use.first is not None and use.last is not None:
                starts.add(use.first)
                ends.add(use.last)
            preceding = preceding or use.scope == "preceding"
        return starts, ends, preceding

    def _emit_fields(
        self,
        item: Struct,
        side: str,
        statements: Callable[[Field, Struct, set[str]], list[str]],
    ) -> None:
        """Emit the statements of every field (bit groups once), wrapping #if and offsets."""
        out = self.out
        coder = "encoder" if side == "encode" else "decoder"
        hidden = wire_only(item)
        members = {field.name for field in item.fields if field.name not in hidden}
        starts, ends, preceding = self._positions(item)
        groups = self._groups(item)
        in_group = {leaf.field.name for leaves in groups.values() for leaf in leaves}
        if preceding:
            out.line(f"const std::size_t start = {coder}.position();")
        uses_value = False
        for field in item.fields:
            lines: list[str] = []
            if field.name in starts:
                lines.append(f"const std::size_t at_{field.name} = {coder}.position();")
            if field.name in groups:
                lines += self._group(groups[field.name], side, members)
                uses_value = uses_value or any(leaf.subject for leaf in groups[field.name])
            elif field.name not in in_group and not self._inline_bits(field):
                lines += statements(field, item, members)
                uses_value = uses_value or field.name in members or plain_count(field) is not None
            if field.name in ends:
                lines.append(f"const std::size_t end_{field.name} = {coder}.position();")
            self._conditional(field, lines)
        if not uses_value:
            out.line("static_cast<void>(value);")

    def _inline_bits(self, field: Field) -> bool:
        return field.bits is None and self._group_bits(field) is not None

    def _conditional(self, field: Field, lines: list[str]) -> None:
        if not lines:
            return
        if field.condition is not None:
            self.out.line(f"#if {self.names.condition(field.condition)}")
        for text in lines:
            self.out.line(text)
        if field.condition is not None:
            self.out.line("#endif")

    def _block(self, header: str, body: list[str]) -> list[str]:
        return [f"{header} {{", *[f"    {line}" for line in body], "}"]

    def _checksum_span(self, field: Field, coder: str) -> tuple[str, str]:
        use = field.checksum
        if use is not None and use.first is not None and use.last is not None:
            return f"at_{use.first}", f"end_{use.last}"
        return "start", f"{coder}.position()"

    # --- encode ---------------------------------------------------------------------------------

    def encode_struct(self, item: Struct) -> None:
        """Emit `encode(const T&, wireforge::Encoder&)`."""
        out = self.out
        out.line("/// @brief Appends the wire form of @p value to @p encoder.")
        out.line(
            f"inline void encode(const {item.name}& value, wireforge::Encoder& encoder) noexcept {{"
        )
        out.indent()
        self._emit_fields(item, "encode", self._encode_field)
        out.dedent()
        out.line("}")
        out.line()

    def _encode_field(self, field: Field, owner: Struct, members: set[str]) -> list[str]:
        if field.checksum is not None:
            begin, end = self._checksum_span(field, "encoder")
            algo = field.checksum.use
            return [f"encoder.write_checksum<checksums::{algo}>({begin}, {end});"]
        if field.const is not None or field.reserved:
            literal = self._const_literal(field)
            return [f"encoder.write<{self._wire_type(field)}>({literal});"]
        if field.name not in members:
            return self._encode_count(field, owner)
        subject = f"value.{field.name}"
        if field.occurs is None:
            return self._encode_value(field, subject, members)
        return self._encode_occurs(field, subject, members)

    def _encode_count(self, field: Field, owner: Struct) -> list[str]:
        array = self._count_owner(owner, field.name)
        size = f"value.{array.name}.size()"
        occurs = array.occurs
        low = self.names.capacity(occurs.min or 0) if occurs is not None else "0U"
        high = self.names.capacity(occurs.max) if occurs is not None else "0U"
        lines = [f"encoder.check(wireforge::check_count({size}, {low}, {high}));"]
        lines += [
            f"encoder.check_value({check});" for check in self._value_checks(field, size, set())
        ]
        lines.append(f"encoder.write_count<{self._wire_type(field)}>({size});")
        return lines

    def _encode_value(self, field: Field, subject: str, members: set[str]) -> list[str]:
        if self.names.has_codec(field):
            return [f"encode({subject}, encoder);"]
        checks = [f"encoder.check_value({c});" for c in self._value_checks(field, subject, members)]
        return [*checks, f"encoder.write({subject});"]

    def _encode_occurs(self, field: Field, subject: str, members: set[str]) -> list[str]:
        occurs = field.occurs
        if occurs is None:
            return []
        lines: list[str] = []
        dynamic = occurs.count is not None or occurs.fill
        if dynamic and (occurs.min or 0) != 0:
            low, high = self.names.capacity(occurs.min or 0), self.names.capacity(occurs.max)
            lines.append(f"encoder.check(wireforge::check_count({subject}.size(), {low}, {high}));")
        if occurs.count is not None and plain_count(field) is None:
            expected = self.names.value(occurs.count, members)
            lines.append(
                f"encoder.check(wireforge::check_count_equals({subject}.size(), {expected}));"
            )
        if field.type == "char":
            width = f"{subject}.size()" if dynamic else self.names.capacity(occurs.max)
            return [*lines, f"encoder.write_text({subject}, {width});"]
        body = self._encode_value(field, "element", members)
        return [*lines, *self._block(f"for (const auto& element : {subject})", body)]

    def _group(self, leaves: list[_Leaf], side: str, members: set[str]) -> list[str]:
        total = sum(leaf.field.bits or 0 for leaf in leaves)
        if side == "encode":
            body = [f"wireforge::BitPacker group({total}U, encoder.encoding().bit_order);"]
            for leaf in leaves:
                field = leaf.field
                if not leaf.subject:
                    body.append(f"encoder.put(group, {self._const_literal(field)}, {field.bits}U);")
                    continue
                checks = self._value_checks(field, leaf.subject, members)
                body += [f"encoder.check_value({check});" for check in checks]
                body.append(f"encoder.put(group, {leaf.subject}, {field.bits}U);")
            body.append("encoder.write_bits(group);")
        else:
            body = [f"wireforge::BitUnpacker group = decoder.read_bits({total}U);"]
            for leaf in leaves:
                body += self._decode_leaf(leaf, members)
        return ["{", *[f"    {line}" for line in body], "}"]

    # --- decode ---------------------------------------------------------------------------------

    def _decode_leaf(self, leaf: _Leaf, members: set[str]) -> list[str]:
        field = leaf.field
        if field.const is not None:
            literal = self._const_literal(field)
            return [
                f"decoder.expect_bits<{self._wire_type(field)}>(group, {literal}, {field.bits}U);"
            ]
        if not leaf.subject:
            return [f"decoder.skip_bits(group, {field.bits}U);"]
        lines = [f"decoder.get(group, {leaf.subject}, {field.bits}U);"]
        checks = self._value_checks(field, leaf.subject, members)
        return lines + [f"decoder.check_value({check});" for check in checks]

    def decode_struct(self, item: Struct) -> None:
        """Emit `decode(T&, wireforge::Decoder&)`."""
        out = self.out
        out.line("/// @brief Reads @p value from @p decoder.")
        out.line(f"inline void decode({item.name}& value, wireforge::Decoder& decoder) noexcept {{")
        out.indent()
        self._emit_fields(item, "decode", self._decode_field)
        out.dedent()
        out.line("}")
        out.line()

    def _decode_field(self, field: Field, _owner: Struct, members: set[str]) -> list[str]:
        if field.checksum is not None:
            begin, end = self._checksum_span(field, "decoder")
            return [f"decoder.verify_checksum<checksums::{field.checksum.use}>({begin}, {end});"]
        if field.const is not None:
            return [f"decoder.expect<{self._wire_type(field)}>({self._const_literal(field)});"]
        if field.reserved:
            size = BUILTIN_BITS[self.names.base(field.type)] // BYTE_BITS
            return [f"decoder.skip({size}U);"]
        if field.name not in members:
            count = f"count_{field.name}"
            lines = [f"const std::size_t {count} = decoder.read_count<{self._wire_type(field)}>();"]
            checks = self._value_checks(field, count, set())
            return lines + [f"decoder.check_value({check});" for check in checks]
        subject = f"value.{field.name}"
        if field.occurs is None:
            return self._decode_value(field, subject, members)
        return self._decode_occurs(field, subject, members)

    def _decode_value(self, field: Field, subject: str, members: set[str]) -> list[str]:
        if self.names.has_codec(field):
            return [f"decode({subject}, decoder);"]
        checks = [f"decoder.check_value({c});" for c in self._value_checks(field, subject, members)]
        return [f"decoder.read({subject});", *checks]

    def _decode_count(self, field: Field, members: set[str]) -> str | None:
        occurs = field.occurs
        if occurs is None or not (occurs.count is not None or occurs.fill):
            return None
        if occurs.fill:
            return f"decoder.fill_count({self._element_size(field)})"
        counter = plain_count(field)
        if counter is not None and counter not in members:
            return f"count_{counter}"
        expected = self.names.value(occurs.count or 0, members)
        return f"static_cast<std::size_t>({expected})"

    def _decode_occurs(self, field: Field, subject: str, members: set[str]) -> list[str]:
        occurs = field.occurs
        if occurs is None:
            return []
        count = self._decode_count(field, members)
        low = self.names.capacity(occurs.min or 0)
        if field.type == "char":
            if count is None:
                width = self.names.capacity(occurs.max)
                return [f"decoder.read_text({subject}, {width}, true);"]
            return [f"decoder.read_text({subject}, {count}, false);"]
        lines = [] if count is None else [f"decoder.resize({subject}, {count}, {low});"]
        body = self._decode_value(field, "element", members)
        return [*lines, *self._block(f"for (auto& element : {subject})", body)]

    # --- messages -------------------------------------------------------------------------------

    def message_api(self, item: Struct) -> None:
        """Emit the message's default encoding, `serialize()` and `deserialize()`."""
        out = self.out
        name = item.name
        literal = encoding_literal(self.project, item)
        out.line(f"/// @brief Encoding of {name} without interface overrides.")
        out.line(f"inline constexpr wireforge::Encoding k{name}Encoding = {literal};")
        out.line()
        out.line("/**")
        out.line(f" * @brief Encodes the payload of a {name} message into @p buffer.")
        out.line(" * @param value Message to encode.")
        out.line(" * @param buffer Destination of the payload.")
        out.line(" * @param[out] size Number of bytes written (0 on error).")
        out.line(" * @param encoding Byte order, bit order and range checks.")
        out.line(" * @return The first error, or wireforge::Status::Ok.")
        out.line(" */")
        out.line(
            f"[[nodiscard]] inline wireforge::Status serialize(const {name}& value, "
            f"wireforge::Span<std::uint8_t> buffer, std::size_t& size, "
            f"const wireforge::Encoding& encoding = k{name}Encoding) noexcept {{"
        )
        out.indent()
        out.line("wireforge::Encoder encoder(buffer, encoding);")
        out.line("encode(value, encoder);")
        out.line("size = encoder.status() == wireforge::Status::Ok ? encoder.position() : 0U;")
        out.line("return encoder.status();")
        out.dedent()
        out.line("}")
        out.line()
        out.line("/**")
        out.line(f" * @brief Decodes the payload of a {name} message.")
        out.line(" * @param payload Encoded payload.")
        out.line(" * @param[out] value Decoded message (partially filled on error).")
        out.line(" * @param encoding Byte order, bit order and range checks.")
        out.line(" * @return The first error, or wireforge::Status::Ok.")
        out.line(" */")
        out.line(
            f"[[nodiscard]] inline wireforge::Status deserialize("
            f"wireforge::Span<const std::uint8_t> payload, {name}& value, "
            f"const wireforge::Encoding& encoding = k{name}Encoding) noexcept {{"
        )
        out.indent()
        out.line("wireforge::Decoder decoder(payload, encoding);")
        out.line("decode(value, decoder);")
        out.line("decoder.finish();")
        out.line("return decoder.status();")
        out.dedent()
        out.line("}")
        out.line()

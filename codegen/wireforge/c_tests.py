"""GoogleTest sources that test the generated C code of the messages of a project.

The tests are C++ (GoogleTest) and call the generated C functions through `model.h`. Every
expectation comes from the reference codec, as for the C++ tests (`cpp_tests.py`): the cases of
`test_cases.py` are the same, only the way a value is built and a result is read differs, because
C has no constructors, vectors or strings: a value is built with `<prefix>_<Type>_init()` and
assignments, an array of variable size has a `<name>_count` member.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from wireforge.c_model import encoding_initializer
from wireforge.c_names import CNames, used_names
from wireforge.codec import CodecError, crc
from wireforge.coverage_points import Points
from wireforge.cpp import Writer, string_literal, type_order, wire_only
from wireforge.llr import Verifier
from wireforge.scaling import (
    function_names,
    is_float,
    number,
    physical,
    probe,
    scaled_fields,
    widened,
)
from wireforge.test_cases import CLEAR, Edit

if TYPE_CHECKING:
    from collections.abc import Iterator

    from wireforge.codec import ChecksumPlugin
    from wireforge.layout import LayoutReport, MessageLayout, ProfileLayout
    from wireforge.model import Field, Project, Struct
    from wireforge.test_cases import (
        EncodeCase,
        MessageCases,
        Outcome,
        ProfileCases,
    )

BYTES_PER_LINE = 12
SUPPORT_NAMESPACE = "wireforge_testing::"
STATUS_NAME = re.compile(r"(?<!^)(?=[A-Z])")


def _identifier(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", text)


def status_constant(name: str) -> str:
    """Return the C constant of a status name: `OutOfRange` is `WF_OUT_OF_RANGE`."""
    return f"WF_{STATUS_NAME.sub('_', name).upper()}"


def _status(name: str) -> str:
    return f"static_cast<wf_status>({status_constant(name)})"


def _byte_array(out: Writer, name: str, data: bytes) -> None:
    """Declare a std::array holding `data`."""
    if not data:
        out.line(f"static constexpr std::array<std::uint8_t, 0U> {name}{{}};")
        return
    out.line(f"static constexpr std::array<std::uint8_t, {len(data)}U> {name}{{{{")
    for start in range(0, len(data), BYTES_PER_LINE):
        chunk = data[start : start + BYTES_PER_LINE]
        out.line("    " + ", ".join(f"0x{byte:02X}U" for byte in chunk) + ",")
    out.line("}};")


def _record(out: Writer, ids: tuple[str, ...]) -> None:
    if ids:
        out.line(f'::testing::Test::RecordProperty("requirements", "{",".join(ids)}");')


def _test(
    out: Writer,
    suite: str,
    name: str,
    lines: Iterator[str] | list[str],
    requirements: tuple[str, ...] = (),
) -> None:
    out.line(f"TEST({suite}, {name}) {{")
    out.indent()
    _record(out, requirements)
    for text in lines:
        out.line(text)
    out.dedent()
    out.line("}")
    out.line()


def _unique_name(name: str, used: set[str]) -> str:
    candidate, index = name, 2
    while candidate in used:
        candidate = f"{name}_{index}"
        index += 1
    used.add(candidate)
    return candidate


def _open_profile(out: Writer, profile: str | None) -> None:
    if profile:
        out.line(f"#if defined(WIREFORGE_PROFILE_{profile})")
        out.line()


def _close_profile(out: Writer, profile: str | None) -> None:
    if profile:
        out.line(f"#endif // WIREFORGE_PROFILE_{profile}")
        out.line()


# --- building values -------------------------------------------------------------------------


class Assignments:
    """C++ statements that build a C value from its JSON form."""

    def __init__(self, project: Project) -> None:
        """Bind to the project."""
        self.project = project
        self.names = CNames(project)

    def literal(self, item: Field, value: object) -> str:
        """Return the literal of one element value."""
        if not isinstance(value, (bool, int, float, str)):
            message = f"{item.name}: {value!r} is not a scalar"
            raise TypeError(message)
        enum = self.project.enums.get(item.type)
        if enum is not None and not isinstance(value, str):
            return f"static_cast<{self.names.tname(item.type)}>({value})"
        if item.type == "char":
            code = ord(value) if isinstance(value, str) else int(value)
            return f"static_cast<char>({code})"
        return self.names.literal(item, value)

    def struct(self, item: Struct, value: dict[str, Any], target: str) -> list[str]:
        """Return the assignments of the fields of a struct value."""
        hidden = wire_only(item)
        lines: list[str] = []
        for field in item.fields:
            if field.name in hidden or field.name not in value:
                continue
            lines += self.field(field, value[field.name], f"{target}.{field.name}")
        return lines

    def field(self, item: Field, value: object, target: str) -> list[str]:
        """Return the assignments of one field."""
        inner = self.project.types.get(item.type)
        if item.occurs is None:
            if inner is not None and isinstance(value, dict):
                return self.struct(inner, value, target)
            return [f"{target} = {self.literal(item, value)};"]
        if item.type == "char":
            text = str(value)
            length = len(text.encode("latin-1"))
            copy = [f"std::memcpy({target}, {string_literal(text)}, {length}U);"] if length else []
            return [*copy, f"{target}_count = {length}U;"]
        elements = value if isinstance(value, list) else []
        lines = [f"{target}_count = {len(elements)}U;"] if self.names.counted(item) else []
        for index, element in enumerate(elements):
            slot = f"{target}[{index}U]"
            if inner is not None and isinstance(element, dict):
                lines += self.struct(inner, element, slot)
            else:
                lines.append(f"{slot} = {self.literal(item, element)};")
        return lines

    def edit(self, edit: Edit) -> str:
        """Return the statement of an edit of the pattern value."""
        target = f"value.{edit.target}"
        if edit.value is CLEAR:
            return f"{target}_count = 0U;"
        return f"{target} = {self.literal(edit.field, edit.value)};"


# --- api.hpp and test_support.hpp --------------------------------------------------------------


def _api_struct(out: Writer, names: CNames, item: Struct) -> None:
    """Write the Api<Message> struct of one message."""
    type_name = names.tname(item.name)
    out.line(f"/// Functions of {item.name} as the test helpers use them.")
    out.line(f"struct Api{_identifier(item.name)} {{")
    out.indent()
    out.line(f"using Type = {type_name};")
    out.line(f"static constexpr std::size_t kMaxSize = {type_name}_MAX_WIRE_SIZE;")
    out.line(f"static void init(Type* value) {{ {names.function(item.name, 'init')}(value); }}")
    out.line(
        f"static wf_status validate(const Type& value) "
        f"{{ return {names.function(item.name, 'validate')}(&value); }}"
    )
    out.line("static const wf_encoding* encoding() { return nullptr; }")
    out.line("/// The default encoding of the message when @p encoding is null.")
    out.line("static wf_status serialize(const Type& value, std::uint8_t* buffer,")
    out.line("                           std::size_t capacity, std::size_t* size,")
    out.line("                           const wf_encoding* encoding) {")
    with_encoding = names.function(item.name, "serialize_with")
    default = names.function(item.name, "serialize")
    out.line("    if (encoding == nullptr) {")
    out.line(f"        return {default}(&value, buffer, capacity, size);")
    out.line("    }")
    out.line(f"    return {with_encoding}(&value, buffer, capacity, size, encoding);")
    out.line("}")
    out.line("static wf_status deserialize(const std::uint8_t* payload, std::size_t size,")
    out.line("                             Type* value, const wf_encoding* encoding) {")
    with_encoding = names.function(item.name, "deserialize_with")
    default = names.function(item.name, "deserialize")
    out.line("    if (encoding == nullptr) {")
    out.line(f"        return {default}(payload, size, value);")
    out.line("    }")
    out.line(f"    return {with_encoding}(payload, size, value, encoding);")
    out.line("}")
    out.dedent()
    out.line("};")


def api_header(project: Project) -> str:
    """Return api.hpp: the C functions of every message behind one small C++ interface."""
    names = CNames(project)
    out = Writer()
    out.line("// Generated by WireForge. Do not edit; regenerate instead.")
    out.line("#ifndef WIREFORGE_GENERATED_C_API_HPP")
    out.line("#define WIREFORGE_GENERATED_C_API_HPP")
    out.line()
    out.line("#include <cstddef>")
    out.line("#include <cstdint>")
    out.line()
    out.line('#include "wireforge/c/codec.h"')
    out.line('extern "C" {')
    out.line(f'#include "{project.name}/model.h"')
    out.line("}")
    out.line()
    out.line("namespace generated_tests {")
    out.line()
    for item in project.messages.values():
        if item.condition is not None:
            out.line(f"#if {names.condition(item.condition)}")
        _api_struct(out, names, item)
        if item.condition is not None:
            out.line("#endif")
        out.line()
    out.line("} // namespace generated_tests")
    out.line()
    out.line("#endif // WIREFORGE_GENERATED_C_API_HPP")
    return out.text()


SUPPORT_HEADER = """\
// Generated by WireForge. Do not edit; regenerate instead.
//
// Assertion helpers of the generated tests of the C code. `Api` is one of the `Api<Message>`
// structs of api.hpp.
#ifndef WIREFORGE_GENERATED_C_TEST_SUPPORT_HPP
#define WIREFORGE_GENERATED_C_TEST_SUPPORT_HPP

#include "api.hpp"

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <gtest/gtest.h>
#include <iomanip>
#include <sstream>
#include <string>

namespace wireforge_testing {

/// Hex dump of @p size bytes for failure messages.
inline std::string hex(const std::uint8_t* bytes, std::size_t size) {
    std::ostringstream text;
    for (std::size_t index = 0U; index < size; ++index) {
        text << std::hex << std::setw(2) << std::setfill('0')
             << static_cast<unsigned>(bytes[index]);
    }
    return text.str();
}

/// Buffer that holds any encoding of the message of `Api`.
template <typename Api> using WireBuffer = std::array<std::uint8_t, Api::kMaxSize + 1U>;

/// Checks that @p value serializes to exactly @p expected.
template <typename Api>
::testing::AssertionResult encodes_to(const typename Api::Type& value, const std::uint8_t* expected,
                                      std::size_t expected_size, const wf_encoding* encoding) {
    WireBuffer<Api> buffer{};
    std::size_t size = 0U;
    const wf_status status = Api::serialize(value, buffer.data(), buffer.size(), &size, encoding);
    if (status != WF_OK || size != expected_size ||
        std::memcmp(buffer.data(), expected, expected_size) != 0) {
        return ::testing::AssertionFailure()
               << "serialize: " << wf_status_name(status) << ", got " << hex(buffer.data(), size)
               << ", expected " << hex(expected, expected_size);
    }
    return ::testing::AssertionSuccess();
}

/// Checks that serializing @p value fails with @p expected and reports size 0.
template <typename Api>
::testing::AssertionResult encode_fails(const typename Api::Type& value, wf_status expected,
                                        const wf_encoding* encoding) {
    WireBuffer<Api> buffer{};
    std::size_t size = 1U;
    const wf_status status = Api::serialize(value, buffer.data(), buffer.size(), &size, encoding);
    if (status != expected || size != 0U) {
        return ::testing::AssertionFailure()
               << "serialize: " << wf_status_name(status) << " (size " << size << "), expected "
               << wf_status_name(expected);
    }
    return ::testing::AssertionSuccess();
}

/// Status of deserializing @p payload.
template <typename Api>
wf_status decode_status(const std::uint8_t* payload, std::size_t size,
                        const wf_encoding* encoding) {
    typename Api::Type value{};
    Api::init(&value);
    return Api::deserialize(payload, size, &value, encoding);
}

/// Checks that @p expected encodes to @p payload and that @p payload decodes to a value that
/// encodes to @p payload again.
template <typename Api>
::testing::AssertionResult golden_round_trip(const typename Api::Type& expected,
                                             const std::uint8_t* payload, std::size_t size,
                                             const wf_encoding* encoding) {
    const ::testing::AssertionResult encoded = encodes_to<Api>(expected, payload, size, encoding);
    if (!encoded) {
        return encoded;
    }
    typename Api::Type decoded{};
    Api::init(&decoded);
    const wf_status status = Api::deserialize(payload, size, &decoded, encoding);
    if (status != WF_OK) {
        return ::testing::AssertionFailure() << "deserialize: " << wf_status_name(status);
    }
    return encodes_to<Api>(decoded, payload, size, encoding) << " (re-encoding the decoded value)";
}

} // namespace wireforge_testing

#endif // WIREFORGE_GENERATED_C_TEST_SUPPORT_HPP
"""


def _header(out: Writer, project: Project, system: tuple[str, ...] = ()) -> None:
    out.line(f"// Generated by WireForge from project {project.name}. Do not edit.")
    out.line('#include "test_support.hpp"')
    out.line()
    for header in ("array", "cstddef", "cstdint", "cstring", "gtest/gtest.h", *system):
        out.line(f"#include <{header}>")
    out.line()
    out.line("namespace {")
    out.line()
    out.line("using wireforge_testing::decode_status;")
    out.line("using wireforge_testing::encode_fails;")
    out.line("using wireforge_testing::encodes_to;")
    out.line("using wireforge_testing::golden_round_trip;")
    out.line()


def _layouts(report: LayoutReport | None) -> dict[str | None, ProfileLayout]:
    return {} if report is None else {profile.profile: profile for profile in report.profiles}


# --- message_test.cpp --------------------------------------------------------------------------


class _MessageTests:
    """Writes message_test.cpp."""

    def __init__(
        self,
        project: Project,
        layout: LayoutReport | None,
        plugins: dict[str, ChecksumPlugin] | None = None,
    ) -> None:
        self.project = project
        self.plugins = plugins or {}
        self.assignments = Assignments(project)
        self.names = self.assignments.names
        self.out = Writer()
        self.layouts = _layouts(layout)
        self.sizes: dict[str, MessageLayout] = {}
        self.verifier = Verifier(project)
        self.points = Points("message_test.cpp")
        self.profile_name: str | None = None

    def _structs(self) -> list[Struct]:
        """Return the structs that have functions: the messages and the types they use."""
        used, _ = used_names(self.project)
        types = [item for item in type_order(self.project) if item.name in used]
        return [*types, *self.project.messages.values()]

    def render(self, profiles: tuple[ProfileCases, ...]) -> str:
        out = self.out
        scaled = any(scaled_fields(item) for item in self._structs())
        _header(out, self.project, ("limits",) if scaled else ())
        self.checksums()
        for profile in profiles:
            _open_profile(out, profile.profile)
            layout = self.layouts.get(profile.profile)
            self.sizes = {} if layout is None else {item.name: item for item in layout.messages}
            self.profile(profile)
            _close_profile(out, profile.profile)
        out.line("} // namespace")
        self.points.check()
        return out.text()

    def checksums(self) -> None:
        """Test the function of every checksum declaration on ASCII 123456789 (and on nothing)."""
        names = self.names
        data = b"123456789"
        for checksum in self.project.checksums.values():
            if checksum.algo == "custom":
                plugin = self.plugins.get(checksum.impl or "")
                if plugin is None:
                    message = f"checksum {checksum.name}: no plugin to compute the expectation"
                    status = "InvalidState"
                    raise CodecError(status, "", message)
                expected, empty = plugin(data), plugin(b"")
            else:
                expected, empty = crc(checksum, data), crc(checksum, b"")
            function = names.checksum_function(checksum.name)
            lines = [
                "static constexpr std::array<std::uint8_t, 9U> kData{",
                "    {0x31U, 0x32U, 0x33U, 0x34U, 0x35U, 0x36U, 0x37U, 0x38U, 0x39U}};",
                f"EXPECT_EQ({function}(kData.data(), kData.size()), UINT64_C({expected}));",
                f"EXPECT_EQ({function}(kData.data(), 0U), UINT64_C({empty}));",
            ]
            keys = self.verifier.keep(f"{checksum.name}.ALGORITHM")
            _test(self.out, "Checksum", checksum.name, lines, self.verifier.shown(keys))

    def profile(self, cases: ProfileCases) -> None:
        self.profile_name = cases.profile
        for message in cases.messages:
            self.require(message)
            self.message(message)
        for item in self._structs():
            for field in scaled_fields(item):
                self.points.require("physical", self.profile_name, item.name, field.name)
            self.physical(item)

    def require(self, cases: MessageCases) -> None:
        """Declare what the tests of a message must exercise, from the cases of the model."""
        name, profile = cases.message.name, self.profile_name
        self.points.require("defaults", profile, name)
        if isinstance(cases.default_encoded, bytes) and cases.default_encoded:
            self.points.require("serialize_room", profile, name)
        if isinstance(cases.pattern_encoded, bytes):
            self.points.require("round_trip", profile, name)
        for case in cases.encodes:
            self.points.require("edit", profile, name, case.name)
        for decode in cases.decodes:
            self.points.require("decode", profile, name, decode.name)

    def outcome(self, subject: str, outcome: Outcome, api: str) -> str:
        if isinstance(outcome, bytes):
            return (
                f"EXPECT_TRUE(encodes_to<{api}>({subject}, kExpected.data(), kExpected.size(), "
                f"{api}::encoding()));"
            )
        return (
            f"EXPECT_TRUE(encode_fails<{api}>({subject}, {_status(outcome)}, {api}::encoding()));"
        )

    @staticmethod
    def expected(outcome: Outcome, out: Writer) -> None:
        if isinstance(outcome, bytes):
            _byte_array(out, "kExpected", outcome)

    def message(self, cases: MessageCases) -> None:
        out = self.out
        name = cases.message.name
        api = f"generated_tests::Api{_identifier(name)}"
        round_trip = isinstance(cases.pattern_encoded, bytes)
        if cases.encodes or round_trip:  # the edits and the round trip use the pattern value
            statements = self.assignments.struct(cases.message, cases.pattern, "value")
            out.line(f"/// Pattern value of {name}: every field inside its range, arrays full.")
            out.line(f"{api}::Type pattern_{name}() {{")
            out.indent()
            out.line(f"{api}::Type value{{}};")
            out.line(f"{api}::init(&value);")
            for statement in statements:
                out.line(statement)
            out.line("return value;")
            out.dedent()
            out.line("}")
            out.line()
        out.line(f"TEST({name}, Defaults) {{")
        out.indent()
        self.points.exercise("defaults", self.profile_name, name)
        _record(out, self.verifier.shown(self.verifier.defaults(cases.message)))
        self.expected(cases.default_encoded, out)
        out.line(f"{api}::Type value{{}};")
        out.line(f"{api}::init(&value);")
        out.line(f"EXPECT_EQ({api}::validate(value), {_status(cases.default_validated)});")
        out.line(self.outcome("value", cases.default_encoded, api))
        out.dedent()
        out.line("}")
        out.line()
        used: set[str] = {"Defaults", "WireSize", "SerializeNeedsRoom", "PatternRoundTrip"}
        if isinstance(cases.default_encoded, bytes) and cases.default_encoded:
            self.needs_room(cases.message, api)
        if round_trip:
            self.pattern_round_trip(cases, api)
        if name in self.sizes:
            self.wire_size(self.sizes[name])
        for case in cases.encodes:
            self.encode_case(cases.message, case, api, used)
        for decode in cases.decodes:
            self.points.exercise("decode", self.profile_name, name, decode.name)
            lines = Writer(depth=0)
            _byte_array(lines, "kPayload", decode.payload)
            lines.line(
                f"EXPECT_EQ(decode_status<{api}>(kPayload.data(), kPayload.size(), "
                f"{api}::encoding()), {_status(decode.status)});"
            )
            _test(
                out,
                name,
                _unique_name(f"Decode_{decode.name}", used),
                lines.lines,
                self.verifier.shown(self.verifier.decode_case(cases.message, decode)),
            )

    def wire_size(self, message: MessageLayout) -> None:
        prefix = f"{self.names.tname(message.name)}"
        lines = [
            (
                f"static_assert({prefix}_MIN_WIRE_SIZE == {message.size_min}U, "
                '"minimum size of the layout analyzer");'
            ),
            (
                f"static_assert({prefix}_MAX_WIRE_SIZE == {message.size_max}U, "
                '"maximum size of the layout analyzer");'
            ),
        ]
        keys = self.verifier.shown(self.verifier.wire_size(self.project.messages[message.name]))
        _test(self.out, message.name, "WireSize", lines, keys)

    def pattern_round_trip(self, cases: MessageCases, api: str) -> None:
        """Check that the pattern value encodes to its bytes and decodes back, for every message."""
        name = cases.message.name
        payload = cases.pattern_encoded
        if not isinstance(payload, bytes):
            return
        self.points.exercise("round_trip", self.profile_name, name)
        lines = Writer(depth=0)
        _byte_array(lines, "kPayload", payload)
        lines.line(
            f"EXPECT_TRUE(golden_round_trip<{api}>(pattern_{name}(), kPayload.data(), "
            f"kPayload.size(), {api}::encoding()));"
        )
        keys = self.verifier.shown(self.verifier.keep(f"{name}.LAYOUT"))
        _test(self.out, name, "PatternRoundTrip", lines.lines, keys)

    def needs_room(self, message: Struct, api: str) -> None:
        """Check that a buffer without room is refused and the size reset, for every message."""
        name = message.name
        self.points.exercise("serialize_room", self.profile_name, name)
        lines = [
            "std::array<std::uint8_t, 1U> buffer{};",
            "std::size_t size = 1U;",
            f"{api}::Type value{{}};",
            f"{api}::init(&value);",
            f"EXPECT_EQ({api}::serialize(value, buffer.data(), 0U, &size, {api}::encoding()),",
            f"          {_status('BufferTooSmall')});",
            "EXPECT_EQ(size, 0U);",
        ]
        keys = self.verifier.shown(self.verifier.keep(f"{name}.LAYOUT"))
        _test(self.out, name, "SerializeNeedsRoom", lines, keys)

    def encode_case(self, message: Struct, case: EncodeCase, api: str, used: set[str]) -> None:
        out = self.out
        name = message.name
        self.points.exercise("edit", self.profile_name, name, case.name)
        out.line(f"TEST({name}, {_unique_name(_identifier(case.name), used)}) {{")
        out.indent()
        _record(out, self.verifier.shown(self.verifier.encode_case(message, case)))
        self.expected(case.encoded, out)
        out.line(f"{api}::Type value = pattern_{name}();")
        out.line(self.assignments.edit(case.edit))
        out.line(f"EXPECT_EQ({api}::validate(value), {_status(case.validated)});")
        out.line(self.outcome("value", case.encoded, api))
        out.dedent()
        out.line("}")
        out.line()

    def physical(self, item: Struct) -> None:
        """Test the physical getter and setter of every scaled field of a struct."""
        out, names = self.out, self.names
        owner = names.tname(item.name)
        for field in scaled_fields(item):
            self.points.exercise("physical", self.profile_name, item.name, field.name)
            getter, setter = function_names(item.name, field)
            getter, setter = f"{names.prefix}_{getter}", f"{names.prefix}_{setter}"
            call = f"{setter}(&value"
            lines = [f"{owner} value{{}};", f"{names.function(item.name, 'init')}(&value);"]
            member = f"value.{field.name}"
            if is_float(field):
                lines += [
                    "constexpr double kInfinity = std::numeric_limits<double>::infinity();",
                    f"EXPECT_TRUE({call}, {number(physical(field, 1.5))}));",
                    f"EXPECT_NEAR({widened(field, member)}, 1.5, 1e-6);",
                    f"EXPECT_NEAR({getter}(&value), {number(physical(field, 1.5))}, 1e-6);",
                    f"EXPECT_FALSE({call}, kInfinity));",
                    f"EXPECT_FALSE({call}, -kInfinity));",
                ]
            else:
                ends = probe(field)
                high = self.assignments.literal(field, ends.high)
                low = self.assignments.literal(field, ends.low)
                zero = self.assignments.literal(field, 0)
                lines += [
                    f"EXPECT_TRUE({call}, {number(physical(field, ends.high))}));",
                    f"EXPECT_EQ({member}, {high});",
                    f"EXPECT_DOUBLE_EQ({getter}(&value), {number(physical(field, ends.high))});",
                    f"EXPECT_TRUE({call}, {number(physical(field, ends.low))}));",
                    f"EXPECT_EQ({member}, {low});",
                    # A little below zero still rounds to the raw value 0.
                    f"EXPECT_TRUE({call}, {number(physical(field, -0.25))}));",
                    f"EXPECT_EQ({member}, {zero});",
                    f"EXPECT_TRUE({call}, {number(physical(field, ends.low))}));",
                    f"EXPECT_FALSE({call}, {number(physical(field, ends.above))}));",
                    f"EXPECT_FALSE({call}, {number(physical(field, ends.below))}));",
                    f"EXPECT_EQ({member}, {low});",
                ]
            for condition in (field.condition, item.condition):
                if condition is not None:
                    out.line(f"#if {names.condition(condition)}")
            _test(out, item.name, f"Physical_{field.name}", lines)
            for condition in (item.condition, field.condition):
                if condition is not None:
                    out.line("#endif")
                    out.line()


def message_test_source(
    project: Project,
    profiles: tuple[ProfileCases, ...],
    layout: LayoutReport | None = None,
    plugins: dict[str, ChecksumPlugin] | None = None,
) -> str:
    """Defaults, boundaries, element counts, decode errors and checksums of the project."""
    return _MessageTests(project, layout, plugins).render(profiles)


# --- golden_test.cpp ---------------------------------------------------------------------------


def golden_test_source(project: Project, vectors: list[dict[str, Any]]) -> str:
    """Every golden vector must round-trip through serialize and deserialize."""
    out = Writer()
    assignments = Assignments(project)
    verifier = Verifier(project)
    _header(out, project)
    for vector in vectors:
        _open_profile(out, vector["profile"])
        message = project.messages[vector["message"]]
        interface = project.interfaces[vector["interface"]]
        api = f"generated_tests::Api{_identifier(message.name)}"
        name = _identifier(f"{vector['interface']}_{vector['message']}_{vector['sample']}")
        out.line(f"TEST(Golden, {name}) {{")
        out.indent()
        _record(out, verifier.shown(verifier.golden(vector)))
        _byte_array(out, "kPayload", bytes.fromhex(vector["payload"]))
        out.line(f"{api}::Type expected{{}};")
        out.line(f"{api}::init(&expected);")
        for statement in assignments.struct(message, vector["value"], "expected"):
            out.line(statement)
        out.line(
            f"const wf_encoding encoding = {encoding_initializer(project, message, interface)};"
        )
        out.line(
            f"EXPECT_TRUE(golden_round_trip<{api}>(expected, kPayload.data(), kPayload.size(), "
            "&encoding));"
        )
        out.dedent()
        out.line("}")
        out.line()
        _close_profile(out, vector["profile"])
    out.line("} // namespace")
    return out.text()

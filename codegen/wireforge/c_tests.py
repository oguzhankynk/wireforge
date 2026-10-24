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
from wireforge.cpp_interface import is_checked_constant, needs_frame_check
from wireforge.llr import Verifier
from wireforge.model import BUILTIN_BITS
from wireforge.scaling import (
    function_names,
    is_float,
    number,
    physical,
    probe,
    scaled_fields,
    widened,
)
from wireforge.test_cases import CLEAR, OK, Edit

if TYPE_CHECKING:
    from collections.abc import Iterator

    from wireforge.codec import ChecksumPlugin
    from wireforge.layout import InterfaceLayout, LayoutReport, MessageLayout, ProfileLayout
    from wireforge.model import Field, Interface, Project, Struct
    from wireforge.test_cases import (
        EncodeCase,
        InterfaceCases,
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
    for interface in sorted(project.interfaces):
        out.line(f'#include "{project.name}/interfaces/{interface}.h"')
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

/// true when the two byte ranges are equal.
inline bool same_bytes(const std::uint8_t* lhs, std::size_t lhs_size, const std::uint8_t* rhs,
                       std::size_t rhs_size) {
    return lhs_size == rhs_size && (lhs_size == 0U || std::memcmp(lhs, rhs, lhs_size) == 0);
}

/// Frames and errors seen while a frame arrives one byte at a time.
struct BytewiseResult {
    std::size_t frames_early = 0U; ///< Frames dispatched before the last byte arrived.
    std::size_t frames = 0U;       ///< Frames dispatched in total.
    std::size_t errors = 0U;       ///< Errors in total.
};

/// Sends @p frame through @p loop one byte at a time and polls after every byte.
template <typename Loop>
BytewiseResult deliver_bytewise(Loop& loop, const std::uint8_t* frame, std::size_t size) {
    BytewiseResult result;
    for (std::size_t index = 0U; index < size; ++index) {
        static_cast<void>(loop.send_frame(&frame[index], 1U));
        const wf_poll_result polled = loop.poll();
        if (index + 1U < size) {
            result.frames_early += polled.frames;
        }
        result.frames += polled.frames;
        result.errors += polled.errors;
    }
    return result;
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


def _header(
    out: Writer, project: Project, system: tuple[str, ...] = (), local: tuple[str, ...] = ()
) -> None:
    out.line(f"// Generated by WireForge from project {project.name}. Do not edit.")
    out.line('#include "test_support.hpp"')
    for include in local:
        out.line(f'#include "{include}"')
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


def _golden_frame(
    project: Project, interface: Interface, message: Struct, frame: bytes, payload: bytes
) -> list[str]:
    """Return the statements that check the frame of a golden vector."""
    names = CNames(project)
    ip = f"{names.prefix}_{interface.name}"
    ok = _status("Ok")
    overhead = len(frame) - len(payload)
    lines = ["", "const std::array<std::uint8_t, " + f"{len(frame)}U> kFrame{{{{"]
    lines += [
        "    " + ", ".join(f"0x{byte:02X}U" for byte in frame[start : start + BYTES_PER_LINE]) + ","
        for start in range(0, len(frame), BYTES_PER_LINE)
    ]
    lines += [
        "}};",
        f"{ip}_header header{{}};",
        f"{ip}_header_init(&header);",
        f"std::array<std::uint8_t, {ip}_MAX_FRAME_SIZE> buffer{{}};",
        "std::size_t size = 0U;",
        (
            f"ASSERT_EQ({ip}_encode_{message.name}(&expected, &header, buffer.data(), "
            "buffer.size(), "
            f"&size), {ok});"
        ),
        (
            "EXPECT_TRUE(wireforge_testing::same_bytes(buffer.data(), size, kFrame.data(), "
            "kFrame.size()));"
        ),
        f"EXPECT_EQ({ip}_decode_header(kFrame.data(), kFrame.size(), &header), {ok});",
    ]
    if overhead > 0:
        lines.append(
            f"EXPECT_EQ({ip}_decode_header(kFrame.data(), {overhead - 1}U, &header), "
            f"{_status('Truncated')});"
        )
    ident = _identifier(interface.name)
    lines += [
        f"generated_tests::Loop_{ident}<{ip}_MAX_FRAME_SIZE> loop;",
        f"ASSERT_EQ(loop.send_frame(kFrame.data(), kFrame.size()), {ok});",
        "EXPECT_EQ(loop.poll().frames, 1U);",
        "EXPECT_EQ(loop.recorder.count(), 1U);",
        (
            "EXPECT_TRUE(wireforge_testing::same_bytes(loop.recorder.last(), "
            "loop.recorder.last_size(), kPayload.data(), kPayload.size()));"
        ),
        f"generated_tests::Loop_{ident}<{ip}_MAX_FRAME_SIZE> sender;",
        f"ASSERT_EQ({ip}_link_send_{message.name}(&sender.link, &expected, &header), {ok});",
        "EXPECT_EQ(sender.poll().frames, 1U);",
        "EXPECT_EQ(sender.recorder.count(), 1U);",
    ]
    return lines


def golden_test_source(project: Project, vectors: list[dict[str, Any]]) -> str:
    """Every golden vector must round-trip through serialize and deserialize."""
    out = Writer()
    assignments = Assignments(project)
    verifier = Verifier(project)
    _header(out, project, local=("handlers.hpp",))
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
        frame = bytes.fromhex(vector["frame"])
        for text in _golden_frame(
            project, interface, message, frame, bytes.fromhex(vector["payload"])
        ):
            out.line(text)
        out.dedent()
        out.line("}")
        out.line()
        _close_profile(out, vector["profile"])
    out.line("} // namespace")
    return out.text()


# --- handlers.hpp ------------------------------------------------------------------------------

LOOPBACK = """\
/// A transport whose bytes sent are the bytes received (a ring of @p N bytes).
template <std::size_t N> class LoopbackTransport {
public:
    LoopbackTransport() {
        wf_ring_init(&ring_, storage_.data(), storage_.size());
        transport_.context = this;
        transport_.send = &LoopbackTransport::send;
        transport_.receive = &LoopbackTransport::receive;
    }
    LoopbackTransport(const LoopbackTransport&) = delete;
    LoopbackTransport& operator=(const LoopbackTransport&) = delete;
    /// The transport for `<interface>_link_init`.
    const wf_transport* transport() const { return &transport_; }

private:
    static std::size_t send(void* context, const std::uint8_t* bytes, std::size_t length) {
        return wf_ring_write(&static_cast<LoopbackTransport*>(context)->ring_, bytes, length);
    }
    static std::size_t receive(void* context, std::uint8_t* buffer, std::size_t capacity) {
        return wf_ring_read(&static_cast<LoopbackTransport*>(context)->ring_, buffer, capacity);
    }
    std::array<std::uint8_t, N> storage_{};
    wf_ring ring_{};
    wf_transport transport_{};
};

"""


def handlers_header(project: Project) -> str:
    """Return handlers.hpp: a recording handler and a loopback link per interface."""
    out = Writer()
    out.line("// Generated by WireForge. Do not edit; regenerate instead.")
    out.line("//")
    out.line(f"// Recording handlers of the {project.name} interfaces for the generated tests.")
    out.line("#ifndef WIREFORGE_GENERATED_C_TEST_HANDLERS_HPP")
    out.line("#define WIREFORGE_GENERATED_C_TEST_HANDLERS_HPP")
    out.line()
    out.line('#include "api.hpp"')
    out.line()
    for header in ("algorithm", "array", "cstddef", "cstdint"):
        out.line(f"#include <{header}>")
    out.line()
    out.line('#include "wireforge/c/ring.h"')
    out.line()
    out.line("namespace generated_tests {")
    out.line()
    for text in LOOPBACK.splitlines():
        out.line(text)
    names = CNames(project)
    for key in sorted(project.interfaces):
        interface = project.interfaces[key]
        _recorder(out, names, interface)
        _raw_recorder(out, names, interface)
    out.line("} // namespace generated_tests")
    out.line()
    out.line("#endif // WIREFORGE_GENERATED_C_TEST_HANDLERS_HPP")
    return out.text()


RECORDER = """\
/// Records the messages received on interface @INTERFACE@.
class Recorder_@ID@ {
public:
    Recorder_@ID@() {
        handler_.context = this;
@SETUP@
    }
    Recorder_@ID@(const Recorder_@ID@&) = delete;
    Recorder_@ID@& operator=(const Recorder_@ID@&) = delete;
    const @IP@_handler& handler() const { return handler_; }
    /// The re-encoded payload of the last message received.
    const std::uint8_t* last() const { return bytes_.data(); }
    std::size_t last_size() const { return size_; }
    /// Number of messages received.
    std::size_t count() const { return count_; }
    /// Number of messages received at index @p index of the message list.
    std::size_t count(std::size_t index) const { return counts_.at(index); }

private:
@CALLBACKS@
    @IP@_handler handler_{};
    std::array<std::uint8_t, @IP@_MAX_FRAME_SIZE> bytes_{};
    std::size_t size_ = 0U;
    std::size_t count_ = 0U;
    std::array<std::size_t, @COUNT@U> counts_{};
};

/// Recorder and loopback link of interface @INTERFACE@.
template <std::size_t N> struct Loop_@ID@ {
    Recorder_@ID@ recorder;
    LoopbackTransport<N> transport;
    @IP@_link link{};
    Loop_@ID@() { @IP@_link_init(&link, transport.transport(), &recorder.handler()); }
    wf_status send_frame(const std::uint8_t* frame, std::size_t size) {
        return @IP@_link_send_frame(&link, frame, size);
    }
    wf_poll_result poll() { return @IP@_link_poll(&link); }
};

"""

RAW_RECORDER = """\
/// Keeps the raw bytes of the messages received on interface @INTERFACE@.
class RawRecorder_@ID@ {
public:
    /// With @p decode the raw callbacks hand the message on to the default decoder.
    explicit RawRecorder_@ID@(bool decode) : decode_(decode) {
        handler_.context = this;
@SETUP@
    }
    RawRecorder_@ID@(const RawRecorder_@ID@&) = delete;
    RawRecorder_@ID@& operator=(const RawRecorder_@ID@&) = delete;
    const @IP@_handler& handler() const { return handler_; }
    const std::uint8_t* payload() const { return payload_.data(); }
    std::size_t payload_size() const { return payload_size_; }
    const std::uint8_t* frame() const { return frame_.data(); }
    std::size_t frame_size() const { return frame_size_; }
    std::size_t raw_count() const { return raw_count_; }
    std::size_t decoded_count() const { return decoded_count_; }

private:
@CALLBACKS@
    using Bytes = std::array<std::uint8_t, @IP@_MAX_FRAME_SIZE>;
    static std::size_t copy(const std::uint8_t* bytes, std::size_t size, Bytes& target) {
        const std::size_t count = std::min(size, target.size());
        std::copy_n(bytes, count, target.begin());
        return count;
    }
    void keep(const wf_raw_message& raw) {
        ++raw_count_;
        payload_size_ = copy(raw.payload, raw.payload_size, payload_);
        frame_size_ = copy(raw.frame, raw.frame_size, frame_);
    }
    bool decode_;
    @IP@_handler handler_{};
    Bytes payload_{};
    Bytes frame_{};
    std::size_t payload_size_ = 0U;
    std::size_t frame_size_ = 0U;
    std::size_t raw_count_ = 0U;
    std::size_t decoded_count_ = 0U;
};

/// Raw recorder and loopback link of interface @INTERFACE@.
template <std::size_t N, bool Decode> struct RawLoop_@ID@ {
    RawRecorder_@ID@ recorder{Decode};
    LoopbackTransport<N> transport;
    @IP@_link link{};
    RawLoop_@ID@() { @IP@_link_init(&link, transport.transport(), &recorder.handler()); }
    wf_status send_frame(const std::uint8_t* frame, std::size_t size) {
        return @IP@_link_send_frame(&link, frame, size);
    }
    wf_poll_result poll() { return @IP@_link_poll(&link); }
};

"""

RECEIVED = """\
    static void received_@NAME@(void* context, const @TYPE@* message) {
        auto* self = static_cast<Recorder_@ID@*>(context);
        ++self->count_;
        ++self->counts_.at(@INDEX@U);
        static_cast<void>(@SERIALIZE@(message, self->bytes_.data(), self->bytes_.size(),
                                      &self->size_, &@IP@_@NAME@_encoding));
    }
"""

RAW_CALLBACKS = """\
    static wf_status raw_@NAME@(void* context, const wf_raw_message* raw) {
        auto* self = static_cast<RawRecorder_@ID@*>(context);
        self->keep(*raw);
        return self->decode_ ? @IP@_@NAME@_default_raw(&self->handler_, raw)
                             : static_cast<wf_status>(WF_OK);
    }
    static void decoded_@NAME@(void* context, const @TYPE@* message) {
        static_cast<void>(message);
        ++static_cast<RawRecorder_@ID@*>(context)->decoded_count_;
    }
"""


def _fill(template: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        template = template.replace(f"@{key}@", value)
    return template


def _guarded(names: CNames, condition: str | None, text: str) -> str:
    if condition is None:
        return text
    return f"#if {names.condition(condition)}\n{text}\n#endif"


def _recorder(out: Writer, names: CNames, interface: Interface) -> None:
    ip = f"{names.prefix}_{interface.name}"
    ident = _identifier(interface.name)
    setup, callbacks = [], []
    for index, entry in enumerate(interface.messages):
        name = entry.name
        setup.append(
            _guarded(
                names,
                entry.condition,
                f"        handler_.on_{name}_received = &Recorder_{ident}::received_{name};",
            )
        )
        values = {
            "NAME": name,
            "TYPE": names.tname(name),
            "ID": ident,
            "INDEX": str(index),
            "IP": ip,
            "SERIALIZE": names.function(name, "serialize_with"),
        }
        callbacks.append(_guarded(names, entry.condition, _fill(RECEIVED, values).rstrip("\n")))
    text = _fill(
        RECORDER,
        {
            "INTERFACE": interface.name,
            "ID": ident,
            "IP": ip,
            "COUNT": str(max(len(interface.messages), 1)),
            "SETUP": "\n".join(setup),
            "CALLBACKS": "\n".join(callbacks),
        },
    )
    for line in text.splitlines():
        out.line(line)


def _raw_recorder(out: Writer, names: CNames, interface: Interface) -> None:
    ip = f"{names.prefix}_{interface.name}"
    ident = _identifier(interface.name)
    setup, callbacks = [], []
    for entry in interface.messages:
        name = entry.name
        both = (
            f"        handler_.on_{name}_raw = &RawRecorder_{ident}::raw_{name};\n"
            f"        handler_.on_{name}_received = &RawRecorder_{ident}::decoded_{name};"
        )
        setup.append(_guarded(names, entry.condition, both))
        values = {"NAME": name, "TYPE": names.tname(name), "ID": ident, "IP": ip}
        callbacks.append(
            _guarded(names, entry.condition, _fill(RAW_CALLBACKS, values).rstrip("\n"))
        )
    text = _fill(
        RAW_RECORDER,
        {
            "INTERFACE": interface.name,
            "ID": ident,
            "IP": ip,
            "SETUP": "\n".join(setup),
            "CALLBACKS": "\n".join(callbacks),
        },
    )
    for line in text.splitlines():
        out.line(line)


# --- interface_test.cpp ------------------------------------------------------------------------


def _header_size(names: CNames, interface: Interface) -> int:
    return sum(
        (item.bits or BUILTIN_BITS[names.base(item.type)]) // 8 for item in interface.header or ()
    )


class _InterfaceTests:
    """Writes interface_test.cpp."""

    def __init__(self, project: Project, layout: LayoutReport | None) -> None:
        self.project = project
        self.out = Writer()
        self.layouts = _layouts(layout)
        self.verifier = Verifier(project)
        self.names = CNames(project)
        self.points = Points("interface_test.cpp")
        self.profile_name: str | None = None

    def render(self, profiles: tuple[ProfileCases, ...]) -> str:
        out = self.out
        _header(out, self.project, ("algorithm",), ("handlers.hpp",))
        for profile in profiles:
            _open_profile(out, profile.profile)
            layout = self.layouts.get(profile.profile)
            if layout is not None:
                for sized in layout.interfaces:
                    keys = self.verifier.interface_test(
                        self.project.interfaces[sized.name], "WireSize"
                    )
                    self.wire_size(sized, self.verifier.shown(keys))
            self.profile(profile)
            _close_profile(out, profile.profile)
        out.line("} // namespace")
        self.points.check()
        return out.text()

    def wire_size(self, interface: InterfaceLayout, requirements: tuple[str, ...]) -> None:
        ip = f"{self.names.prefix}_{interface.name}"
        checks = (
            ("HEADER_SIZE", interface.header_size),
            ("TRAILER_SIZE", interface.trailer_size),
            ("MAX_PAYLOAD_SIZE", interface.max_payload),
            ("MAX_FRAME_SIZE", interface.max_frame),
            ("RX_BUFFER_BYTES", interface.rx_buffer),
            ("TX_BUFFER_BYTES", interface.tx_buffer),
        )
        lines = [
            f'static_assert({ip}_{name} == {value}U, "{name} of the layout analyzer");'
            for name, value in checks
        ]
        _test(self.out, f"Interface_{_identifier(interface.name)}", "WireSize", lines, requirements)

    def profile(self, cases: ProfileCases) -> None:
        self.profile_name = cases.profile
        messages = {item.message.name: item for item in cases.messages}
        for interface in cases.interfaces:
            self.require(interface, messages)
            self.interface(interface, messages)

    def exercise(self, kind: str, cases: InterfaceCases, message: str = "") -> None:
        self.points.exercise(kind, self.profile_name, cases.interface.name, message)

    def require(self, cases: InterfaceCases, messages: dict[str, MessageCases]) -> None:
        """Declare what the tests of an interface must exercise, from the cases of the model."""
        interface = cases.interface
        profile, name = self.profile_name, interface.name
        self.points.require("corrupt", profile, name)
        if cases.frames:
            self.points.require("raw_forward", profile, name)
            self.points.require("ignored", profile, name)
        if interface.framing.kind != "none":
            self.points.require("byte_by_byte", profile, name)
        if cases.sync:
            self.points.require("garbage", profile, name)
        if self.has_id(interface) and cases.frames:
            self.points.require("unknown_id", profile, name)
        if cases.frames and (interface.header or interface.trailer):
            self.points.require("short_frame", profile, name)
        self.require_messages(cases, messages)

    def require_messages(self, cases: InterfaceCases, messages: dict[str, MessageCases]) -> None:
        """Declare what the tests must exercise for every message of an interface."""
        interface = cases.interface
        profile, name = self.profile_name, interface.name
        for index, _ in cases.frames:
            message = interface.messages[index].name
            for kind in ("dispatch", "raw_delivery"):
                self.points.require(kind, profile, name, message)
            if needs_frame_check(interface):
                self.points.require("receiver_check", profile, name, message)
        for index, size, _ in cases.defaults:
            if size > 0:
                self.points.require("frame_room", profile, name, interface.messages[index].name)
        for index, _ in cases.raw_errors:
            message = interface.messages[index].name
            if message in messages:
                self.points.require("raw_decode_error", profile, name, message)

    @staticmethod
    def has_id(interface: Interface) -> bool:
        return any(item.role == "message_id" for item in interface.header or ())

    def interface(self, cases: InterfaceCases, messages: dict[str, MessageCases]) -> None:
        interface = cases.interface
        suite = f"Interface_{_identifier(interface.name)}"
        framed = interface.framing.kind != "none"
        self.dispatch(cases, suite, framed=framed)
        if framed:
            self.bytewise(cases, suite)
        if cases.sync:
            self.garbage(cases, suite)
        self.corrupt(cases, suite)
        self.broken_frames(cases, suite, framed=framed)
        self.too_small(cases, suite)
        if cases.frames:
            self.raw(cases, suite)
            self.raw_and_decoded(cases, suite)
            self.raw_decode_errors(cases, messages, suite)
            self.ignored(cases, suite)
            self.short_frame(cases, suite)
            if self.has_id(interface):
                self.unknown_id(cases, suite)

    def requirements(
        self,
        cases: InterfaceCases,
        name: str,
        *,
        corrupt_status: str = OK,
        decode_keys: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        keys = self.verifier.interface_test(
            cases.interface, name, corrupt_status=corrupt_status, decode_keys=decode_keys
        )
        return self.verifier.shown(keys)

    def ip(self, cases: InterfaceCases) -> str:
        return f"{self.names.prefix}_{cases.interface.name}"

    @staticmethod
    def loop(cases: InterfaceCases, capacity: int) -> str:
        name = _identifier(cases.interface.name)
        return f"generated_tests::Loop_{name}<{max(capacity, 1)}U> loop;"

    @staticmethod
    def raw_loop(cases: InterfaceCases, capacity: int, *, decode: bool) -> str:
        name = _identifier(cases.interface.name)
        flag = "true" if decode else "false"
        return f"generated_tests::RawLoop_{name}<{max(capacity, 1)}U, {flag}> loop;"

    @staticmethod
    def frames(lines: Writer, cases: InterfaceCases) -> None:
        for position, (_, frame) in enumerate(cases.frames):
            _byte_array(lines, f"kFrame{position}", frame)

    def dispatch(self, cases: InterfaceCases, suite: str, *, framed: bool) -> None:
        for index, _ in cases.frames:
            self.exercise("dispatch", cases, cases.interface.messages[index].name)
        lines = Writer()
        self.frames(lines, cases)
        lines.line(self.loop(cases, sum(len(frame) for _, frame in cases.frames)))
        for position in range(len(cases.frames)):
            frame = f"kFrame{position}.data(), kFrame{position}.size()"
            lines.line(f"ASSERT_EQ(loop.send_frame({frame}), {_status('Ok')});")
            if not framed:
                lines.line("EXPECT_EQ(loop.poll().frames, 1U);")
        if framed:
            lines.line("const wf_poll_result polled = loop.poll();")
            lines.line(f"EXPECT_EQ(polled.frames, {len(cases.frames)}U);")
            lines.line("EXPECT_EQ(polled.errors, 0U);")
        for index, _ in cases.frames:
            lines.line(f"EXPECT_EQ(loop.recorder.count({index}U), 1U);")
        _test(
            self.out,
            suite,
            "DispatchesEveryMessage",
            lines.lines,
            self.requirements(cases, "DispatchesEveryMessage"),
        )

    def bytewise(self, cases: InterfaceCases, suite: str) -> None:
        self.exercise("byte_by_byte", cases)
        lines = Writer()
        frame = cases.frames[0][1]
        _byte_array(lines, "kFrame", frame)
        lines.line(self.loop(cases, len(frame)))
        lines.line(
            f"const {SUPPORT_NAMESPACE}BytewiseResult result = "
            f"{SUPPORT_NAMESPACE}deliver_bytewise(loop, kFrame.data(), kFrame.size());"
        )
        lines.line("EXPECT_EQ(result.frames_early, 0U);")
        lines.line("EXPECT_EQ(result.frames, 1U);")
        lines.line("EXPECT_EQ(result.errors, 0U);")
        _test(
            self.out,
            suite,
            "DeliversByteByByte",
            lines.lines,
            self.requirements(cases, "DeliversByteByByte"),
        )

    def garbage(self, cases: InterfaceCases, suite: str) -> None:
        self.exercise("garbage", cases)
        lines = Writer()
        frame = cases.frames[0][1]
        junk = bytes([cases.sync[0] ^ 0xFF]) * 3
        _byte_array(lines, "kGarbage", junk)
        _byte_array(lines, "kFrame", frame)
        lines.line(self.loop(cases, len(junk) + len(frame)))
        ok = _status("Ok")
        lines.line(f"ASSERT_EQ(loop.send_frame(kGarbage.data(), kGarbage.size()), {ok});")
        lines.line(f"ASSERT_EQ(loop.send_frame(kFrame.data(), kFrame.size()), {ok});")
        lines.line("const wf_poll_result polled = loop.poll();")
        lines.line("EXPECT_EQ(polled.frames, 1U);")
        lines.line("EXPECT_EQ(polled.errors, 0U);")
        _test(
            self.out,
            suite,
            "SkipsBytesBeforeSync",
            lines.lines,
            self.requirements(cases, "SkipsBytesBeforeSync"),
        )

    def corrupt(self, cases: InterfaceCases, suite: str) -> None:
        self.exercise("corrupt", cases)
        lines = Writer()
        frame = cases.frames[0][1]
        _byte_array(lines, "kCorrupt", cases.corrupt)
        if cases.exact:
            _byte_array(lines, "kFrame", frame)
        ok = _status("Ok")
        lines.line(self.loop(cases, len(frame)))
        lines.line(f"ASSERT_EQ(loop.send_frame(kCorrupt.data(), kCorrupt.size()), {ok});")
        lines.line("const wf_poll_result rejected = loop.poll();")
        if cases.corrupt_status == OK:
            lines.line("EXPECT_EQ(rejected.frames, 1U);")
            lines.line("EXPECT_EQ(rejected.errors, 0U);")
        else:
            lines.line("EXPECT_EQ(rejected.frames, 0U);")
            lines.line("EXPECT_EQ(loop.recorder.count(), 0U);")
            if cases.exact:
                lines.line("EXPECT_EQ(rejected.errors, 1U);")
                lines.line(f"EXPECT_EQ(rejected.last_error, {_status(cases.corrupt_status)});")
            else:
                lines.line("EXPECT_GE(rejected.errors, 1U);")
        if cases.exact:
            lines.line(f"ASSERT_EQ(loop.send_frame(kFrame.data(), kFrame.size()), {ok});")
            lines.line("EXPECT_EQ(loop.poll().frames, 1U);")
        status = cases.corrupt_status
        requirements = self.requirements(cases, "RejectsCorruptedFrame", corrupt_status=status)
        _test(self.out, suite, "RejectsCorruptedFrame", lines.lines, requirements)

    def raw(self, cases: InterfaceCases, suite: str) -> None:
        for index, _ in cases.frames:
            self.exercise("raw_delivery", cases, cases.interface.messages[index].name)
        ip = self.ip(cases)
        lines = Writer()
        self.frames(lines, cases)
        capacity = max(len(frame) for _, frame in cases.frames)
        lines.line(self.raw_loop(cases, capacity, decode=False))
        ok = _status("Ok")
        for position in range(len(cases.frames)):
            frame = f"kFrame{position}.data(), kFrame{position}.size()"
            payload = (
                f"kFrame{position}.data() + {ip}_HEADER_SIZE, "
                f"kFrame{position}.size() - {ip}_HEADER_SIZE - {ip}_TRAILER_SIZE"
            )
            same = f"{SUPPORT_NAMESPACE}same_bytes"
            lines.line(f"ASSERT_EQ(loop.send_frame({frame}), {ok});")
            lines.line("EXPECT_EQ(loop.poll().frames, 1U);")
            lines.line(
                f"EXPECT_TRUE({same}(loop.recorder.frame(), loop.recorder.frame_size(), {frame}));"
            )
            lines.line(
                f"EXPECT_TRUE({same}(loop.recorder.payload(), loop.recorder.payload_size(), "
                f"{payload}));"
            )
        lines.line(f"EXPECT_EQ(loop.recorder.raw_count(), {len(cases.frames)}U);")
        lines.line("EXPECT_EQ(loop.recorder.decoded_count(), 0U);")
        _test(
            self.out,
            suite,
            "DeliversRawBytesWithoutDecoding",
            lines.lines,
            self.requirements(cases, "DeliversRawBytesWithoutDecoding"),
        )

    def raw_and_decoded(self, cases: InterfaceCases, suite: str) -> None:
        self.exercise("raw_forward", cases)
        lines = Writer()
        frame = cases.frames[0][1]
        _byte_array(lines, "kFrame", frame)
        lines.line(self.raw_loop(cases, len(frame), decode=True))
        ok = _status("Ok")
        lines.line(f"ASSERT_EQ(loop.send_frame(kFrame.data(), kFrame.size()), {ok});")
        lines.line("const wf_poll_result polled = loop.poll();")
        lines.line("EXPECT_EQ(polled.frames, 1U);")
        lines.line("EXPECT_EQ(polled.errors, 0U);")
        lines.line("EXPECT_EQ(loop.recorder.raw_count(), 1U);")
        lines.line("EXPECT_EQ(loop.recorder.decoded_count(), 1U);")
        _test(
            self.out,
            suite,
            "RawCanForwardToDecoder",
            lines.lines,
            self.requirements(cases, "RawCanForwardToDecoder"),
        )

    def raw_decode_errors(
        self, cases: InterfaceCases, messages: dict[str, MessageCases], suite: str
    ) -> None:
        """Check that the default raw hook returns a decode error and keeps the payload out."""
        interface = cases.interface
        identifier = _identifier(interface.name)
        ip = self.ip(cases)
        for index, failing in cases.raw_errors:
            entry = interface.messages[index]
            if entry.name not in messages:
                continue
            self.exercise("raw_decode_error", cases, entry.name)
            lines = Writer()
            _byte_array(lines, "kPayload", failing.payload)
            lines.line(f"generated_tests::RawRecorder_{identifier} recorder(true);")
            lines.line(
                "const wf_raw_message raw{kPayload.data(), kPayload.size(), kPayload.data(), "
                "kPayload.size()};"
            )
            lines.line(
                f"EXPECT_EQ({ip}_{entry.name}_default_raw(&recorder.handler(), &raw), "
                f"{_status(failing.status)});"
            )
            lines.line("EXPECT_EQ(recorder.decoded_count(), 0U);")
            keys = self.verifier.decode_case(self.project.messages[entry.name], failing)
            name = f"RawDecodeErrorOf{entry.name}"
            _test(
                self.out, suite, name, lines.lines, self.requirements(cases, name, decode_keys=keys)
            )

    def ignored(self, cases: InterfaceCases, suite: str) -> None:
        """Check that a handler without callbacks accepts every message and does nothing."""
        self.exercise("ignored", cases)
        ip = self.ip(cases)
        lines = Writer()
        self.frames(lines, cases)
        lines.line(f"const {ip}_handler none{{}};")
        for position in range(len(cases.frames)):
            lines.line(
                f"EXPECT_EQ({ip}_dispatch_frame(&none, kFrame{position}.data(), "
                f"kFrame{position}.size()), {_status('Ok')});"
            )
        _test(
            self.out,
            suite,
            "IgnoresMessagesWithoutCallbacks",
            lines.lines,
            self.requirements(cases, "IgnoresMessagesWithoutCallbacks"),
        )

    def short_frame(self, cases: InterfaceCases, suite: str) -> None:
        """Check that a frame shorter than header and trailer is refused by the dispatcher."""
        interface = cases.interface
        if not (interface.header or interface.trailer):
            return
        self.exercise("short_frame", cases)
        ip = self.ip(cases)
        lines = Writer()
        _byte_array(lines, "kFrame", cases.frames[0][1])
        lines.line(f"generated_tests::Recorder_{_identifier(interface.name)} recorder;")
        lines.line(f"constexpr std::size_t kOverhead = {ip}_HEADER_SIZE + {ip}_TRAILER_SIZE;")
        lines.line(
            f"EXPECT_EQ({ip}_dispatch_frame(&recorder.handler(), kFrame.data(), kOverhead - 1U), "
            f"{_status('Truncated')});"
        )
        lines.line("EXPECT_EQ(recorder.count(), 0U);")
        _test(
            self.out,
            suite,
            "RejectsFramesShorterThanTheirOverhead",
            lines.lines,
            self.requirements(cases, "RejectsFramesShorterThanTheirOverhead"),
        )

    def unknown_id(self, cases: InterfaceCases, suite: str) -> None:
        """Check that a frame with an id of no message is refused by the dispatcher."""
        interface = cases.interface
        self.exercise("unknown_id", cases)
        ip = self.ip(cases)
        offset = 0
        field = None
        for item in interface.header or ():
            if item.role == "message_id":
                field = item
                break
            offset += (item.bits or BUILTIN_BITS[self.names.base(item.type)]) // 8
        if field is None:
            return
        width = (field.bits or BUILTIN_BITS[self.names.base(field.type)]) // 8
        endian = interface.endian or self.project.defaults.get("endian") or "big"
        known: set[int | None] = set()
        for entry in interface.messages:
            info = self.project.messages[entry.name].message
            known.add(entry.id if entry.id is not None else (info.id if info else None))
        unknown = next(value for value in range(1 << (8 * width)) if value not in known)
        frame = bytearray(cases.frames[0][1])
        encoded = (
            unknown.to_bytes(width, "big") if endian == "big" else unknown.to_bytes(width, "little")
        )
        frame[offset : offset + width] = encoded
        lines = Writer()
        _byte_array(lines, "kFrame", bytes(frame))
        lines.line(f"generated_tests::Recorder_{_identifier(interface.name)} recorder;")
        lines.line(
            f"EXPECT_EQ({ip}_dispatch_frame(&recorder.handler(), kFrame.data(), kFrame.size()), "
            f"{_status('UnknownMessageId')});"
        )
        lines.line("EXPECT_EQ(recorder.count(), 0U);")
        _test(
            self.out,
            suite,
            "RejectsUnknownMessageId",
            lines.lines,
            self.requirements(cases, "RejectsUnknownMessageId"),
        )

    def broken_frames(self, cases: InterfaceCases, suite: str, *, framed: bool) -> None:
        """Check that a header constant or a checksum that the receiver checks is enforced."""
        interface = cases.interface
        ip = self.ip(cases)
        offset = 0
        position: int | None = None
        for item in interface.header or ():
            if is_checked_constant(item, framed=framed):
                position = offset
                break
            offset += (item.bits or BUILTIN_BITS[self.names.base(item.type)]) // 8
        checksum = not framed and any(item.checksum for item in interface.trailer)
        if (position is None and not checksum) or not cases.frames:
            return
        for index, _ in cases.frames:
            self.exercise("receiver_check", cases, interface.messages[index].name)
        lines = Writer()
        lines.line(f"generated_tests::Recorder_{_identifier(interface.name)} recorder;")
        for _, frame in cases.frames:
            lines.line("{")
            lines.depth += 1
            _byte_array(lines, "kFrame", frame)
            changes = [(position, "ConstMismatch")] if position is not None else []
            changes += [(len(frame) - 1, "ChecksumMismatch")] if checksum else []
            for at, status in changes:
                lines.line(f"std::array<std::uint8_t, {len(frame)}U> broken{status} = kFrame;")
                lines.line(
                    f"broken{status}[{at}U] = "
                    f"static_cast<std::uint8_t>(broken{status}[{at}U] ^ 0xFFU);"
                )
                lines.line(
                    f"EXPECT_EQ({ip}_dispatch_frame(&recorder.handler(), broken{status}.data(), "
                    f"broken{status}.size()), {_status(status)});"
                )
            lines.depth -= 1
            lines.line("}")
        lines.line("EXPECT_EQ(recorder.count(), 0U);")
        name = "RejectsBrokenFrames"
        _test(self.out, suite, name, lines.lines, self.requirements(cases, name))

    def too_small(self, cases: InterfaceCases, suite: str) -> None:
        interface = cases.interface
        sized = [item for item in cases.defaults if item[1] > 0]
        if not sized:
            return
        ip = self.ip(cases)
        for index, _, _ in sized:
            self.exercise("frame_room", cases, interface.messages[index].name)
        lines = [
            f"std::array<std::uint8_t, {max(item[2] for item in sized)}U> buffer{{}};",
            "std::size_t size = 1U;",
            f"{ip}_header header{{}};",
            f"{ip}_header_init(&header);",
        ]
        for index, frame_size, overhead in sized:
            message = interface.messages[index].name
            type_name = self.names.tname(message)
            lines += [
                "{",
                f"    {type_name} value{{}};",
                f"    {self.names.function(message, 'init')}(&value);",
            ]
            rooms = [0]
            if 0 < overhead < frame_size:
                rooms.append(overhead)  # room for header and trailer, none for the payload
            for room in rooms:
                call = f"{ip}_encode_{message}(&value, &header, buffer.data(), {room}U, &size)"
                lines += [
                    f"    EXPECT_EQ({call}, {_status('BufferTooSmall')});",
                    "    EXPECT_EQ(size, 0U);",
                    "    size = 1U;",
                ]
            lines.append("}")
        _test(
            self.out,
            suite,
            "EncodeFrameNeedsRoom",
            lines,
            self.requirements(cases, "EncodeFrameNeedsRoom"),
        )


def interface_test_source(
    project: Project, profiles: tuple[ProfileCases, ...], layout: LayoutReport | None = None
) -> str:
    """Dispatch, framing and error handling of every interface."""
    return _InterfaceTests(project, layout).render(profiles)

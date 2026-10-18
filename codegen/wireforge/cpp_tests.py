"""GoogleTest sources of the generated test project.

The expectations come from the reference codec (`wireforge.golden` vectors and
`wireforge.test_cases`), so the generated C++ is checked against the model's reference semantics:

- `golden_test.cpp`: every golden vector round-trips through serialize/deserialize, encodes to the
  golden frame and loops back through Link, Endpoint and Handler;
- `message_test.cpp`: defaults, the pattern value, boundaries, element counts and decode errors
  of every message;
- `interface_test.cpp`: dispatch of every message, byte-wise delivery, garbage before a frame,
  corrupted frames and too small buffers per interface;
- `handlers.hpp`: a recording Handler per interface;
- `test_support.hpp`, `wire_support.hpp`: assertion and byte helpers;
- `diff_harness.cpp`: line-based harness of `wireforge.differential`;
- `fuzz_target.cpp`: libFuzzer target over every message and interface.

Every test records the ids of the requirements it verifies (`RecordProperty("requirements", ...)`,
see `wireforge.llr`); the trace report reads them back from the sources and gtest writes them into
its XML result.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from wireforge.cpp import Names, Writer, string_literal, wire_only
from wireforge.cpp_codec import encoding_literal
from wireforge.cpp_interface import header_is_written, is_checked_constant
from wireforge.llr import Verifier
from wireforge.model import BUILTIN_BITS, Field
from wireforge.test_cases import CLEAR, OK, Edit, Outcome

if TYPE_CHECKING:
    from collections.abc import Iterator

    from wireforge.layout import InterfaceLayout, LayoutReport, MessageLayout, ProfileLayout
    from wireforge.model import Field, Interface, Project, Struct, Vector
    from wireforge.test_cases import (
        EncodeCase,
        InterfaceCases,
        MessageCases,
        ProfileCases,
    )
    from wireforge.vectors import VectorRun

BYTES_PER_LINE = 12
BYTE_BITS = 8
SPAN = "wireforge::Span<std::uint8_t>"
CSPAN = "wireforge::Span<const std::uint8_t>"
STATUS = "wireforge::Status::"
SUPPORT = "wireforge::testing::"
FLOAT_LITERAL = re.compile(r"^-?(?=[\d.eE+-]*[.eEFf])\d+(\.\d*)?([eE][-+]?\d+)?[Ff]?$")


def _has_user_type(project: Project, fields: tuple[Field, ...]) -> bool:
    """Whether any field (recursively, through nested structs) is an opaque user type."""
    for field in fields:
        if field.type in project.user_types:
            return True
        inner = project.types.get(field.type)
        if inner is not None and _has_user_type(project, inner.fields):
            return True
    return False


def _identifier(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "_", text)


def _byte_lines(data: bytes) -> list[str]:
    chunks = [data[index : index + BYTES_PER_LINE] for index in range(0, len(data), BYTES_PER_LINE)]
    return ["    " + ", ".join(f"0x{byte:02X}U" for byte in chunk) + "," for chunk in chunks]


def _scalar_bytes(names: Names, item: Field) -> int:
    """Return the wire size of a byte-aligned integer header field."""
    return BUILTIN_BITS[names.base(item.type)] // BYTE_BITS


def _byte_array(out: Writer, name: str, data: bytes, *, static: bool = True) -> None:
    """Declare a std::array holding `data`."""
    prefix = "static constexpr " if static else "constexpr "
    if not data:
        out.line(f"{prefix}std::array<std::uint8_t, 0U> {name}{{}};")
        return
    out.line(f"{prefix}std::array<std::uint8_t, {len(data)}U> {name}{{{{")
    out.depth -= 1
    for line in _byte_lines(data):
        out.line(line)
    out.depth += 1
    out.line("}};")


class _Assignments:
    """C++ statements that build a value from its JSON form."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.names = Names(project)

    def literal(self, item: Field, value: object) -> str:
        if not isinstance(value, (bool, int, float, str)):
            message = f"{item.name}: {value!r} is not a scalar"
            raise TypeError(message)
        return self.names.literal(item, value, "model::")

    def struct(self, item: Struct, value: dict[str, Any], target: str) -> list[str]:
        hidden = wire_only(item)
        lines: list[str] = []
        for field in item.fields:
            if field.name in hidden or field.name not in value:
                continue
            lines += self.field(field, value[field.name], f"{target}.{field.name}")
        return lines

    def field(self, item: Field, value: object, target: str) -> list[str]:
        if item.type in self.project.user_types:
            return []  # opaque N-byte value: left default-constructed, no assignment emitted
        inner = self.project.types.get(item.type)
        if item.occurs is None:
            if inner is not None and isinstance(value, dict):
                return self.struct(inner, value, target)
            return [f"{target} = {self.literal(item, value)};"]
        if item.type == "char":
            return [f"static_cast<void>({target}.assign({string_literal(str(value))}));"]
        elements = value if isinstance(value, list) else []
        dynamic = item.occurs.count is not None or item.occurs.fill
        lines = [f"static_cast<void>({target}.resize({len(elements)}U));"] if dynamic else []
        for index, element in enumerate(elements):
            slot = f"{target}[{index}U]"
            if inner is not None and isinstance(element, dict):
                lines += self.struct(inner, element, slot)
            else:
                lines.append(f"{slot} = {self.literal(item, element)};")
        return lines

    def edit(self, edit: Edit) -> str:
        target = f"value.{edit.target}"
        if edit.value is CLEAR:
            return f"{target}.clear();"
        return f"{target} = {self.literal(edit.field, edit.value)};"


TEST_SYSTEM_HEADERS = ("array", "cstddef", "cstdint", "gtest/gtest.h")


def _header(
    out: Writer,
    project: Project,
    includes: list[str],
    system: tuple[str, ...] = TEST_SYSTEM_HEADERS,
) -> None:
    out.line(f"// Generated by WireForge from project {project.name}. Do not edit.")
    out.line(f'#include "{project.name}/model.hpp"')
    for include in includes:
        out.line(f'#include "{include}"')
    out.line()
    for header in system:
        out.line(f"#include <{header}>")
    out.line()
    out.line("namespace {")
    out.line()
    out.line(f"namespace model = {project.namespace or project.name};")
    out.line()


def _interface_includes(project: Project) -> list[str]:
    return [f"{project.name}/interfaces/{name}.hpp" for name in sorted(project.interfaces)]


def _open_profile(out: Writer, profile: str | None) -> None:
    if profile:
        out.line(f"#if defined(WIREFORGE_PROFILE_{profile})")
        out.line()


def _close_profile(out: Writer, profile: str | None) -> None:
    if profile:
        out.line(f"#endif // WIREFORGE_PROFILE_{profile}")
        out.line()


def _record(out: Writer, ids: tuple[str, ...]) -> None:
    """Record the requirement ids a test verifies (nothing for an empty list)."""
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


# --- test_support.hpp -------------------------------------------------------------------------

WIRE_SUPPORT_HEADER = """\
/**
 * @file wire_support.hpp
 * @brief Byte helpers of the generated tests, harness and fuzz target (no GoogleTest).
 *
 * Generated by WireForge. Do not edit; regenerate instead.
 */
#ifndef WIREFORGE_GENERATED_WIRE_SUPPORT_HPP
#define WIREFORGE_GENERATED_WIRE_SUPPORT_HPP

#include <array>
#include <cstddef>
#include <cstdint>
#include <iomanip>
#include <sstream>
#include <string>
#include <wireforge/codec_support.hpp>
#include <wireforge/link.hpp>
#include <wireforge/span.hpp>
#include <wireforge/status.hpp>

namespace wireforge::testing {

/// @brief Hex dump of @p bytes for failure messages.
inline std::string hex(Span<const std::uint8_t> bytes) {
    std::ostringstream text;
    for (const std::uint8_t byte : bytes) {
        text << std::hex << std::setw(2) << std::setfill('0') << static_cast<unsigned>(byte);
    }
    return text.str();
}

/// @brief true when @p lhs and @p rhs hold the same bytes.
inline bool same_bytes(Span<const std::uint8_t> lhs, Span<const std::uint8_t> rhs) noexcept {
    if (lhs.size() != rhs.size()) {
        return false;
    }
    for (std::size_t index = 0U; index < lhs.size(); ++index) {
        if (lhs[index] != rhs[index]) {
            return false;
        }
    }
    return true;
}

/// @brief Buffer that holds any encoding of @p T.
template <typename T>
using WireBuffer = std::array<std::uint8_t, max_wire_size(type_tag<T>) + 1U>;

/// @brief Frames and errors seen while a frame arrives one byte at a time.
struct BytewiseResult {
    std::size_t frames_early = 0U; ///< Frames dispatched before the last byte arrived.
    std::size_t frames = 0U;       ///< Frames dispatched in total.
    std::size_t errors = 0U;       ///< Errors in total.
};

/// @brief Sends @p frame through @p link one byte at a time and polls after every byte.
template <typename L> BytewiseResult deliver_bytewise(L& link, Span<const std::uint8_t> frame) {
    BytewiseResult result;
    for (std::size_t index = 0U; index < frame.size(); ++index) {
        static_cast<void>(link.send_frame(frame.subspan(index, 1U)));
        const PollResult polled = link.poll();
        if (index + 1U < frame.size()) {
            result.frames_early += polled.frames;
        }
        result.frames += polled.frames;
        result.errors += polled.errors;
    }
    return result;
}

} // namespace wireforge::testing

#endif // WIREFORGE_GENERATED_WIRE_SUPPORT_HPP
"""

SUPPORT_HEADER = """\
/**
 * @file test_support.hpp
 * @brief Assertion helpers of the generated tests.
 *
 * Generated by WireForge. Do not edit; regenerate instead.
 */
#ifndef WIREFORGE_GENERATED_TEST_SUPPORT_HPP
#define WIREFORGE_GENERATED_TEST_SUPPORT_HPP

#include "wire_support.hpp"

#include <cstddef>
#include <cstdint>
#include <gtest/gtest.h>
#include <wireforge/codec_support.hpp>
#include <wireforge/span.hpp>
#include <wireforge/status.hpp>

namespace wireforge::testing {

/// @brief Checks that @p value serializes to exactly @p expected.
template <typename T>
::testing::AssertionResult encodes_to(const T& value, Span<const std::uint8_t> expected,
                                      const Encoding& encoding) {
    WireBuffer<T> buffer{};
    std::size_t size = 0U;
    const Status status = serialize(value, Span<std::uint8_t>(buffer), size, encoding);
    const Span<const std::uint8_t> encoded(buffer.data(), size);
    if (status != Status::Ok || !same_bytes(encoded, expected)) {
        return ::testing::AssertionFailure() << "serialize: " << to_string(status) << ", got "
                                             << hex(encoded) << ", expected " << hex(expected);
    }
    return ::testing::AssertionSuccess();
}

/// @brief Checks that serializing @p value fails with @p expected and reports size 0.
template <typename T>
::testing::AssertionResult encode_fails(const T& value, Status expected, const Encoding& encoding) {
    WireBuffer<T> buffer{};
    std::size_t size = 1U;
    const Status status = serialize(value, Span<std::uint8_t>(buffer), size, encoding);
    if (status != expected || size != 0U) {
        return ::testing::AssertionFailure() << "serialize: " << to_string(status) << " (size "
                                             << size << "), expected " << to_string(expected);
    }
    return ::testing::AssertionSuccess();
}

/// @brief Status of deserializing @p payload as a @p T.
template <typename T>
Status decode_status(Span<const std::uint8_t> payload, const Encoding& encoding) {
    T value{};
    return deserialize(payload, value, encoding);
}

/**
 * @brief Checks that @p expected encodes to @p payload and that @p payload decodes to a value
 *        that encodes to @p payload again.
 */
template <typename T>
::testing::AssertionResult golden_round_trip(const T& expected, Span<const std::uint8_t> payload,
                                             const Encoding& encoding) {
    const ::testing::AssertionResult encoded = encodes_to(expected, payload, encoding);
    if (!encoded) {
        return encoded;
    }
    T decoded{};
    const Status status = deserialize(payload, decoded, encoding);
    if (status != Status::Ok) {
        return ::testing::AssertionFailure() << "deserialize: " << to_string(status);
    }
    return encodes_to(decoded, payload, encoding) << " (re-encoding the decoded value)";
}

} // namespace wireforge::testing

#endif // WIREFORGE_GENERATED_TEST_SUPPORT_HPP
"""


# --- handlers.hpp ------------------------------------------------------------------------------


def handlers_header(project: Project) -> str:
    """Return handlers.hpp: a recording Handler and a loopback link per interface."""
    out = Writer()
    name = project.name
    out.line("/**")
    out.line(" * @file handlers.hpp")
    out.line(f" * @brief Recording handlers of the {name} interfaces for the generated tests.")
    out.line(" *")
    out.line(" * Generated by WireForge. Do not edit; regenerate instead.")
    out.line(" */")
    out.line("#ifndef WIREFORGE_GENERATED_TEST_HANDLERS_HPP")
    out.line("#define WIREFORGE_GENERATED_TEST_HANDLERS_HPP")
    out.line()
    out.line(f'#include "{name}/model.hpp"')
    for include in _interface_includes(project):
        out.line(f'#include "{include}"')
    out.line()
    out.line("#include <algorithm>")
    out.line("#include <array>")
    out.line("#include <cstddef>")
    out.line("#include <cstdint>")
    out.line("#include <wireforge/transport.hpp>")
    out.line()
    out.line("/// @brief Test doubles of the generated tests.")
    out.line("namespace generated_tests {")
    out.line()
    for interface in (project.interfaces[key] for key in sorted(project.interfaces)):
        _recorder(out, project, interface)
        _raw_recorder(out, project, interface)
    out.line("} // namespace generated_tests")
    out.line()
    out.line("#endif // WIREFORGE_GENERATED_TEST_HANDLERS_HPP")
    return out.text()


RECORDER_TAIL = """\
/// @brief The re-encoded payload of the last message received.
[[nodiscard]] wireforge::Span<const std::uint8_t> last() const noexcept {
    return wireforge::Span<const std::uint8_t>(bytes_.data(), size_);
}
/// @brief Number of messages received.
[[nodiscard]] std::size_t count() const noexcept { return count_; }
/// @brief Number of messages received at index @p index of the message list.
[[nodiscard]] std::size_t count(std::size_t index) const { return counts_.at(index); }

private:
template <std::size_t Index, typename T>
void record(const T& message, const wireforge::Encoding& encoding) noexcept {
    ++count_;
    ++std::get<Index>(counts_);
    static_cast<void>(serialize(message, wireforge::Span<std::uint8_t>(bytes_), size_, encoding));
}
std::array<std::uint8_t, @NS@::kMaxFrameSize> bytes_{};
std::size_t size_ = 0U;
std::size_t count_ = 0U;
std::array<std::size_t, @COUNT@U> counts_{};
};

/// @brief Recorder, endpoint and loopback link of interface @INTERFACE@.
/// @tparam N Capacity of the loopback transport in bytes.
template <std::size_t N> struct Loop_@ID@ {
Recorder_@ID@ recorder;                   ///< Receives the messages.
@NS@::Endpoint endpoint{recorder};         ///< Decoder and dispatcher.
wireforge::LoopbackTransport<N> transport; ///< Bytes sent are received again.
@NS@::Link<wireforge::LoopbackTransport<N>> link{
    transport, endpoint.decoder(), endpoint.dispatcher()}; ///< The link under test.
};
"""


def _recorder(out: Writer, project: Project, interface: Interface) -> None:
    """Write a handler that counts and re-encodes every received message, and a loopback link."""
    model = f"::{project.namespace or project.name}"
    ns = f"{model}::{interface.name}"
    identifier = _identifier(interface.name)
    names = Names(project)
    out.line(f"/// @brief Records the messages received on interface {interface.name}.")
    out.line(f"class Recorder_{identifier} final : public {ns}::Handler {{")
    out.line("public:")
    for index, entry in enumerate(interface.messages):
        if entry.condition is not None:
            out.line(f"#if {names.condition(entry.condition)}")
        out.line("/// @brief Counts and re-encodes the message.")
        out.line(
            f"void on{entry.name}Received(const {model}::{entry.name}& message) noexcept "
            "override {"
        )
        out.line(f"    record<{index}U>(message, {ns}::k{entry.name}Encoding);")
        out.line("}")
        if entry.condition is not None:
            out.line("#endif")
    tail = RECORDER_TAIL
    for key, value in (
        ("NS", ns),
        ("COUNT", str(len(interface.messages))),
        ("INTERFACE", interface.name),
        ("ID", identifier),
    ):
        tail = tail.replace(f"@{key}@", value)
    for text in tail.splitlines():
        out.line(text)
    out.line()


RAW_RECORDER_TAIL = """\
/// @brief Payload of the last raw message.
[[nodiscard]] wireforge::Span<const std::uint8_t> payload() const noexcept {
    return wireforge::Span<const std::uint8_t>(payload_.data(), payload_size_);
}
/// @brief Frame of the last raw message.
[[nodiscard]] wireforge::Span<const std::uint8_t> frame() const noexcept {
    return wireforge::Span<const std::uint8_t>(frame_.data(), frame_size_);
}
/// @brief Number of raw messages received.
[[nodiscard]] std::size_t raw_count() const noexcept { return raw_count_; }
/// @brief Number of messages the default implementation decoded.
[[nodiscard]] std::size_t decoded_count() const noexcept { return decoded_count_; }

private:
using Bytes = std::array<std::uint8_t, @NS@::kMaxFrameSize>;
static std::size_t copy(wireforge::Span<const std::uint8_t> bytes, Bytes& target) noexcept {
    const std::size_t size = std::min(bytes.size(), target.size());
    std::copy_n(bytes.begin(), size, target.begin());
    return size;
}
void keep(const wireforge::RawMessage& raw) noexcept {
    ++raw_count_;
    payload_size_ = copy(raw.payload, payload_);
    frame_size_ = copy(raw.frame, frame_);
}
bool decode_;
Bytes payload_{};
Bytes frame_{};
std::size_t payload_size_ = 0U;
std::size_t frame_size_ = 0U;
std::size_t raw_count_ = 0U;
std::size_t decoded_count_ = 0U;
};

/// @brief Raw recorder, endpoint and loopback link of interface @INTERFACE@.
/// @tparam N Capacity of the loopback transport in bytes.
/// @tparam Decode Whether the default implementation decodes the messages too.
template <std::size_t N, bool Decode> struct RawLoop_@ID@ {
RawRecorder_@ID@ recorder{Decode};         ///< Keeps the raw messages.
@NS@::Endpoint endpoint{recorder};         ///< Decoder and dispatcher.
wireforge::LoopbackTransport<N> transport; ///< Bytes sent are received again.
@NS@::Link<wireforge::LoopbackTransport<N>> link{
    transport, endpoint.decoder(), endpoint.dispatcher()}; ///< The link under test.
};
"""


def _raw_recorder(out: Writer, project: Project, interface: Interface) -> None:
    """Write a handler that keeps the raw bytes of every message, and its loopback link."""
    model = f"::{project.namespace or project.name}"
    ns = f"{model}::{interface.name}"
    identifier = _identifier(interface.name)
    names = Names(project)
    out.line(
        f"/// @brief Keeps the raw bytes of the messages received on interface {interface.name}."
    )
    out.line(f"class RawRecorder_{identifier} final : public {ns}::Handler {{")
    out.line("public:")
    out.line("/// @brief With @p decode the default implementation decodes the messages as well.")
    out.line(f"explicit RawRecorder_{identifier}(bool decode) noexcept : decode_(decode) {{}}")
    for entry in interface.messages:
        if entry.condition is not None:
            out.line(f"#if {names.condition(entry.condition)}")
        out.line("/// @brief Keeps the bytes; decodes them as well when asked to.")
        out.line(
            f"wireforge::Status on{entry.name}Raw(const wireforge::RawMessage& raw) noexcept "
            "override {"
        )
        out.line("    keep(raw);")
        out.line(
            f"    return decode_ ? {ns}::Handler::on{entry.name}Raw(raw) : wireforge::Status::Ok;"
        )
        out.line("}")
        out.line("/// @brief Counts the decoded message.")
        out.line(
            f"void on{entry.name}Received(const {model}::{entry.name}& message) noexcept "
            "override {"
        )
        out.line("    static_cast<void>(message);")
        out.line("    ++decoded_count_;")
        out.line("}")
        if entry.condition is not None:
            out.line("#endif")
    tail = RAW_RECORDER_TAIL
    for key, value in (("NS", ns), ("INTERFACE", interface.name), ("ID", identifier)):
        tail = tail.replace(f"@{key}@", value)
    for text in tail.splitlines():
        out.line(text)
    out.line()


# --- golden_test.cpp ---------------------------------------------------------------------------


def golden_test_source(project: Project, vectors: list[dict[str, Any]]) -> str:
    """Every golden vector must round-trip and loop back as a frame."""
    out = Writer()
    assignments = _Assignments(project)
    verifier = Verifier(project)
    _header(out, project, [*_interface_includes(project), "handlers.hpp", "test_support.hpp"])
    for vector in vectors:
        _open_profile(out, vector["profile"])
        _golden_test(out, assignments, verifier, vector)
        _close_profile(out, vector["profile"])
    out.line("} // namespace")
    return out.text()


def _golden_test(
    out: Writer, assignments: _Assignments, verifier: Verifier, vector: dict[str, Any]
) -> None:
    project = assignments.project
    message = project.messages[vector["message"]]
    interface = project.interfaces[vector["interface"]]
    payload = bytes.fromhex(vector["payload"])
    frame = bytes.fromhex(vector["frame"])
    name = _identifier(f"{vector['interface']}_{vector['message']}_{vector['sample']}")
    out.line(f"TEST(Golden, {name}) {{")
    out.indent()
    _record(out, verifier.shown(verifier.golden(vector)))
    _byte_array(out, "kPayload", payload)
    _byte_array(out, "kFrame", frame)
    # A user-type field is opaque: the value built from the golden pattern leaves it
    # default-constructed, so its encoded bytes cannot be compared against the reference codec's
    # bytes for that field.
    opaque = _has_user_type(project, message.fields)
    if not opaque:
        statements = assignments.struct(message, vector["value"], "expected")
        out.line(f"{'' if statements else 'const '}model::{message.name} expected{{}};")
        for statement in statements:
            out.line(statement)
    encoding = encoding_literal(project, message, interface)
    mutable = header_is_written(interface.header or ())
    ns = f"model::{interface.name}"
    opaque_lines = (
        [
            (
                f"EXPECT_EQ({SUPPORT}decode_status<model::{message.name}>({CSPAN}(kPayload), "
                f"{encoding}), {STATUS}Ok);"
            ),
            # The opaque field's own bytes can't be predicted, but a default-constructed value
            # must still encode successfully: exercises the encode_frame() success path.
            f"std::array<std::uint8_t, {ns}::kMaxFrameSize> default_buffer{{}};",
            "std::size_t default_size = 0U;",
            (
                f"EXPECT_EQ({ns}::encode_frame(model::{message.name}{{}}, "
                f"{SPAN}(default_buffer), default_size), {STATUS}Ok);"
            ),
        ]
        if opaque
        else None
    )
    for text in _golden_checks(
        interface.name,
        encoding,
        len(frame) - len(payload),
        mutable=mutable,
        opaque_lines=opaque_lines,
    ):
        out.line(text)
    out.dedent()
    out.line("}")
    out.line()


def _golden_checks(
    interface: str, encoding: str, overhead: int, *, mutable: bool, opaque_lines: list[str] | None
) -> list[str]:
    ns = f"model::{interface}"
    if opaque_lines is not None:
        lines = [
            *opaque_lines,
            f"const {CSPAN} golden_frame(kFrame);",
            f"{'' if mutable else 'const '}{ns}::Header header{{}};",
            f"EXPECT_EQ({ns}::decode_header(golden_frame, header), {STATUS}Ok);",
        ]
    else:
        lines = [
            f"EXPECT_TRUE({SUPPORT}golden_round_trip(",
            f"    expected, {CSPAN}(kPayload), {encoding}));",
            f"const {CSPAN} golden_frame(kFrame);",
            f"std::array<std::uint8_t, {ns}::kMaxFrameSize> buffer{{}};",
            "std::size_t size = 0U;",
            f"ASSERT_EQ({ns}::encode_frame(expected, {SPAN}(buffer), size), {STATUS}Ok);",
            f"EXPECT_TRUE({SUPPORT}same_bytes({CSPAN}(buffer.data(), size), golden_frame));",
            f"{'' if mutable else 'const '}{ns}::Header header{{}};",
            f"EXPECT_EQ({ns}::decode_header(golden_frame, header), {STATUS}Ok);",
        ]
    if overhead > 0:
        lines.append(
            f"EXPECT_EQ({ns}::decode_header(golden_frame.first({overhead - 1}U), header), "
            f"{STATUS}Truncated);"
        )
    lines += [
        f"generated_tests::Loop_{_identifier(interface)}<{ns}::kMaxFrameSize> loop;",
        f"ASSERT_EQ(loop.endpoint.status(), {STATUS}Ok);",
        f"ASSERT_EQ(loop.link.send_frame(golden_frame), {STATUS}Ok);",
        "EXPECT_EQ(loop.link.poll().frames, 1U);",
        "EXPECT_EQ(loop.recorder.count(), 1U);",
        f"EXPECT_TRUE({SUPPORT}same_bytes(loop.recorder.last(), {CSPAN}(kPayload)));",
    ]
    return lines


# --- message_test.cpp --------------------------------------------------------------------------


def _layouts(report: LayoutReport | None) -> dict[str | None, ProfileLayout]:
    return {} if report is None else {profile.profile: profile for profile in report.profiles}


def _message_wire_size(out: Writer, message: MessageLayout, requirements: tuple[str, ...]) -> None:
    tag = f"wireforge::type_tag<model::{message.name}>"
    lines = [
        (
            f"static_assert(min_wire_size({tag}) == {message.size_min}U, "
            '"minimum size of the layout analyzer");'
        ),
        (
            f"static_assert(max_wire_size({tag}) == {message.size_max}U, "
            '"maximum size of the layout analyzer");'
        ),
    ]
    _test(out, message.name, "WireSize", lines, requirements)


def _interface_wire_size(
    out: Writer, interface: InterfaceLayout, requirements: tuple[str, ...]
) -> None:
    ns = f"model::{interface.name}"
    checks = (
        ("kHeaderSize", interface.header_size),
        ("kTrailerSize", interface.trailer_size),
        ("max_payload_size()", interface.max_payload),
        ("kMaxFrameSize", interface.max_frame),
        ("kRxBufferBytes", interface.rx_buffer),
        ("kTxBufferBytes", interface.tx_buffer),
    )
    lines = [
        f'static_assert({ns}::{name} == {value}U, "{name} of the layout analyzer");'
        for name, value in checks
    ]
    _test(out, f"Interface_{_identifier(interface.name)}", "WireSize", lines, requirements)


class _MessageTests:
    """Writes message_test.cpp."""

    def __init__(self, project: Project, layout: LayoutReport | None) -> None:
        self.project = project
        self.assignments = _Assignments(project)
        self.out = Writer()
        self.layouts = _layouts(layout)
        self.sizes: dict[str, MessageLayout] = {}
        self.verifier = Verifier(project)

    def render(self, profiles: tuple[ProfileCases, ...]) -> str:
        out = self.out
        _header(out, self.project, ["test_support.hpp"])
        out.line("using wireforge::Status;")
        out.line()
        for profile in profiles:
            _open_profile(out, profile.profile)
            layout = self.layouts.get(profile.profile)
            self.sizes = {} if layout is None else {item.name: item for item in layout.messages}
            self.profile(profile)
            _close_profile(out, profile.profile)
        out.line("} // namespace")
        return out.text()

    def profile(self, cases: ProfileCases) -> None:
        for message in cases.messages:
            self.message(message)

    def outcome(self, subject: str, outcome: Outcome, encoding: str, *, opaque: bool) -> str | None:
        if isinstance(outcome, bytes):
            if opaque:
                return None
            return f"EXPECT_TRUE({SUPPORT}encodes_to({subject}, {CSPAN}(kExpected), {encoding}));"
        return f"EXPECT_TRUE({SUPPORT}encode_fails({subject}, Status::{outcome}, {encoding}));"

    def expected(self, outcome: Outcome, *, opaque: bool) -> None:
        if isinstance(outcome, bytes) and not opaque:
            _byte_array(self.out, "kExpected", outcome)

    def message(self, cases: MessageCases) -> None:
        out = self.out
        name = cases.message.name
        encoding = f"model::k{name}Encoding"
        # A user-type field is opaque: the value built from the golden pattern leaves it
        # default-constructed, so its encoded bytes cannot be compared against the reference
        # codec's bytes for that field.
        opaque = _has_user_type(self.project, cases.message.fields)
        round_trip = isinstance(cases.pattern_encoded, bytes) and not opaque
        if cases.encodes or round_trip:  # the edits and the round trip use the pattern value
            statements = self.assignments.struct(cases.message, cases.pattern, "value")
            out.line(f"/// Pattern value of {name}: every field inside its range, arrays full.")
            out.line(f"model::{name} pattern_{name}() {{")
            out.indent()
            out.line(f"model::{name} value{{}};")
            for statement in statements:
                out.line(statement)
            out.line("return value;")
            out.dedent()
            out.line("}")
            out.line()
        out.line(f"TEST({name}, Defaults) {{")
        out.indent()
        _record(out, self.verifier.shown(self.verifier.defaults(cases.message)))
        self.expected(cases.default_encoded, opaque=opaque)
        out.line(f"const model::{name} value{{}};")
        out.line(f"EXPECT_EQ(validate(value), Status::{cases.default_validated});")
        line = self.outcome("value", cases.default_encoded, encoding, opaque=opaque)
        if line is not None:
            out.line(line)
        out.dedent()
        out.line("}")
        out.line()
        used: set[str] = {"Defaults", "WireSize", "SerializeNeedsRoom", "PatternRoundTrip"}
        if isinstance(cases.default_encoded, bytes) and cases.default_encoded:
            self.needs_room(cases.message, encoding)
        if isinstance(cases.pattern_encoded, bytes) and not opaque:
            self.pattern_round_trip(cases, encoding)
        if name in self.sizes:
            _message_wire_size(
                out, self.sizes[name], self.verifier.shown(self.verifier.wire_size(cases.message))
            )
        for case in cases.encodes:
            self.encode_case(cases.message, case, encoding, used, opaque=opaque)
        for decode in cases.decodes:
            lines = Writer(depth=0)
            _byte_array(lines, "kPayload", decode.payload)
            lines.line(
                f"EXPECT_EQ({SUPPORT}decode_status<model::{name}>({CSPAN}(kPayload), "
                f"{encoding}), Status::{decode.status});"
            )
            _test(
                out,
                name,
                _unique_name(f"Decode_{decode.name}", used),
                lines.lines,
                self.verifier.shown(self.verifier.decode_case(cases.message, decode)),
            )

    def pattern_round_trip(self, cases: MessageCases, encoding: str) -> None:
        """Check that the pattern value encodes to its bytes and decodes back, for every message."""
        name = cases.message.name
        payload = cases.pattern_encoded
        if not isinstance(payload, bytes):
            return
        lines = Writer(depth=0)
        _byte_array(lines, "kPayload", payload)
        check = f"golden_round_trip(pattern_{name}(), {CSPAN}(kPayload), {encoding})"
        lines.line(f"EXPECT_TRUE({SUPPORT}{check});")
        keys = self.verifier.shown(self.verifier.keep(f"{name}.LAYOUT"))
        _test(self.out, name, "PatternRoundTrip", lines.lines, keys)

    def needs_room(self, message: Struct, encoding: str) -> None:
        """Check that a buffer without room is refused and the size reset, for every message."""
        name = message.name
        lines = [
            "std::array<std::uint8_t, 1U> buffer{};",
            "std::size_t size = 1U;",
            f"EXPECT_EQ(serialize(model::{name}{{}}, {SPAN}(buffer.data(), 0U), size, {encoding}),",
            f"          {STATUS}BufferTooSmall);",
            "EXPECT_EQ(size, 0U);",
        ]
        keys = self.verifier.shown(self.verifier.keep(f"{name}.LAYOUT"))
        _test(self.out, name, "SerializeNeedsRoom", lines, keys)

    def encode_case(
        self, message: Struct, case: EncodeCase, encoding: str, used: set[str], *, opaque: bool
    ) -> None:
        out = self.out
        name = message.name
        out.line(f"TEST({name}, {_unique_name(_identifier(case.name), used)}) {{")
        out.indent()
        _record(out, self.verifier.shown(self.verifier.encode_case(message, case)))
        self.expected(case.encoded, opaque=opaque)
        out.line(f"model::{name} value = pattern_{name}();")
        out.line(self.assignments.edit(case.edit))
        out.line(f"EXPECT_EQ(validate(value), Status::{case.validated});")
        line = self.outcome("value", case.encoded, encoding, opaque=opaque)
        if line is not None:
            out.line(line)
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


def message_test_source(
    project: Project, profiles: tuple[ProfileCases, ...], layout: LayoutReport | None = None
) -> str:
    """Defaults, boundaries, element counts and decode errors of every message.

    With `layout`, the wire sizes of the layout analyzer are checked with static_asserts.
    """
    return _MessageTests(project, layout).render(profiles)


# --- interface_test.cpp ------------------------------------------------------------------------


class _InterfaceTests:
    """Writes interface_test.cpp."""

    def __init__(self, project: Project, layout: LayoutReport | None) -> None:
        self.project = project
        self.out = Writer()
        self.layouts = _layouts(layout)
        self.verifier = Verifier(project)
        self.names = Names(project)

    def render(self, profiles: tuple[ProfileCases, ...]) -> str:
        out = self.out
        includes = [*_interface_includes(self.project), "handlers.hpp", "test_support.hpp"]
        _header(out, self.project, includes)
        for profile in profiles:
            _open_profile(out, profile.profile)
            layout = self.layouts.get(profile.profile)
            if layout is not None:
                for sized in layout.interfaces:
                    requirements = self.verifier.shown(
                        self.verifier.interface_test(
                            self.project.interfaces[sized.name], "WireSize"
                        )
                    )
                    _interface_wire_size(out, sized, requirements)
            self.profile(profile)
            _close_profile(out, profile.profile)
        out.line("} // namespace")
        return out.text()

    def profile(self, cases: ProfileCases) -> None:
        messages = {item.message.name: item for item in cases.messages}
        for interface in cases.interfaces:
            self.interface(interface, messages)

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
        lines = Writer()
        self.frames(lines, cases)
        lines.line(self.loop(cases, sum(len(frame) for _, frame in cases.frames)))
        lines.line(f"ASSERT_EQ(loop.endpoint.status(), {STATUS}Ok);")
        for position in range(len(cases.frames)):
            frame = f"{CSPAN}(kFrame{position})"
            lines.line(f"ASSERT_EQ(loop.link.send_frame({frame}), {STATUS}Ok);")
            if not framed:
                lines.line("EXPECT_EQ(loop.link.poll().frames, 1U);")
        if framed:
            lines.line("const wireforge::PollResult polled = loop.link.poll();")
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
        lines = Writer()
        frame = cases.frames[0][1]
        _byte_array(lines, "kFrame", frame)
        lines.line(self.loop(cases, len(frame)))
        lines.line(
            f"const {SUPPORT}BytewiseResult result = "
            f"{SUPPORT}deliver_bytewise(loop.link, {CSPAN}(kFrame));"
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
        lines = Writer()
        frame = cases.frames[0][1]
        junk = bytes([cases.sync[0] ^ 0xFF]) * 3
        _byte_array(lines, "kGarbage", junk)
        _byte_array(lines, "kFrame", frame)
        lines.line(self.loop(cases, len(junk) + len(frame)))
        lines.line(f"ASSERT_EQ(loop.link.send_frame({CSPAN}(kGarbage)), {STATUS}Ok);")
        lines.line(f"ASSERT_EQ(loop.link.send_frame({CSPAN}(kFrame)), {STATUS}Ok);")
        lines.line("const wireforge::PollResult polled = loop.link.poll();")
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
        lines = Writer()
        frame = cases.frames[0][1]
        _byte_array(lines, "kCorrupt", cases.corrupt)
        if cases.exact:
            _byte_array(lines, "kFrame", frame)
        lines.line(self.loop(cases, len(frame)))
        lines.line(f"ASSERT_EQ(loop.link.send_frame({CSPAN}(kCorrupt)), {STATUS}Ok);")
        lines.line("const wireforge::PollResult rejected = loop.link.poll();")
        if cases.corrupt_status == OK:
            lines.line("EXPECT_EQ(rejected.frames, 1U);")
            lines.line("EXPECT_EQ(rejected.errors, 0U);")
        else:
            lines.line("EXPECT_EQ(rejected.frames, 0U);")
            lines.line("EXPECT_EQ(loop.recorder.count(), 0U);")
            if cases.exact:
                lines.line("EXPECT_EQ(rejected.errors, 1U);")
                lines.line(f"EXPECT_EQ(rejected.last_error, {STATUS}{cases.corrupt_status});")
            else:
                lines.line("EXPECT_GE(rejected.errors, 1U);")
        if cases.exact:
            lines.line(f"ASSERT_EQ(loop.link.send_frame({CSPAN}(kFrame)), {STATUS}Ok);")
            lines.line("EXPECT_EQ(loop.link.poll().frames, 1U);")
        status = cases.corrupt_status
        requirements = self.requirements(cases, "RejectsCorruptedFrame", corrupt_status=status)
        _test(self.out, suite, "RejectsCorruptedFrame", lines.lines, requirements)

    def raw(self, cases: InterfaceCases, suite: str) -> None:
        ns = f"model::{cases.interface.name}"
        lines = Writer()
        self.frames(lines, cases)
        capacity = max(len(frame) for _, frame in cases.frames)
        lines.line(self.raw_loop(cases, capacity, decode=False))
        lines.line(f"ASSERT_EQ(loop.endpoint.status(), {STATUS}Ok);")
        for position in range(len(cases.frames)):
            frame = f"{CSPAN}(kFrame{position})"
            payload = (
                f"{frame}.subspan({ns}::kHeaderSize, kFrame{position}.size() - "
                f"{ns}::kHeaderSize - {ns}::kTrailerSize)"
            )
            lines.line(f"ASSERT_EQ(loop.link.send_frame({frame}), {STATUS}Ok);")
            lines.line("EXPECT_EQ(loop.link.poll().frames, 1U);")
            lines.line(f"EXPECT_TRUE({SUPPORT}same_bytes(loop.recorder.frame(), {frame}));")
            lines.line(f"EXPECT_TRUE({SUPPORT}same_bytes(loop.recorder.payload(), {payload}));")
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
        lines = Writer()
        frame = cases.frames[0][1]
        _byte_array(lines, "kFrame", frame)
        lines.line(self.raw_loop(cases, len(frame), decode=True))
        lines.line(f"ASSERT_EQ(loop.link.send_frame({CSPAN}(kFrame)), {STATUS}Ok);")
        lines.line("const wireforge::PollResult polled = loop.link.poll();")
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
        for index, failing in cases.raw_errors:
            entry = interface.messages[index]
            if entry.name not in messages:
                continue
            lines = Writer()
            _byte_array(lines, "kPayload", failing.payload)
            lines.line(f"generated_tests::RawRecorder_{identifier} recorder(true);")
            lines.line(f"const wireforge::RawMessage raw{{{CSPAN}(kPayload), {CSPAN}(kPayload)}};")
            lines.line(f"EXPECT_EQ(recorder.on{entry.name}Raw(raw), {STATUS}{failing.status});")
            lines.line("EXPECT_EQ(recorder.decoded_count(), 0U);")
            keys = self.verifier.decode_case(self.project.messages[entry.name], failing)
            name = f"RawDecodeErrorOf{entry.name}"
            _test(
                self.out, suite, name, lines.lines, self.requirements(cases, name, decode_keys=keys)
            )

    def broken_frames(self, cases: InterfaceCases, suite: str, *, framed: bool) -> None:
        """Check that a header constant or a checksum that the receiver checks is enforced.

        The frame of every message is changed in the byte of the constant, and (without a frame
        decoder, which would reject it first) in its last byte, and given to the dispatcher.
        """
        interface = cases.interface
        offset = 0
        position: int | None = None
        for item in interface.header or ():
            if is_checked_constant(item, framed=framed):
                position = offset
                break
            offset += _scalar_bytes(self.names, item)
        checksum = not framed and any(item.checksum for item in interface.trailer)
        if (position is None and not checksum) or not cases.frames:
            return
        lines = Writer()
        lines.line(f"generated_tests::Recorder_{_identifier(interface.name)} recorder;")
        lines.line(f"const model::{interface.name}::Endpoint endpoint{{recorder}};")
        lines.line(f"ASSERT_EQ(endpoint.status(), {STATUS}Ok);")
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
                    f"EXPECT_EQ(endpoint.dispatcher().dispatch_frame("
                    f"{CSPAN}(broken{status}.data(), broken{status}.size())), {STATUS}{status});"
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
        ns = f"model::{interface.name}"
        # Every message has its own instance of the frame writer, so every message is tried.
        lines = [
            f"std::array<std::uint8_t, {max(item[2] for item in sized)}U> buffer{{}};",
            "std::size_t size = 1U;",
        ]
        for index, frame_size, overhead in sized:
            message = interface.messages[index].name
            rooms = [0]
            if 0 < overhead < frame_size:
                rooms.append(overhead)  # room for header and trailer, none for the payload
            for room in rooms:
                call = (
                    f"{ns}::encode_frame(model::{message}{{}}, "
                    f"{SPAN}(buffer.data(), {room}U), size)"
                )
                lines += [
                    f"EXPECT_EQ({call},",
                    f"          {STATUS}BufferTooSmall);",
                    "EXPECT_EQ(size, 0U);",
                ]
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
    """Dispatch, framing and error handling of every interface.

    With `layout`, frame and buffer sizes of the layout analyzer are checked with static_asserts.
    """
    return _InterfaceTests(project, layout).render(profiles)


# --- hlr_vector_test.cpp -----------------------------------------------------------------------


class _VectorCode:
    """C++ statements that check one requirement vector, for GoogleTest or the self-test.

    The expectations are the values written in the model; the reference codec is not asked.
    """

    def __init__(self, project: Project, label: str, *, selftest: bool) -> None:
        self.project = project
        self.label = label
        self.selftest = selftest
        self.assignments = _Assignments(project)

    # --- statement forms ------------------------------------------------------------------------

    def equal(self, actual: str, expected: str, *, fatal: bool = False) -> str:
        if self.selftest:
            return f'checks.expect({self.same(actual, expected)}, "{self.label}", "{actual}");'
        return f"{'ASSERT' if fatal else 'EXPECT'}_EQ({actual}, {expected});"

    @staticmethod
    def same(actual: str, expected: str) -> str:
        """Compare in the self-test: no `==` on floats (-Wfloat-equal), no `== true`."""
        if expected == "true":
            return actual
        if expected == "false":
            return f"!{actual}"
        if FLOAT_LITERAL.match(expected):
            return f"(!({actual} < {expected}) && !({expected} < {actual}))"
        return f"{actual} == {expected}"

    def truth(self, condition: str, what: str) -> str:
        if self.selftest:
            return f'checks.expect({condition}, "{self.label}", "{what}");'
        return f"EXPECT_TRUE({condition});"

    def spans(self, name: str) -> str:
        return f"Bytes({name})" if self.selftest else f"{CSPAN}({name})"

    def out_span(self, name: str) -> str:
        return f"Span<std::uint8_t>({name})" if self.selftest else f"{SPAN}({name})"

    def status(self, value: str) -> str:
        return f"Status::{value}" if self.selftest else f"{STATUS}{value}"

    # --- one vector -----------------------------------------------------------------------------

    def lines(self, vector: Vector) -> list[str]:
        project = self.project
        message = project.messages[vector.message]
        interface = project.interfaces[vector.interface] if vector.interface else None
        encoding = (
            encoding_literal(project, message, interface)
            if interface is not None
            else f"model::k{message.name}Encoding"
        )
        out = Writer(depth=0)
        _byte_array(out, "kBytes", vector.data or b"")
        if vector.direction in {"decode", "both"}:
            for text in self.decode(vector, message, interface, encoding):
                out.line(text)
        if vector.direction in {"encode", "both"} and not _has_user_type(project, message.fields):
            for text in self.encode(vector, message, interface, encoding):
                out.line(text)
        return out.lines

    def decode(
        self, vector: Vector, message: Struct, interface: Interface | None, encoding: str
    ) -> list[str]:
        if interface is None:
            return self.decode_payload(vector, message, encoding)
        name = _identifier(interface.name)
        size = max(len(vector.data or b""), 1)
        lines = [
            f"generated_tests::Loop_{name}<{size}U> loop;",
            self.equal("loop.endpoint.status()", self.status("Ok"), fatal=True),
            self.equal(
                f"loop.link.send_frame({self.spans('kBytes')})", self.status("Ok"), fatal=True
            ),
            "const wireforge::PollResult polled = loop.link.poll();",
        ]
        if vector.status is not None:
            lines += [
                self.equal("loop.recorder.count()", "0U"),
                self.equal("polled.frames", "0U"),
                self.truth("polled.errors >= 1U", "errors"),
            ]
            if not interface.framing.resync or interface.framing.kind == "none":
                lines.append(self.equal("polled.last_error", self.status(vector.status)))
            return lines
        lines += [
            self.equal("polled.frames", "1U"),
            self.equal("polled.errors", "0U"),
            self.equal("loop.recorder.count()", "1U", fatal=True),
        ]
        return lines + self.fields(vector, message, "loop.recorder.last()", encoding)

    def decode_payload(self, vector: Vector, message: Struct, encoding: str) -> list[str]:
        if vector.status is None:
            return self.fields(vector, message, self.spans("kBytes"), encoding)
        if self.selftest:
            check = (
                f'decode_status<model::{message.name}>(checks, "{self.label}", Bytes(kBytes), '
                f"Status::{vector.status}, {encoding});"
            )
            return [check]
        call = f"{SUPPORT}decode_status<model::{message.name}>({CSPAN}(kBytes), {encoding})"
        return [f"EXPECT_EQ({call}, {STATUS}{vector.status});"]

    def fields(self, vector: Vector, message: Struct, payload: str, encoding: str) -> list[str]:
        call = f"deserialize({payload}, decoded, {encoding})"
        lines = [
            f"model::{message.name} decoded{{}};",
            self.equal(call, self.status("Ok"), fatal=True),
        ]
        return lines + self.expectations(message, vector.fields or {}, "decoded")

    def expectations(self, struct_: Struct, expected: dict[str, Any], target: str) -> list[str]:
        lines: list[str] = []
        for field in struct_.fields:
            if field.name not in expected or field.type in self.project.user_types:
                continue
            value = expected[field.name]
            here = f"{target}.{field.name}"
            inner = self.project.types.get(field.type)
            if field.occurs is None:
                if inner is not None and isinstance(value, dict):
                    lines += self.expectations(inner, value, here)
                else:
                    lines.append(self.equal(here, self.assignments.literal(field, value)))
            elif field.type == "char":
                lines.append(self.equal(f"{here}.view()", string_literal(str(value))))
            else:
                lines += self.elements(field, inner, value, here)
        return lines

    def elements(self, field: Field, inner: Struct | None, value: object, here: str) -> list[str]:
        items = value if isinstance(value, list) else []
        lines = [self.equal(f"{here}.size()", f"{len(items)}U", fatal=True)]
        for index, element in enumerate(items):
            slot = f"{here}[{index}U]"
            if inner is not None and isinstance(element, dict):
                lines += self.expectations(inner, element, slot)
            else:
                lines.append(self.equal(slot, self.assignments.literal(field, element)))
        return lines

    def encode(
        self, vector: Vector, message: Struct, interface: Interface | None, encoding: str
    ) -> list[str]:
        lines = [f"model::{message.name} value{{}};"]
        lines += self.assignments.struct(message, vector.fields or {}, "value")
        if interface is None:
            if self.selftest:
                tag = f"wireforge::type_tag<model::{message.name}>"
                lines += [
                    f"std::array<std::uint8_t, max_wire_size({tag}) + 1U> buffer{{}};",
                    "std::size_t size = 0U;",
                    self.equal(
                        f"serialize(value, Span<std::uint8_t>(buffer), size, {encoding})",
                        "Status::Ok",
                    ),
                    self.truth("same(Bytes(buffer.data(), size), Bytes(kBytes))", "payload"),
                ]
                return lines
            lines.append(f"EXPECT_TRUE({SUPPORT}encodes_to(value, {CSPAN}(kBytes), {encoding}));")
            return lines
        ns = f"model::{interface.name}"
        lines += [
            f"std::array<std::uint8_t, {ns}::kMaxFrameSize> buffer{{}};",
            "std::size_t size = 0U;",
            self.equal(
                f"{ns}::encode_frame(value, {self.out_span('buffer')}, size)",
                self.status("Ok"),
                fatal=True,
            ),
        ]
        same = (
            "same(Bytes(buffer.data(), size), Bytes(kBytes))"
            if self.selftest
            else f"{SUPPORT}same_bytes({CSPAN}(buffer.data(), size), {CSPAN}(kBytes))"
        )
        lines.append(self.truth(same, "frame"))
        return lines


class _HlrVectorTests:
    """Writes hlr_vector_test.cpp: the vectors of the model, checked against the user's values."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.out = Writer()

    def render(self, runs: list[VectorRun]) -> str:
        out = self.out
        includes = [*_interface_includes(self.project), "handlers.hpp", "test_support.hpp"]
        _header(out, self.project, includes)
        out.line("using wireforge::Status;")
        out.line()
        profiles: dict[str | None, list[VectorRun]] = {}
        for item in runs:
            profiles.setdefault(item.profile, []).append(item)
        for profile, items in profiles.items():
            _open_profile(out, profile)
            for item in items:
                self.test(item.vector)
            _close_profile(out, profile)
        out.line("} // namespace")
        return out.text()

    def test(self, vector: Vector) -> None:
        out = self.out
        out.line(f"TEST(HlrVector, {vector.name}) {{")
        out.indent()
        _record(out, vector.reqs)
        for text in _VectorCode(self.project, vector.name, selftest=False).lines(vector):
            out.line(text)
        out.dedent()
        out.line("}")
        out.line()


def hlr_vector_test_source(project: Project, runs: list[VectorRun]) -> str:
    """Test of every requirement vector against the values written in the model."""
    return _HlrVectorTests(project).render(runs)


# --- diff_harness.cpp and fuzz_target.cpp ------------------------------------------------------

HARNESS_HEAD = """\
using wireforge::Span;
using wireforge::Status;

/// Bytes of a hex string ("-" is empty); false for malformed text.
bool parse_hex(const std::string& text, std::vector<std::uint8_t>& bytes) {
    bytes.clear();
    if (text == "-") {
        return true;
    }
    if (text.size() % 2U != 0U) {
        return false;
    }
    for (std::size_t index = 0U; index < text.size(); index += 2U) {
        const std::string pair = text.substr(index, 2U);
        if (pair.find_first_not_of("0123456789abcdefABCDEF") != std::string::npos) {
            return false;
        }
        bytes.push_back(static_cast<std::uint8_t>(std::stoul(pair, nullptr, 16)));
    }
    return true;
}

std::string hex_or_dash(Span<const std::uint8_t> bytes) {
    return bytes.empty() ? std::string("-") : wireforge::testing::hex(bytes);
}

/// Decodes @p payload, encodes the result again and describes both steps.
template <typename T>
std::string message_answer(Span<const std::uint8_t> payload, const wireforge::Encoding& encoding) {
    T value{};
    const Status decoded = deserialize(payload, value, encoding);
    if (decoded != Status::Ok) {
        return std::string(to_string(decoded)) + " - -";
    }
    wireforge::testing::WireBuffer<T> buffer{};
    std::size_t size = 0U;
    const Status encoded = serialize(value, Span<std::uint8_t>(buffer), size, encoding);
    if (encoded != Status::Ok) {
        return std::string("Ok ") + to_string(encoded) + " -";
    }
    return "Ok Ok " + hex_or_dash(Span<const std::uint8_t>(buffer.data(), size));
}

/// Sends @p frame through a fresh loopback link and describes what was received.
template <typename Loop> std::string frame_answer(Loop& loop, Span<const std::uint8_t> frame) {
    static_cast<void>(loop.link.send_frame(frame));
    const wireforge::PollResult polled = loop.link.poll();
    const std::string last = loop.recorder.count() > 0U ? hex_or_dash(loop.recorder.last()) : "-";
    return std::to_string(polled.frames) + " " + std::to_string(polled.errors) + " " + last;
}
"""

HARNESS_MAIN = """\
} // namespace

int main() {
    std::string kind;
    std::string target;
    std::string text;
    std::vector<std::uint8_t> bytes;
    while (std::cin >> kind >> target >> text) {
        if (!parse_hex(text, bytes)) {
            std::cout << "malformed\\n";
            continue;
        }
        const Span<const std::uint8_t> data(bytes.data(), bytes.size());
        std::cout << (kind == "M" ? message(target, data) : frame(target, data)) << '\\n';
    }
    return 0;
}
"""


def diff_harness_source(project: Project) -> str:
    """Line-based harness used by `wireforge.differential` (one executable per profile)."""
    out = Writer()
    names = Names(project)
    _header(
        out,
        project,
        [*_interface_includes(project), "handlers.hpp", "wire_support.hpp"],
        ("array", "cstddef", "cstdint", "iostream", "string", "vector"),
    )
    for text in HARNESS_HEAD.splitlines():
        out.line(text)
    out.line()
    out.line("std::string message(const std::string& name, Span<const std::uint8_t> payload) {")
    out.indent()
    for message in project.messages.values():
        if message.condition is not None:
            out.line(f"#if {names.condition(message.condition)}")
        out.line(f'if (name == "{message.name}") {{')
        out.line(
            f"    return message_answer<model::{message.name}>(payload, "
            f"model::k{message.name}Encoding);"
        )
        out.line("}")
        if message.condition is not None:
            out.line("#endif")
    out.line('return "unknown";')
    out.dedent()
    out.line("}")
    out.line()
    out.line("std::string frame(const std::string& name, Span<const std::uint8_t> bytes) {")
    out.indent()
    for interface in sorted(project.interfaces):
        ns = f"model::{interface}"
        out.line(f'if (name == "{interface}") {{')
        out.line(
            f"    generated_tests::Loop_{_identifier(interface)}<"
            f"2U * {ns}::kMaxFrameSize + 16U> loop;"
        )
        out.line("    return frame_answer(loop, bytes);")
        out.line("}")
    out.line('return "unknown";')
    out.dedent()
    out.line("}")
    out.line()
    for text in HARNESS_MAIN.splitlines():
        out.line(text)
    return out.text()


FUZZ_HEAD = """\
using wireforge::Span;
using wireforge::Status;

/// Stops the fuzzer: a decoded value did not survive encode/decode unchanged.
[[noreturn]] void broken_round_trip() noexcept { std::abort(); }

/// Decodes @p input; a value that encodes must decode and encode to the same bytes again.
template <typename T>
void round_trip(Span<const std::uint8_t> input, const wireforge::Encoding& encoding) noexcept {
    T value{};
    if (deserialize(input, value, encoding) != Status::Ok) {
        return;
    }
    wireforge::testing::WireBuffer<T> first{};
    std::size_t first_size = 0U;
    if (serialize(value, Span<std::uint8_t>(first), first_size, encoding) != Status::Ok) {
        return; // decoding without range checks can produce values that do not encode
    }
    const Span<const std::uint8_t> encoded(first.data(), first_size);
    T again{};
    if (deserialize(encoded, again, encoding) != Status::Ok) {
        broken_round_trip();
    }
    wireforge::testing::WireBuffer<T> second{};
    std::size_t second_size = 0U;
    if (serialize(again, Span<std::uint8_t>(second), second_size, encoding) != Status::Ok ||
        !wireforge::testing::same_bytes(encoded,
                                        Span<const std::uint8_t>(second.data(), second_size))) {
        broken_round_trip();
    }
}

/// Streams @p input through @p loop in chunks whose sizes come from the input itself.
template <typename Loop> void stream(Loop& loop, Span<const std::uint8_t> input) noexcept {
    std::size_t offset = 0U;
    while (offset < input.size()) {
        const std::size_t chunk = 1U + (input[offset] % 255U);
        static_cast<void>(loop.link.send_frame(input.subspan(offset, chunk)));
        static_cast<void>(loop.link.poll());
        offset += chunk;
    }
}
"""


def fuzz_source(project: Project) -> str:
    """Return the libFuzzer target: the first byte selects a message or an interface."""
    out = Writer()
    names = Names(project)
    _header(
        out,
        project,
        [*_interface_includes(project), "handlers.hpp", "wire_support.hpp"],
        ("array", "cstddef", "cstdint", "cstdlib"),
    )
    for text in FUZZ_HEAD.splitlines():
        out.line(text)
    out.line()
    out.line("} // namespace")
    out.line()
    targets = len(project.messages) + len(project.interfaces)
    out.line("/// @brief libFuzzer entry point.")
    out.line('extern "C" int LLVMFuzzerTestOneInput(const std::uint8_t* data, std::size_t size) {')
    out.indent()
    out.line("const Span<const std::uint8_t> all(data, size);")
    out.line("if (all.empty()) {")
    out.line("    return 0;")
    out.line("}")
    out.line("const Span<const std::uint8_t> input = all.subspan(1U);")
    out.line(f"switch (all[0] % {targets}U) {{")
    index = 0
    for message in project.messages.values():
        if message.condition is not None:
            out.line(f"#if {names.condition(message.condition)}")
        out.line(f"case {index}U:")
        out.line(f"    round_trip<model::{message.name}>(input, model::k{message.name}Encoding);")
        out.line("    break;")
        if message.condition is not None:
            out.line("#endif")
        index += 1
    for interface in sorted(project.interfaces):
        out.line(f"case {index}U: {{")
        out.line(
            f"    generated_tests::Loop_{_identifier(interface)}<"
            f"model::{interface}::kMaxFrameSize + 16U> loop;"
        )
        out.line("    stream(loop, input);")
        out.line("    break;")
        out.line("}")
        index += 1
    out.line("default:")
    out.line("    break;")
    out.line("}")
    out.line("return 0;")
    out.dedent()
    out.line("}")
    return out.text()


# --- selftest.cpp ------------------------------------------------------------------------------

SELFTEST_HEAD = """\
using wireforge::Span;
using wireforge::Status;
using Bytes = Span<const std::uint8_t>;

/// Writes @p text to standard output.
void print(const char* text) noexcept { static_cast<void>(std::fputs(text, stdout)); }

/// Writes @p value in decimal to standard output.
void print(int value) noexcept {
    std::array<char, 16> digits{};
    const std::to_chars_result written =
        std::to_chars(digits.data(),
                      std::next(digits.data(), static_cast<std::ptrdiff_t>(digits.size() - 1U)),
                      value);
    *written.ptr = '\\0';
    print(digits.data());
}

/// Counts the checks of the self-test and prints every failure.
class Checks {
public:
    /// Records one check named @p name / @p what.
    void expect(bool passed, const char* name, const char* what) noexcept {
        ++count_;
        if (!passed) {
            ++failures_;
            print("FAIL ");
            print(name);
            print(": ");
            print(what);
            print("\\n");
        }
    }
    /// Number of failed checks.
    [[nodiscard]] int failures() const noexcept { return failures_; }
    /// Number of checks.
    [[nodiscard]] int count() const noexcept { return count_; }

private:
    int count_ = 0;
    int failures_ = 0;
};

bool same(Bytes lhs, Bytes rhs) noexcept {
    if (lhs.size() != rhs.size()) {
        return false;
    }
    for (std::size_t index = 0U; index < lhs.size(); ++index) {
        if (lhs[index] != rhs[index]) {
            return false;
        }
    }
    return true;
}

/// @p expected encodes to @p payload, which decodes to a value that encodes to it again.
template <typename T>
void round_trip(Checks& checks, const char* name, const T& expected, Bytes payload,
                const wireforge::Encoding& encoding) noexcept {
    std::array<std::uint8_t, max_wire_size(wireforge::type_tag<T>) + 1U> buffer{};
    std::size_t size = 0U;
    const Status encoded = serialize(expected, Span<std::uint8_t>(buffer), size, encoding);
    checks.expect(encoded == Status::Ok && same(Bytes(buffer.data(), size), payload), name,
                  "serialize");
    T decoded{};
    checks.expect(deserialize(payload, decoded, encoding) == Status::Ok, name, "deserialize");
    const Status again = serialize(decoded, Span<std::uint8_t>(buffer), size, encoding);
    checks.expect(again == Status::Ok && same(Bytes(buffer.data(), size), payload), name,
                  "encode the decoded value");
}

/// @p frame sent through a loopback link reaches the handler with @p payload.
template <typename Loop>
void loopback(Checks& checks, const char* name, Loop& loop, Bytes frame, Bytes payload) noexcept {
    checks.expect(loop.endpoint.status() == Status::Ok, name, "endpoint");
    checks.expect(loop.link.send_frame(frame) == Status::Ok, name, "send");
    const wireforge::PollResult polled = loop.link.poll();
    checks.expect(polled.frames == 1U && polled.errors == 0U, name, "dispatch");
    checks.expect(loop.recorder.count() == 1U && same(loop.recorder.last(), payload), name,
                  "handler");
}

/// Decoding @p payload as a @p T gives @p expected.
template <typename T>
void decode_status(Checks& checks, const char* name, Bytes payload, Status expected,
                   const wireforge::Encoding& encoding) noexcept {
    T value{};
    checks.expect(deserialize(payload, value, encoding) == expected, name, "decode status");
}
"""


class _SelfTest:
    """Writes selftest.cpp."""

    def __init__(self, project: Project) -> None:
        self.project = project
        self.assignments = _Assignments(project)
        self.out = Writer()
        self.calls: dict[str | None, list[str]] = {}

    def render(
        self,
        vectors: list[dict[str, Any]],
        profiles: tuple[ProfileCases, ...],
        runs: list[VectorRun],
    ) -> str:
        out = self.out
        _header(
            out,
            self.project,
            [*_interface_includes(self.project), "handlers.hpp"],
            ("array", "charconv", "cstddef", "cstdint", "cstdio", "iterator"),
        )
        for text in SELFTEST_HEAD.splitlines():
            out.line(text)
        out.line()
        for index, vector in enumerate(vectors):
            _open_profile(out, vector["profile"])
            self.golden(index, vector)
            _close_profile(out, vector["profile"])
        for profile in profiles:
            _open_profile(out, profile.profile)
            for message in profile.messages:
                self.decodes(profile.profile, message)
            _close_profile(out, profile.profile)
        for run in runs:
            _open_profile(out, run.profile)
            self.requirement_vector(run)
            _close_profile(out, run.profile)
        out.line("} // namespace")
        out.line()
        self.main([profile.profile for profile in profiles])
        return out.text()

    def function(self, profile: str | None, name: str) -> None:
        self.calls.setdefault(profile, []).append(name)
        self.out.line(f"void {name}(Checks& checks) noexcept {{")
        self.out.indent()

    def end(self) -> None:
        self.out.dedent()
        self.out.line("}")
        self.out.line()

    def golden(self, index: int, vector: dict[str, Any]) -> None:
        out = self.out
        project = self.project
        message = project.messages[vector["message"]]
        interface = project.interfaces[vector["interface"]]
        label = f"{vector['interface']}/{vector['message']}/{vector['sample']}"
        self.function(vector["profile"], f"golden_{index}")
        _byte_array(out, "kPayload", bytes.fromhex(vector["payload"]))
        _byte_array(out, "kFrame", bytes.fromhex(vector["frame"]))
        # A user-type field is opaque: the value built from the golden pattern leaves it
        # default-constructed, so its encoded bytes cannot be compared against the reference
        # codec's bytes for that field.
        opaque = _has_user_type(project, message.fields)
        ns = f"model::{interface.name}"
        encoding = encoding_literal(project, message, interface)
        lines: list[str] = []
        if opaque:
            lines.append(
                f'decode_status<model::{message.name}>(checks, "{label}", Bytes(kPayload), '
                f"Status::Ok, {encoding});"
            )
        else:
            statements = self.assignments.struct(message, vector["value"], "expected")
            out.line(f"{'' if statements else 'const '}model::{message.name} expected{{}};")
            for statement in statements:
                out.line(statement)
            lines += [
                f'round_trip(checks, "{label}", expected, Bytes(kPayload), {encoding});',
                f"std::array<std::uint8_t, {ns}::kMaxFrameSize> buffer{{}};",
                "std::size_t size = 0U;",
                f"const Status framed = {ns}::encode_frame(",
                "    expected, Span<std::uint8_t>(buffer), size);",
                "const bool same_frame = same(Bytes(buffer.data(), size), Bytes(kFrame));",
                f'checks.expect(framed == Status::Ok && same_frame, "{label}", "encode_frame");',
            ]
        lines += [
            f"generated_tests::Loop_{_identifier(interface.name)}<{ns}::kMaxFrameSize> loop;",
            f'loopback(checks, "{label}", loop, Bytes(kFrame), Bytes(kPayload));',
        ]
        for text in lines:
            out.line(text)
        self.end()

    def requirement_vector(self, run: VectorRun) -> None:
        vector = run.vector
        self.function(run.profile, f"requirement_vector_{_identifier(vector.name)}")
        for text in _VectorCode(self.project, vector.name, selftest=True).lines(vector):
            self.out.line(text)
        self.end()

    def decodes(self, profile: str | None, cases: MessageCases) -> None:
        name = cases.message.name
        for case in cases.decodes:
            self.function(profile, f"decode_{_identifier(name)}_{_identifier(case.name)}")
            _byte_array(self.out, "kPayload", case.payload)
            self.out.line(
                f'decode_status<model::{name}>(checks, "{name}/{case.name}", Bytes(kPayload), '
                f"Status::{case.status}, model::k{name}Encoding);"
            )
            self.end()

    def main(self, profiles: list[str | None]) -> None:
        out = self.out
        out.line("/// Runs every check; the exit code is 0 when all pass.")
        out.line("int main() {")
        out.indent()
        out.line("Checks checks;")
        for profile in dict.fromkeys(profiles):
            if profile:
                out.line(f"#if defined(WIREFORGE_PROFILE_{profile})")
            for call in self.calls.get(profile, []):
                out.line(f"{call}(checks);")
            if profile:
                out.line("#endif")
        for text in (
            "print(checks.count());",
            'print(" checks, ");',
            "print(checks.failures());",
            'print(" failures\\n");',
        ):
            out.line(text)
        out.line("return checks.failures() == 0 ? 0 : 1;")
        out.dedent()
        out.line("}")


def selftest_source(
    project: Project,
    vectors: list[dict[str, Any]],
    profiles: tuple[ProfileCases, ...],
    runs: list[VectorRun] | None = None,
) -> str:
    """Return the portable self-test: no test framework, exceptions, RTTI or heap.

    It checks the golden vectors (payload, frame, loopback) and the decode cases of every message,
    prints failures and returns non-zero when any check fails. It runs on cross targets
    (big-endian, 32-bit) under an emulator.
    """
    return _SelfTest(project).render(vectors, profiles, runs or [])

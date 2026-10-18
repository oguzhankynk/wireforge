"""Import of CAN databases (DBC files) as WireForge projects.

A DBC file lists the messages of a CAN network (`BO_`), their signals (`SG_`), value tables
(`VAL_`), comments (`CM_`) and a few attributes (`BA_`). The importer reads the subset that decides
the bytes of a frame and turns every message into a WireForge message:

- the signals become bit-fields in the order of their position, gaps become reserved bits;
- Intel signals (little endian, `@1`) give a little-endian, LSB-first message; Motorola signals
  (big endian, `@0`) a big-endian, MSB-first message. A message uses one byte order only;
- factor and offset become `scale` and `offset`, value tables become enums, the physical limits
  become raw `min` and `max`;
- the interfaces carry a header with the CAN identifier (the extended-frame flag is bit 31, as in
  the DBC file and in SocketCAN) and the data length.

What the importer cannot express stays visible: multiplexed signals, extended multiplexing, frames
longer than 8 bytes, mixed byte orders, overlapping signals. They are errors with file and line, or
with `skip_unsupported` the message is left out and listed in the notes. Statements that do not
change a frame (version, nodes' attributes, environment variables ...) are listed in the notes too.
"""

from __future__ import annotations

import keyword
import math
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from wireforge.issues import Issue
from wireforge.scaling import integer_limits

if TYPE_CHECKING:
    from wireforge.importers import Project

BYTE = 8
MAX_DATA_BYTES = 8
FRAME_OVERHEAD = 5  # identifier (4 bytes) and data length (1 byte)
INTEL = "1"
EXTENDED_FLAG = 0x80000000
WIDTHS = (8, 16, 32, 64)
FLOAT_SIZES = {1: 32, 2: 64}
TOLERANCE = 1e-6
ORPHAN_SIGNALS = "VECTOR__INDEPENDENT_SIG_MSG"
STRUCTURE = frozenset({"VERSION", "BS_:", "BS_", "BU_:", "BU_", "BA_DEF_", "BA_DEF_DEF_"})
MESSAGE_COMMENT_WORDS = 2
SIGNAL_COMMENT_WORDS = 3
VALUE_TABLE_WORDS = 2
MULTI_LINE = frozenset(
    {
        "CM_",
        "BA_DEF_",
        "BA_DEF_DEF_",
        "BA_DEF_REL_",
        "BA_DEF_DEF_REL_",
        "BA_",
        "BA_REL_",
        "VAL_",
        "VAL_TABLE_",
        "SIG_VALTYPE_",
        "SIG_GROUP_",
        "SG_MUL_VAL_",
        "BO_TX_BU_",
        "EV_",
        "ENVVAR_DATA_",
        "SGTYPE_",
        "SGTYPE_VAL_",
        "BA_DEF_SGTYPE_",
        "BA_SGTYPE_",
        "SIG_TYPE_REF_",
        "CAT_DEF_",
        "CAT_",
        "FILTER",
    }
)
MESSAGE_LINE = re.compile(r"^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\w+)\s*$")
SIGNAL_LINE = re.compile(
    r"^SG_\s+(\w+)\s*(M|m\d+M?)?\s*:\s*(\d+)\|(\d+)@([01])([+-])\s*"
    r"\(\s*([^,\s]+)\s*,\s*([^)\s]+)\s*\)\s*\[\s*([^|\s]+)\s*\|\s*([^\]\s]+)\s*\]\s*"
    r'"([^"]*)"\s*(.*)$'
)
NUMBER = re.compile(r"^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$")
QUOTED = re.compile(r'"((?:[^"\\]|\\.)*)"')


class DbcImportError(Exception):
    """The database cannot be imported; `issues` locate the problems."""

    def __init__(self, issues: list[Issue]) -> None:
        """Store the problems of the database."""
        super().__init__("; ".join(str(issue) for issue in issues))
        self.issues = issues


@dataclass
class Signal:
    """One `SG_` line with what the other statements add to it."""

    name: str
    line: int
    multiplex: str | None
    start: int
    size: int
    little: bool
    signed: bool
    factor: float
    offset: float
    minimum: float
    maximum: float
    unit: str
    receivers: tuple[str, ...]
    comment: str = ""
    values: dict[int, str] = field(default_factory=dict)
    kind: int = 0  # 0 integer, 1 f32, 2 f64
    start_value: float | None = None

    @property
    def position(self) -> int:
        """Return the first bit of the signal in the order the bits are written."""
        if self.little:
            return self.start
        return BYTE * (self.start // BYTE) + (BYTE - 1 - self.start % BYTE)


@dataclass
class Message:
    """One `BO_` line with its signals."""

    frame_id: int
    name: str
    line: int
    size: int
    transmitter: str
    signals: list[Signal] = field(default_factory=list)
    comment: str = ""
    cycle_time: int | None = None


@dataclass
class Database:
    """What the importer read from a DBC file."""

    source: str
    messages: dict[int, Message] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def problem(self, line: int | None, text: str) -> None:
        """Record an error at a line."""
        self.issues.append(Issue(self.source, line, "", text))


# --- reading --------------------------------------------------------------------------------------


def _statement_end(text: str) -> int:
    """Return the index of the `;` that ends a statement (outside quotes), or -1."""
    quoted = False
    skip = False
    for index, char in enumerate(text):
        if skip:
            skip = False
        elif char == "\\" and quoted:
            skip = True
        elif char == '"':
            quoted = not quoted
        elif char == ";" and not quoted:
            return index
    return -1


def _statements(text: str) -> list[tuple[int, str]]:
    """Split the file into (first line, statement) pairs; a statement may span lines."""
    lines = text.splitlines()
    result: list[tuple[int, str]] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("NS_") and ":" in stripped:  # the list of symbols that follows
            while index + 1 < len(lines) and (not lines[index + 1] or lines[index + 1][0] in " \t"):
                index += 1
            index += 1
            continue
        keyword_ = stripped.split(None, 1)[0] if stripped else ""
        first = index + 1
        if keyword_ in MULTI_LINE:
            collected = stripped
            while _statement_end(collected) < 0 and index + 1 < len(lines):
                index += 1
                collected += "\n" + lines[index].strip()
            result.append((first, collected))
        elif stripped:
            result.append((first, stripped))
        index += 1
    return result


def _read_number(text: str) -> float | None:
    return float(text) if NUMBER.match(text) else None


def _signal(db: Database, line: int, text: str) -> Signal | None:
    match = SIGNAL_LINE.match(text)
    if match is None:
        db.problem(line, f"cannot read the signal: {text}")
        return None
    name, multiplex, start, size, order, sign, factor, offset, low, high, unit, rest = (
        match.groups()
    )
    numbers = [_read_number(value) for value in (factor, offset, low, high)]
    if any(value is None for value in numbers):
        db.problem(line, f"signal {name}: factor, offset and limits must be numbers")
        return None
    scale, shift, minimum, maximum = (float(value or 0.0) for value in numbers)
    receivers = tuple(part for part in re.split(r"[,\s]+", rest.strip()) if part)
    return Signal(
        name=name,
        line=line,
        multiplex=multiplex,
        start=int(start),
        size=int(size),
        little=order == INTEL,
        signed=sign == "-",
        factor=scale,
        offset=shift,
        minimum=minimum,
        maximum=maximum,
        unit=unit,
        receivers=receivers,
    )


def _message(db: Database, line: int, text: str) -> Message | None:
    match = MESSAGE_LINE.match(text)
    if match is None:
        db.problem(line, f"cannot read the message: {text}")
        return None
    frame_id, name, size, transmitter = match.groups()
    if int(frame_id) in db.messages:
        db.problem(line, f"message {name}: identifier {frame_id} is already used")
        return None
    message = Message(int(frame_id), name, line, int(size), transmitter)
    db.messages[message.frame_id] = message
    return message


def _find_signal(db: Database, frame_id: str, name: str) -> Signal | None:
    message = db.messages.get(int(frame_id))
    if message is None:
        return None
    return next((item for item in message.signals if item.name == name), None)


def _comment(db: Database, text: str) -> None:
    parts = text.removeprefix("CM_").strip()
    match = QUOTED.search(parts)
    if match is None:
        return
    body = match.group(1).replace('\\"', '"').strip()
    words = parts[: match.start()].split()
    if words[:1] == ["BO_"] and len(words) == MESSAGE_COMMENT_WORDS and words[1].isdigit():
        message = db.messages.get(int(words[1]))
        if message is not None:
            message.comment = body
    elif words[:1] == ["SG_"] and len(words) == SIGNAL_COMMENT_WORDS and words[1].isdigit():
        found = _find_signal(db, words[1], words[2])
        if found is not None:
            found.comment = body


def _values(db: Database, line: int, text: str) -> None:
    head = text.removeprefix("VAL_").split('"', 1)[0].split()
    if len(head) < VALUE_TABLE_WORDS or not head[0].isdigit():
        return  # a value table of an environment variable or a named table
    signal = _find_signal(db, head[0], head[1])
    if signal is None:
        db.problem(line, f"value table of an unknown signal {head[1]} of message {head[0]}")
        return
    pairs = re.findall(r'(-?\d+)\s+"((?:[^"\\]|\\.)*)"', text)
    signal.values = {int(value): label for value, label in pairs}


def _attribute(db: Database, text: str) -> bool:
    """Apply the attributes that change the import; return whether the statement was used."""
    match = re.match(r'BA_\s+"(\w+)"\s+BO_\s+(\d+)\s+([-\d.eE+]+)\s*;', text)
    if match is not None and match.group(1) == "GenMsgCycleTime":
        message = db.messages.get(int(match.group(2)))
        if message is not None:
            message.cycle_time = int(float(match.group(3)))
        return True
    match = re.match(r'BA_\s+"(\w+)"\s+SG_\s+(\d+)\s+(\w+)\s+([-\d.eE+]+)\s*;', text)
    if match is not None and match.group(1) == "GenSigStartValue":
        signal = _find_signal(db, match.group(2), match.group(3))
        if signal is not None:
            signal.start_value = float(match.group(4))
        return True
    return False


def _value_type(db: Database, line: int, text: str) -> None:
    match = re.match(r"SIG_VALTYPE_\s+(\d+)\s+(\w+)\s*:\s*(\d)\s*;", text)
    if match is None:
        db.problem(line, f"cannot read the signal type: {text}")
        return
    signal = _find_signal(db, match.group(1), match.group(2))
    if signal is None:
        db.problem(line, f"signal type of an unknown signal {match.group(2)}")
    else:
        signal.kind = int(match.group(3))


@dataclass
class _State:
    """Where the reader is in the file."""

    current: Message | None = None
    ignored: dict[str, int] = field(default_factory=dict)


def _add_signal(db: Database, state: _State, line: int, statement: str) -> None:
    signal = _signal(db, line, statement)
    if state.current is None:
        db.problem(line, "a signal outside of a message")
    elif signal is not None:
        state.current.signals.append(signal)


def _apply(db: Database, state: _State, line: int, statement: str) -> None:
    """Apply one statement to the database."""
    word = statement.split(None, 1)[0]
    if word == "BO_":
        state.current = _message(db, line, statement)
    elif word == "SG_":
        _add_signal(db, state, line, statement)
    elif word == "CM_":
        _comment(db, statement)
    elif word == "VAL_":
        _values(db, line, statement)
    elif word == "SIG_VALTYPE_":
        _value_type(db, line, statement)
    elif word == "SG_MUL_VAL_":
        db.problem(line, "extended multiplexing (SG_MUL_VAL_) is not supported")
    elif word not in STRUCTURE and not (word == "BA_" and _attribute(db, statement)):
        state.ignored[word] = state.ignored.get(word, 0) + 1


def parse(text: str, source: str = "network.dbc") -> Database:
    """Read the messages and signals of a DBC file."""
    db = Database(source)
    state = _State()
    for line, statement in _statements(text):
        _apply(db, state, line, statement)
    for word, count in sorted(state.ignored.items()):
        db.notes.append(f"{count} statement(s) of kind {word} ignored")
    return db


# --- conversion -----------------------------------------------------------------------------------


class _UnsupportedError(Exception):
    """A message that WireForge cannot express; `line` locates the cause."""

    def __init__(self, line: int, text: str) -> None:
        super().__init__(text)
        self.line = line
        self.text = text


@dataclass
class _Converted:
    """A message as a WireForge message with the enums it needs."""

    name: str
    body: dict[str, Any]
    enums: dict[str, dict[str, Any]] = field(default_factory=dict)


def _identifier(text: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", text).strip("_")
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"v_{cleaned}"
    return f"{cleaned}_" if keyword.iskeyword(cleaned) else cleaned


def _width(size: int) -> int:
    return next(item for item in WIDTHS if item >= size)


def _integer_type(signal: Signal) -> str:
    return f"{'i' if signal.signed else 'u'}{_width(signal.size)}"


def _snap(value: float) -> float:
    nearest = round(value)
    return float(nearest) if abs(value - nearest) < TOLERANCE * max(1.0, abs(value)) else value


def _number(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _raw_range(signal: Signal, notes: list[str], where: str) -> tuple[float, float] | None:
    """Return the raw `min` and `max` of the physical limits, or None when there are none."""
    if signal.minimum == 0 and signal.maximum == 0:
        return None
    low, high = sorted(
        _snap((limit - signal.offset) / signal.factor) for limit in (signal.minimum, signal.maximum)
    )
    if signal.kind == 0:
        narrowed = (math.ceil(low), math.floor(high))
        if (low, high) != (float(narrowed[0]), float(narrowed[1])):
            notes.append(f"{where}: the limits are not whole raw values and were narrowed")
        return float(narrowed[0]), float(narrowed[1])
    return low, high


def _scaling(signal: Signal) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if signal.factor != 1:
        result["scale"] = _number(signal.factor)
    if signal.offset != 0:
        result["offset"] = _number(signal.offset)
    return result


def _limits(signal: Signal, notes: list[str], where: str) -> dict[str, Any]:
    raw = _raw_range(signal, notes, where)
    if raw is None:
        return {}
    if signal.kind != 0:
        return {"min": raw[0], "max": raw[1]}
    full = integer_limits(_integer_type(signal), signal.size)
    return {} if (int(raw[0]), int(raw[1])) == full else {"min": int(raw[0]), "max": int(raw[1])}


def _signal_field(signal: Signal, notes: list[str], where: str) -> dict[str, Any]:
    """Return the field of a signal (before value tables and defaults)."""
    if signal.kind == 0:
        item: dict[str, Any] = {
            "name": signal.name,
            "type": _integer_type(signal),
            "bits": signal.size,
        }
    else:
        item = {"name": signal.name, "type": "f32" if signal.kind == 1 else "f64"}
    item |= _scaling(signal) | _limits(signal, notes, where)
    if signal.unit:
        item["units"] = signal.unit
    if signal.comment:
        item["description"] = signal.comment
    return item


def _enum_values(signal: Signal) -> dict[str, int] | None:
    """Return the members of a value table, or None when it cannot be an enum."""
    if not signal.values or signal.kind != 0 or signal.factor != 1 or signal.offset != 0:
        return None
    low, high = integer_limits(_integer_type(signal), signal.size)
    members: dict[str, int] = {}
    for value, label in signal.values.items():
        name = _identifier(label)
        if name in members or not low <= value <= high:
            return None
        members[name] = value
    return members


def _table_text(signal: Signal) -> str:
    return "; ".join(f"{value} = {label}" for value, label in sorted(signal.values.items()))


def _with_values(
    message: Message, signal: Signal, item: dict[str, Any], converted: _Converted
) -> None:
    """Turn the value table of a signal into an enum, or into its description."""
    members = _enum_values(signal)
    if members is None:
        if signal.values:
            note = f"Values: {_table_text(signal)}."
            item["description"] = f"{item['description']} {note}" if "description" in item else note
        return
    name = _identifier(f"{message.name}_{signal.name}")
    converted.enums[name] = {"type": item["type"], "values": members}
    item["type"] = name
    item.pop("min", None)
    item.pop("max", None)
    if 0 not in members.values():
        item["default"] = next(iter(members))


def _default(signal: Signal, item: dict[str, Any], enums: dict[str, dict[str, Any]]) -> None:
    """Set the default of a field from `GenSigStartValue` (a physical value)."""
    if signal.start_value is None:
        return
    raw = _snap((signal.start_value - signal.offset) / signal.factor)
    if signal.kind != 0:
        item["default"] = raw
        return
    value = round(raw)
    low, high = integer_limits(_integer_type(signal), signal.size)
    if not (low <= value <= high and abs(raw - value) < TOLERANCE):
        return
    enum = enums.get(item["type"])
    if enum is None:
        item["default"] = value
        return
    names = [name for name, number in enum["values"].items() if number == value]
    if names:
        item["default"] = names[0]


def _gap(position: int, size: int) -> dict[str, Any]:
    return {
        "name": f"reserved_{position}",
        "type": f"u{_width(size)}",
        "bits": size,
        "reserved": True,
    }


def _check_message(message: Message) -> None:
    if not 1 <= message.size <= MAX_DATA_BYTES:
        text = f"message {message.name}: {message.size} data bytes (1 to 8 are supported)"
        raise _UnsupportedError(message.line, text)
    little = {signal.little for signal in message.signals}
    if len(little) > 1:
        text = f"message {message.name}: Intel and Motorola signals in one message"
        raise _UnsupportedError(message.line, text)
    for signal in message.signals:
        where = f"message {message.name}, signal {signal.name}"
        if signal.multiplex is not None:
            raise _UnsupportedError(signal.line, f"{where}: multiplexed signals are not supported")
        if signal.factor == 0:
            raise _UnsupportedError(signal.line, f"{where}: the factor is 0")
        if not 1 <= signal.size <= BYTE * MAX_DATA_BYTES:
            raise _UnsupportedError(signal.line, f"{where}: size {signal.size} bits")
        if signal.kind != 0 and (
            signal.size != FLOAT_SIZES.get(signal.kind) or signal.position % BYTE
        ):
            raise _UnsupportedError(signal.line, f"{where}: a float signal must fill whole bytes")


def _fields(message: Message, converted: _Converted, notes: list[str]) -> list[dict[str, Any]]:
    """Return the fields of a message: signals in order, gaps as reserved bits."""
    fields: list[dict[str, Any]] = []
    cursor = 0
    for signal in sorted(message.signals, key=lambda item: item.position):
        where = f"message {message.name}, signal {signal.name}"
        if signal.position < cursor:
            raise _UnsupportedError(signal.line, f"{where}: overlaps another signal")
        if signal.position + signal.size > message.size * BYTE:
            raise _UnsupportedError(signal.line, f"{where}: does not fit in {message.size} bytes")
        if signal.position > cursor:
            fields.append(_gap(cursor, signal.position - cursor))
        item = _signal_field(signal, notes, where)
        _with_values(message, signal, item, converted)
        _default(signal, item, converted.enums)
        fields.append(item)
        cursor = signal.position + signal.size
    if cursor < message.size * BYTE:
        fields.append(_gap(cursor, message.size * BYTE - cursor))
    return fields


def _convert(message: Message, notes: list[str]) -> _Converted:
    _check_message(message)
    converted = _Converted(_identifier(message.name), {})
    little = bool(message.signals) and message.signals[0].little
    body: dict[str, Any] = {"id": message.frame_id}
    description = message.comment
    if message.cycle_time:
        text = f"Cycle time {message.cycle_time} ms."
        description = f"{description} {text}" if description else text
    if description:
        body["description"] = description
    if little:
        body["endian"], body["bit_order"] = "little", "lsb_first"
    body["fields"] = _fields(message, converted, notes)
    converted.body = body
    return converted


def _interface(direction: str, names: list[str]) -> dict[str, Any]:
    return {
        "version": "1.0.0",
        "direction": direction,
        "header": {
            "fields": [
                {"name": "canId", "type": "u32", "role": "message_id"},
                {"name": "dlc", "type": "u8", "role": "payload_length"},
            ]
        },
        "framing": {"kind": "length_prefixed", "max_frame_size": FRAME_OVERHEAD + MAX_DATA_BYTES},
        "messages": names,
    }


def _interfaces(
    db: Database, converted: dict[int, _Converted], node: str | None
) -> dict[str, dict[str, Any]]:
    if node is None:
        return {"can": _interface("both", [item.name for item in converted.values()])}
    sent = [
        converted[frame_id].name
        for frame_id in converted
        if db.messages[frame_id].transmitter == node
    ]
    received = [
        converted[frame_id].name
        for frame_id in converted
        if db.messages[frame_id].transmitter != node
        and any(node in signal.receivers for signal in db.messages[frame_id].signals)
    ]
    result = {f"{node}_rx": _interface("rx", received), f"{node}_tx": _interface("tx", sent)}
    return {name: item for name, item in result.items() if item["messages"]}


def from_dbc(
    text: str,
    options: Project,
    node: str | None = None,
    *,
    skip_unsupported: bool = False,
    source: str = "network.dbc",
) -> tuple[dict[str, Any], list[str]]:
    """Return the project document of a DBC file and the notes about what was left out.

    With `node`, the interfaces are those of that network node (`<node>_rx`, `<node>_tx`);
    without it, one interface `can` carries every message. With `skip_unsupported`, a message that
    cannot be expressed is left out and listed in the notes instead of failing the import.
    """
    db = parse(text, source)
    notes = list(db.notes)
    converted: dict[int, _Converted] = {}
    for frame_id, message in db.messages.items():
        if message.name == ORPHAN_SIGNALS:
            notes.append(f"{ORPHAN_SIGNALS} (signals without a message) ignored")
            continue
        try:
            converted[frame_id] = _convert(message, notes)
        except _UnsupportedError as error:
            if skip_unsupported:
                notes.append(f"{source}:{error.line}: left out: {error.text}")
            else:
                db.problem(error.line, error.text)
    _check_names(db, converted)
    if node is not None and node not in {m.transmitter for m in db.messages.values()} | {
        name for m in db.messages.values() for s in m.signals for name in s.receivers
    }:
        db.problem(None, f"node {node} does not send or receive a message")
    if not converted and not db.issues:
        db.problem(None, "no message could be imported")
    if db.issues:
        raise DbcImportError(db.issues)
    return _document(db, converted, options, node), notes


def _check_names(db: Database, converted: dict[int, _Converted]) -> None:
    names: dict[str, int] = {}
    for frame_id, item in converted.items():
        line = db.messages[frame_id].line
        if item.name in names:
            db.problem(line, f"message {item.name}: the name is already used after cleaning it")
        names[item.name] = line


def _document(
    db: Database, converted: dict[int, _Converted], options: Project, node: str | None
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": 1,
        "project": options.name,
        "version": options.version,
    }
    if options.namespace:
        document["namespace"] = options.namespace
    document["defaults"] = {"endian": "big", "bit_order": "msb_first"}
    enums = {name: body for item in converted.values() for name, body in item.enums.items()}
    if enums:
        document["enums"] = enums
    document["messages"] = {item.name: item.body for item in converted.values()}
    document["interfaces"] = _interfaces(db, converted, node)
    return document

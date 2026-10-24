"""Reference implementation of the MAVLink 2 checksum (`mav2sum::Crc`) of this protocol model."""

import re
from pathlib import Path

SKIPPED = 1  # the start-of-frame byte is not part of the checksum
MESSAGE_ID_AT = 7  # index of the three-byte message id (little endian) in the frame
MESSAGE_ID_BYTES = 3
MASK = 0xFFFF
BYTE = 0xFF
INIT = 0xFFFF

# The CRC_EXTRA byte of each message (a hash of its name and its base fields), from the list the C
# and C++ code use.
_TABLE = Path(__file__).resolve().parents[1] / "app" / "include" / "mav2sum" / "crc_extra.h"
CRC_EXTRA = {
    int(identifier): int(extra)
    for identifier, extra in re.findall(r"X\((\d+)U, *(\d+)U\)", _TABLE.read_text(encoding="utf-8"))
}


def _accumulate(crc: int, byte: int) -> int:
    """Add one byte to a CRC-16/MCRF4XX (the MAVLink "X.25" accumulate)."""
    tmp = byte ^ (crc & BYTE)
    tmp = (tmp ^ (tmp << 4)) & BYTE
    return ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & MASK


def mavlink2_crc(data: bytes) -> int:
    """Return the checksum of a MAVLink 2 frame without its checksum field."""
    crc = INIT
    for byte in data[SKIPPED:]:
        crc = _accumulate(crc, byte)
    end = MESSAGE_ID_AT + MESSAGE_ID_BYTES
    message_id = int.from_bytes(data[MESSAGE_ID_AT:end], "little") if len(data) >= end else 0
    return _accumulate(crc, CRC_EXTRA.get(message_id, 0))


PLUGINS = {"mav2sum::Crc": mavlink2_crc}

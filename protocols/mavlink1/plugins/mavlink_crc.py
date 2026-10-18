"""Reference implementation of the MAVLink 1 checksum (`mavsum::Crc`) of this protocol model."""

SKIPPED = 1  # the start-of-frame byte is not part of the checksum
MESSAGE_ID_AT = 5  # index of the message id in the frame
MASK = 0xFFFF
BYTE = 0xFF
INIT = 0xFFFF

# The CRC_EXTRA byte of each message: a hash of its name and its field names and types.
CRC_EXTRA = {0: 50, 30: 39, 33: 104, 76: 152}


def _accumulate(crc: int, byte: int) -> int:
    """Add one byte to a CRC-16/MCRF4XX (the MAVLink "X.25" accumulate)."""
    tmp = byte ^ (crc & BYTE)
    tmp = (tmp ^ (tmp << 4)) & BYTE
    return ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & MASK


def mavlink_crc(data: bytes) -> int:
    """Return the checksum of a MAVLink 1 frame without its checksum field."""
    crc = INIT
    for byte in data[SKIPPED:]:
        crc = _accumulate(crc, byte)
    message_id = data[MESSAGE_ID_AT] if len(data) > MESSAGE_ID_AT else 0
    return _accumulate(crc, CRC_EXTRA.get(message_id, 0))


PLUGINS = {"mavsum::Crc": mavlink_crc}

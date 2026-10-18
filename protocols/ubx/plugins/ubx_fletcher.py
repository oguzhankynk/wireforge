"""Reference implementation of the UBX checksum (`ubxsum::Fletcher8`) of this protocol model."""

SYNC_BYTES = 2
MASK = 0xFF
BYTE = 8


def ubx_fletcher(data: bytes) -> int:
    """8-bit Fletcher checksum of a UBX frame without its two sync characters.

    The result is `CK_B << 8 | CK_A`, so a little-endian trailer sends CK_A first.
    """
    a = b = 0
    for byte in data[SYNC_BYTES:]:
        a = (a + byte) & MASK
        b = (b + a) & MASK
    return b << BYTE | a


PLUGINS = {"ubxsum::Fletcher8": ubx_fletcher}

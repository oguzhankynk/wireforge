"""Reference implementation of the custom checksum `vendor::SumChecksum` used by this example."""

MASK = 0xFFFF


def vendor_sum(data: bytes) -> int:
    """16-bit sum of all bytes, modulo 2^16."""
    return sum(data) & MASK


PLUGINS = {"vendor::SumChecksum": vendor_sum}

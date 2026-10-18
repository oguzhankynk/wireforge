"""WireForge against an independent implementation of the same protocol (pymodbus).

The Modbus RTU example (examples/05_modbus_rtu) is written from the specification. pymodbus is a
separate implementation of it; the two must agree on the bytes of the frames, on the values in them
and on what a broken CRC is. A disagreement fails the test whichever side is wrong.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pymodbus.framer import FramerRTU
from pymodbus.pdu import DecodePDU
from pymodbus.pdu.register_message import (
    ReadHoldingRegistersRequest,
    ReadHoldingRegistersResponse,
    WriteSingleRegisterRequest,
)

from wireforge import service
from wireforge.codec import CodecError, ReferenceCodec
from wireforge.loader import load_project

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "05_modbus_rtu"
SLAVE = 17


@pytest.fixture(scope="module")
def codec() -> ReferenceCodec:
    project = load_project(EXAMPLE).project
    assert project is not None
    return ReferenceCodec(project, None, service.load_plugins(service.find_plugins(EXAMPLE)))


def _frame(codec: ReferenceCodec, interface: str, message: str, value: dict[str, object]) -> bytes:
    return codec.encode_frame(interface, message, value, {"address": SLAVE})


def _pymodbus_frame(function: int, data: bytes, *, request: bool) -> bytes:
    return FramerRTU(DecodePDU(is_server=request)).encode(bytes([function]) + data, SLAVE, 0)


def test_the_read_request_has_the_same_bytes(codec: ReferenceCodec) -> None:
    ours = _frame(
        codec, "to_server", "ReadHoldingRegistersRequest", {"startAddress": 107, "quantity": 3}
    )
    data = ReadHoldingRegistersRequest(address=107, count=3, dev_id=SLAVE).encode()
    theirs = _pymodbus_frame(3, data, request=True)
    assert ours == theirs == bytes.fromhex("1103006B00037687")


def test_the_read_response_has_the_same_bytes(codec: ReferenceCodec) -> None:
    value = {"byteCount": 6, "registers": [555, 0, 100]}
    ours = _frame(codec, "to_client", "ReadHoldingRegistersResponse", value)
    registers = ReadHoldingRegistersResponse(registers=[555, 0, 100], dev_id=SLAVE).encode()
    theirs = _pymodbus_frame(3, bytes([6]) + registers[1:], request=False)
    assert ours == theirs == bytes.fromhex("110306022B00000064C8BA")


def test_the_write_request_has_the_same_bytes(codec: ReferenceCodec) -> None:
    ours = _frame(codec, "to_server", "WriteSingleRegister", {"registerAddress": 1, "value": 3})
    data = WriteSingleRegisterRequest(address=1, registers=[3], dev_id=SLAVE).encode()
    theirs = _pymodbus_frame(6, data, request=True)
    assert ours == theirs == bytes.fromhex("1106000100039A9B")


def test_pymodbus_reads_what_wireforge_wrote(codec: ReferenceCodec) -> None:
    frame = _frame(
        codec, "to_server", "ReadHoldingRegistersRequest", {"startAddress": 300, "quantity": 125}
    )
    used, device, _, pdu = FramerRTU(DecodePDU(is_server=True)).decode(frame)
    assert (used, device) == (len(frame), SLAVE), "the CRC is accepted"
    request = DecodePDU(is_server=True).decode(pdu)
    assert isinstance(request, ReadHoldingRegistersRequest)
    assert (request.address, request.count) == (300, 125)


def test_wireforge_reads_what_pymodbus_wrote(codec: ReferenceCodec) -> None:
    registers = [1, 65535, 258, 0]
    data = bytes([2 * len(registers)]) + b"".join(value.to_bytes(2, "big") for value in registers)
    frame = _pymodbus_frame(3, data, request=False)
    decoded = codec.decode_frame("to_client", frame)
    assert decoded.message == "ReadHoldingRegistersResponse"
    assert decoded.value["registers"] == registers
    assert decoded.header["address"] == SLAVE


def test_both_refuse_a_broken_crc(codec: ReferenceCodec) -> None:
    frame = bytearray(bytes.fromhex("1103006B00037687"))
    frame[-1] ^= 0x01
    _, _, _, pdu = FramerRTU(DecodePDU(is_server=True)).decode(bytes(frame))
    assert pdu == b"", "pymodbus drops the frame"
    with pytest.raises(CodecError) as error:
        codec.decode_frame("to_server", bytes(frame))
    assert error.value.status == "ChecksumMismatch"


def test_the_crc_is_the_same_on_many_frames(codec: ReferenceCodec) -> None:
    for start in (0, 1, 255, 256, 65534):
        for quantity in (1, 2, 125):
            ours = _frame(
                codec,
                "to_server",
                "ReadHoldingRegistersRequest",
                {"startAddress": start, "quantity": quantity},
            )
            expected = FramerRTU.compute_CRC(ours[:-2]).to_bytes(2, "big")
            assert ours[-2:] == expected, (start, quantity)

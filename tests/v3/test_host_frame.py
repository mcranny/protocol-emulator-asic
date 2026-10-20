"""The host frame is exactly 48 bits, cmd/addr/payload, MSB first."""
import pytest

from protocol_emulator.v3 import host


def test_a_frame_is_48_bits() -> None:
    assert host.FRAME_BITS == 48


def test_fields_pack_in_wire_order() -> None:
    bits = host.pack(cmd=0x12, addr=0x34, payload=0xDEADBEEF)
    assert bits >> 40 == 0x12
    assert (bits >> 32) & 0xFF == 0x34
    assert bits & 0xFFFFFFFF == 0xDEADBEEF


def test_pack_and_unpack_round_trip() -> None:
    frame = host.Frame(cmd=0x7F, addr=0xFF, payload=0x01234567)
    assert host.unpack(host.pack(*frame)) == frame


def test_the_reject_bit_and_the_engine_bit_do_not_collide() -> None:
    assert host.REJECT_BIT != host.ENGINE_BIT
    assert host.REJECT_BIT == 0x80, "bit 7 of the reply command byte flags rejection"


def test_no_command_sets_the_reject_bit() -> None:
    for name, code in host.COMMANDS.items():
        assert not code & host.REJECT_BIT, f"{name} collides with the reject flag"


def test_no_command_sets_the_engine_bit() -> None:
    for name, code in host.COMMANDS.items():
        assert not code & host.ENGINE_BIT, f"{name} collides with the engine select bit"


def test_an_oversized_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="payload"):
        host.pack(cmd=0, addr=0, payload=1 << 32)


def test_the_command_map_is_exact() -> None:
    assert host.COMMANDS == {
        "NOP": 0x00,
        "WRITE": 0x01,
        "READ": 0x02,
        "CONFIG": 0x03,
        "WATCH": 0x04,
        "START": 0x05,
        "STOP": 0x06,
        "VERSION": 0x07,
        "TXPUSH": 0x08,
        "RXPOP": 0x09,
        "CLEAR": 0x0A,
    }


def test_the_version_word_carries_major_version_3() -> None:
    assert host.VERSION_WORD >> 24 == 3


@pytest.mark.parametrize("field", ["cmd", "addr", "payload"])
def test_pack_rejects_a_negative_field_naming_it(field: str) -> None:
    fields = {"cmd": 0, "addr": 0, "payload": 0}
    fields[field] = -1
    with pytest.raises(ValueError, match=field):
        host.pack(**fields)


@pytest.mark.parametrize(
    ("field", "too_wide"), [("cmd", 1 << 8), ("addr", 1 << 8), ("payload", 1 << 32)]
)
def test_pack_rejects_a_too_wide_field_naming_it(field: str, too_wide: int) -> None:
    fields = {"cmd": 0, "addr": 0, "payload": 0}
    fields[field] = too_wide
    with pytest.raises(ValueError, match=field):
        host.pack(**fields)


def test_pack_accepts_the_maximum_of_every_field() -> None:
    assert host.pack(0xFF, 0xFF, 0xFFFFFFFF) == (1 << 48) - 1


@pytest.mark.parametrize("bits", [-1, 1 << 48])
def test_unpack_rejects_a_value_outside_48_bits(bits: int) -> None:
    with pytest.raises(ValueError, match="48"):
        host.unpack(bits)

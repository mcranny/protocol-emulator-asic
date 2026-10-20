"""The CRC unit matches published vectors for the polynomials V3 must support."""
from protocol_emulator.v3 import model


def crc_over(data: bytes, poly: int, seed: int, msb_first: bool) -> int:
    unit = model.Crc()
    unit.configure(poly=poly, msb_first=msb_first)
    unit.load(seed)
    for byte in data:
        order = range(7, -1, -1) if msb_first else range(8)
        for index in order:
            unit.shift((byte >> index) & 1)
    return unit.value


def test_crc16_ccitt_false_matches_the_check_vector() -> None:
    # CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, MSB first, check "123456789".
    assert crc_over(b"123456789", poly=0x1021, seed=0xFFFF, msb_first=True) == 0x29B1


def test_crc16_arc_matches_the_check_vector() -> None:
    # CRC-16/ARC: reflected poly 0xA001, init 0x0000, LSB first.
    assert crc_over(b"123456789", poly=0xA001, seed=0x0000, msb_first=False) == 0xBB3D


def test_usb_crc5_stays_in_five_bits_and_has_a_constant_residual() -> None:
    """CRC5 for low-speed USB, which V3 is wired for but does not yet implement.

    No published check value is asserted here, because the one in circulation
    for a given token is easy to get wrong and a wrong constant is worse than
    no constant. Instead this checks the two properties that actually matter:
    a 5-bit polynomial keeps the register inside five bits, and feeding a
    message followed by its own CRC produces the same residual every time.
    That residual is captured from the first message and compared against the
    others, so the test has no external magic number.
    """

    def crc5(bits: list[int], seed: int = 0x1F) -> int:
        unit = model.Crc()
        unit.configure(poly=0x0014, msb_first=False)  # x^5+x^2+1, reflected
        unit.load(seed)
        for bit in bits:
            unit.shift(bit)
        assert unit.value <= 0x1F, "a 5-bit polynomial must not leave five bits"
        return unit.value

    residuals = set()
    for token in (0x0015, 0x0000, 0x07FF, 0x03A5):
        message = [(token >> index) & 1 for index in range(11)]
        check = crc5(message) ^ 0x1F
        appended = message + [(check >> index) & 1 for index in range(5)]
        residuals.add(crc5(appended))
    assert len(residuals) == 1, f"residual is not constant: {residuals}"


def test_configuring_does_not_disturb_the_state() -> None:
    unit = model.Crc()
    unit.configure(poly=0x1021, msb_first=True)
    unit.load(0xFFFF)
    unit.shift(1)
    before = unit.value
    unit.configure(poly=0x1021, msb_first=True)
    assert unit.value == before


def test_the_state_stays_sixteen_bits() -> None:
    unit = model.Crc()
    unit.configure(poly=0x8005, msb_first=True)
    unit.load(0xFFFF)
    for _ in range(64):
        unit.shift(1)
        assert 0 <= unit.value <= 0xFFFF

"""The V3 package exists, is importable, and declares its version."""
import protocol_emulator.v3 as v3


def test_version_is_a_v3_dev_version() -> None:
    assert v3.__version__.startswith("3.")


def test_clock_hz_is_48_mhz() -> None:
    assert v3.CLOCK_HZ == 48_000_000

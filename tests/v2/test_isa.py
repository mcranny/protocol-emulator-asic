"""Independent V2 isa behavior checks."""

import pytest
from protocol_emulator.isa import encode as v1_encode
from protocol_emulator.v2.isa import assemble, decode, encode


def test_v1_encoding_compatibility():
    for name, operands in [
        ("NOP", ()),
        ("SET", (255, 85)),
        ("OE", (255, 255)),
        ("LI", (1, 65535)),
        ("WAIT", (7, 1, 65535)),
        ("DJNZ", (1, 63)),
        ("JMP", (63,)),
        ("HALT", ()),
    ]:
        assert encode(name, *operands) == v1_encode(name, *operands)
    assert assemble("JMP last\n" + "NOP\n" * 126 + "last: HALT")[-1] == 0xC00000
    with pytest.raises(ValueError):
        assemble("JMP 64", capacity=64)
    with pytest.raises(ValueError):
        decode(0xDF8000)
    with pytest.raises(ValueError):
        decode(0xD20001)

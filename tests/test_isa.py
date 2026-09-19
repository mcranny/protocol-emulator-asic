import random
import pytest
from protocol_emulator.isa import assemble, decode, disassemble, encode, image_bytes, image_words
from protocol_emulator.firmware import uart_program

VECTORS = [
    ("NOP", (), 0), ("SET", (0x81, 0x80), 0x108180),
    ("OE", (255, 1), 0x20FF01), ("LI", (1, 0xBEEF), 0x31BEEF),
    ("PULL", (1,), 0x410000), ("MOVOS", (0,), 0x500000),
    ("OUT", (7, 1), 0x60000F), ("SAMPLE", (1, 255), 0x7100FF),
    ("DELAY", (217,), 0x8000D9), ("WAIT", (3, 1, 25), 0x90019B),
    ("DJNZ", (1, 63), 0xA1003F), ("JMP", (63,), 0xB0003F), ("HALT", (), 0xC00000)
]

@pytest.mark.parametrize("name,args,word", VECTORS)
def test_hand_encoded(name, args, word):
    assert encode(name, *args) == word
    assert decode(word).operands == args
    assert assemble(disassemble([word])) == [word]

@pytest.mark.parametrize("word", [0xD00000, 0xE00000, 0xF00000, 1, 0x110000,
    0x210000, 0x320000, 0x400001, 0x500001, 0x600010, 0x700100, 0x810000,
    0x800000, 0x90000F, 0xA00040, 0xB00040, 0xC00001, -1, 0x1000000])
def test_invalid_encodings(word):
    with pytest.raises(ValueError):
        decode(word)

@pytest.mark.parametrize("source", ["", "BAD", "NOP 1", "OUT 8, 0", "LI 2, 0",
    "DELAY 0", "WAIT 0, 0, 0", "JMP 64", "JMP nowhere", "a: NOP\na: HALT",
    "SET 1", "NOP\n" * 65])
def test_invalid_source(source):
    with pytest.raises(ValueError):
        assemble(source)

def test_roundtrip_random_legal_words():
    rng = random.Random(8712)
    masks = [0, 65535, 65535, 131071, 65536, 65536, 15, 65791, 65535, 1048575, 65599, 63, 0]
    for op, mask in enumerate(masks):
        for _ in range(200):
            word = op << 20 | rng.getrandbits(20) & mask
            if op == 8 and not word & 65535 or op == 9 and not (word & 1048575) >> 4:
                continue
            assert assemble(disassemble([word])) == [word]

def test_images_and_labels():
    words = assemble("start: LI 0, 3\nloop: DJNZ 0, loop\nJMP start")
    assert words == [0x300003, 0xA00001, 0xB00000]
    assert image_words(image_bytes(words)) == words
    for data in (b"", b"x", bytes(195), bytes.fromhex("f00000")):
        with pytest.raises(ValueError):
            image_words(data)

@pytest.mark.parametrize("period", [25, 217])
def test_uart_fits(period):
    assert len(uart_program(period)) == 25

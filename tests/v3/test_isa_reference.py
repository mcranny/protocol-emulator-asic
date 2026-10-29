"""The generated instruction reference stays in step with the opcode table."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import v3_isa_reference  # noqa: E402

from protocol_emulator.v3 import opcodes  # noqa: E402


def test_the_committed_page_matches_the_table() -> None:
    assert v3_isa_reference.PAGE.read_text(encoding="utf-8") == v3_isa_reference.render(), (
        "docs/isa.md is stale. Regenerate with: python tools/v3_isa_reference.py"
    )


def test_every_opcode_condition_and_polarity_is_listed() -> None:
    text = v3_isa_reference.render()
    for op in opcodes.OPS.values():
        assert f"`{op.name}`" in text
        assert f"`0x{op.code:02X}`" in text
    for name in opcodes.CONDITIONS:
        assert f"`{name}`" in text
    for name in opcodes.POLARITIES:
        assert f"`{name}`" in text

"""The assembler produces a full 256-word image with resolved labels."""
import pytest

from protocol_emulator.v3 import PROGRAM_WORDS, isa


def test_an_empty_program_is_all_halt() -> None:
    image = isa.assemble("")
    assert len(image.words) == PROGRAM_WORDS
    assert set(image.words) == {isa.encode("HALT")}
    assert image.used == 0


def test_instructions_land_in_order_and_the_rest_is_halt() -> None:
    image = isa.assemble("NOP\nHALT\n")
    assert image.words[0] == isa.encode("NOP")
    assert image.words[1] == isa.encode("HALT")
    assert image.used == 2
    assert image.words[2] == isa.encode("HALT")


def test_comments_and_blank_lines_are_ignored() -> None:
    image = isa.assemble("# leading comment\n\nNOP   # trailing\n")
    assert image.used == 1


def test_a_backward_label_resolves() -> None:
    image = isa.assemble("top:\nNOP\nJMP target=top\n")
    assert isa.decode(image.words[1]).operands["target"] == 0


def test_a_forward_label_resolves() -> None:
    image = isa.assemble("JMP target=end\nNOP\nend:\nHALT\n")
    assert isa.decode(image.words[0]).operands["target"] == 2


def test_symbolic_conditions_resolve() -> None:
    image = isa.assemble("BRC target=0 condition=timeout\n")
    assert isa.decode(image.words[0]).operands["condition"] == 0


def test_symbolic_polarities_resolve() -> None:
    image = isa.assemble("WAITEDGE pin=3 polarity=falling\n")
    assert isa.decode(image.words[0]).operands["polarity"] == 1


def test_hex_and_binary_literals_resolve() -> None:
    image = isa.assemble("LI reg=0 value=0xFF\nLI reg=1 value=0b1010\n")
    assert isa.decode(image.words[0]).operands["value"] == 0xFF
    assert isa.decode(image.words[1]).operands["value"] == 0b1010


def test_operand_order_does_not_matter() -> None:
    a = isa.assemble("OUT offset=5 level=1 pin=2\n")
    b = isa.assemble("OUT pin=2 level=1 offset=5\n")
    assert a.words[0] == b.words[0]


def test_org_moves_the_assembly_address() -> None:
    image = isa.assemble(".org 10\nNOP\n")
    assert image.words[10] == isa.encode("NOP")
    assert image.words[0] == isa.encode("HALT")


def test_a_program_that_does_not_fit_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match=r"line 257.*does not fit"):
        isa.assemble("NOP\n" * (PROGRAM_WORDS + 1))


def test_a_full_program_of_256_instructions_assembles() -> None:
    image = isa.assemble("NOP\n" * PROGRAM_WORDS)
    assert image.used == PROGRAM_WORDS


def test_org_past_the_end_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match=r"line 3.*does not fit"):
        isa.assemble(".org 255\nNOP\nNOP\n")


def test_a_duplicate_label_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match="duplicate label"):
        isa.assemble("a:\nNOP\na:\nNOP\n")


def test_an_undefined_label_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match="undefined"):
        isa.assemble("JMP target=nowhere\n")


def test_an_error_reports_the_line_number() -> None:
    with pytest.raises(isa.AssemblyError, match="line 3"):
        isa.assemble("NOP\nNOP\nBOGUS\n")


def test_an_unknown_instruction_with_operands_reports_the_line() -> None:
    with pytest.raises(isa.AssemblyError, match="line 1"):
        isa.assemble("BOGUS name=1\n")


def test_two_instructions_cannot_occupy_the_same_address() -> None:
    with pytest.raises(isa.AssemblyError, match="already occupied"):
        isa.assemble("NOP\n.org 0\nNOP\n")


def test_an_unknown_operand_is_rejected_with_a_line_number() -> None:
    with pytest.raises(isa.AssemblyError, match=r"line 1.*name"):
        isa.assemble("NOP name=1\n")


def test_a_duplicate_operand_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match=r"line 1.*duplicate operand 'pin'"):
        isa.assemble("OUT pin=1 pin=2 level=1 offset=0\n")


@pytest.mark.parametrize(
    "source",
    ["5:", ":", "my label:", "timeout:"],
)
def test_invalid_labels_are_rejected(source: str) -> None:
    with pytest.raises(isa.AssemblyError, match="invalid label"):
        isa.assemble(f"{source}\nNOP\n")


def test_a_valid_label_with_digits_and_underscore_is_accepted() -> None:
    image = isa.assemble("loop_1:\nNOP\n")
    assert image.labels == {"loop_1": 0}


def test_comma_separated_operands_match_space_separated() -> None:
    spaced = isa.assemble("OUT pin=2 level=1 offset=5\n")
    comma = isa.assemble("OUT pin=2, level=1, offset=5\n")
    assert spaced.words[0] == comma.words[0]

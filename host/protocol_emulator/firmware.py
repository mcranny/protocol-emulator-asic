"""Protocol programs assembled for the generic engine."""
from .isa import assemble


def uart_source(bit_cycles: int = 25) -> str:
    if not isinstance(bit_cycles, int) or not 5 <= bit_cycles <= 65536:
        raise ValueError("UART bit interval must be 5..65536 cycles")
    lines = ["SET 1, 1", "OE 1, 1", "next: PULL 0", "MOVOS 0", "SET 1, 0",
             f"DELAY {bit_cycles - 1}"]
    for _ in range(8):
        lines += ["OUT 0, 0", f"DELAY {bit_cycles - 1}"]
    # After the stop edge: delay, JMP, PULL, MOVOS, SET (next start).
    lines += ["SET 1, 1", f"DELAY {bit_cycles - 4}", "JMP next"]
    return "\n".join(lines) + "\n"


def uart_program(bit_cycles: int = 25) -> list[int]:
    return assemble(uart_source(bit_cycles))

"""V3 protocol emulator: time-cursor architecture, SRAM program memory, 48 MHz.

V3 is a clean break from V1 and V2. Nothing in this package is encoding- or
cycle-compatible with the earlier designs.
"""

__version__ = "3.0.0.dev0"

#: System clock. Every timing constant in :mod:`protocol_emulator.v3.timing`
#: derives from this value.
CLOCK_HZ = 48_000_000

#: Program memory depth per engine, in 32-bit words.
PROGRAM_WORDS = 256

#: Width of the shared free-running timebase counter.
TIMEBASE_BITS = 24

#: Number of protocol pins.
PIN_COUNT = 8

__all__ = ["CLOCK_HZ", "PROGRAM_WORDS", "TIMEBASE_BITS", "PIN_COUNT", "__version__"]

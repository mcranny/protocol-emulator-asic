"""Firmware program generators for the V3 engines.

Each generator returns assembly source for one protocol, parameterised by pin
and rate. The source is assembled with
:func:`protocol_emulator.v3.isa.assemble` and loaded into an engine. All timing
is taken from :mod:`protocol_emulator.v3.timing`, so a generator either produces
a program correct for the requested rate or raises :class:`ValueError`.

:data:`PROGRAMS` maps a short name to its generator so a tool can enumerate the
available firmware without importing each module.
"""
from collections.abc import Callable

from .spi import spi_controller, spi_target
from .uart import uart_rx, uart_tx

#: Registry of firmware generators, keyed by a short program name. Every value
#: takes keyword or positional parameters (at least ``pin`` and a rate) and
#: returns assembly source.
PROGRAMS: dict[str, Callable[..., str]] = {
    "uart_tx": uart_tx,
    "uart_rx": uart_rx,
    "spi_controller": spi_controller,
    "spi_target": spi_target,
}

__all__ = ["PROGRAMS", "uart_tx", "uart_rx", "spi_controller", "spi_target"]

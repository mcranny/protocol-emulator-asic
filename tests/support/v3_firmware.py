"""Composed datapath for firmware model tests.

:class:`FirmwareBus` wires the published V3 model blocks together the way the
top level does, so a firmware program can be exercised against the same timing
the silicon sees:

* a :class:`~protocol_emulator.v3.model.Timebase`,
* eight shared :class:`~protocol_emulator.v3.model.PinCell` instances,
* the RTL input path -- an eight-wide
  :class:`~protocol_emulator.v3.model.InputFilter` feeding a
  :class:`~protocol_emulator.v3.model.Synchronizer` (the pin cell's two-flop
  synchroniser), reproducing the real pad-to-engine latency,
* one or two :class:`~protocol_emulator.v3.model.Engine` instances with their
  ownership and open-drain masks, their queues, and a shared
  :class:`~protocol_emulator.v3.model.Crc` with engine 0 given priority.

Each pin resolves a bus level every cycle from the engine drive, an optional
external stimulus, and a pull level; the resolution supports push-pull lines
and open-drain wired-AND lines with a pull-up. The resolved level is recorded
as ``(tick, level)`` change points for the protocol peers and fed back through
the input path so the engine samples exactly what is on the wire (its own
output on a wired-AND bus included).

The module is the shared harness for the firmware model tests (UART, and the
SPI, I2C and WS2812 firmware that build on it). It lives under ``tests`` rather
than the shipped package because it exists only to drive tests.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from protocol_emulator.v3 import PIN_COUNT, isa, model

#: An external pin stimulus: the level a pin is driven to at a tick, or ``None``
#: when the external driver is not driving (high impedance).
Stimulus = Callable[[int], "int | None"]


def level_source(changes: Sequence[tuple[int, int]]) -> Stimulus:
    """Turn ``(tick, level)`` change points into a level-at-tick function.

    The level holds from each change point until the next; before the first it
    holds the first point's level. Change points must be in non-decreasing tick
    order.

    :param changes: the waveform as change points.
    :returns: a function from tick to level.
    :raises ValueError: if ``changes`` is empty.
    """
    if not changes:
        raise ValueError("a level source needs at least one change point")
    ordered = list(changes)

    def at(tick: int) -> int:
        level = ordered[0][1]
        for change_tick, change_level in ordered:
            if change_tick > tick:
                break
            level = change_level
        return level

    return at


class EngineSpec:
    """One engine to place on the bus.

    :param source: assembly source to assemble and load.
    :param owner: 8-bit mask of the pins the engine may drive.
    :param open_drain: 8-bit open-drain mask for the engine's pins.
    """

    def __init__(self, source: str, owner: int, open_drain: int = 0) -> None:
        self.image = isa.assemble(source)
        self.owner = owner & 0xFF
        self.open_drain = open_drain & 0xFF


class FirmwareBus:
    """Compose the V3 datapath and run firmware against it.

    :param specs: one or two :class:`EngineSpec` instances. With two engines,
        engine 0 has CRC priority and is stepped first each cycle.
    :param start_now: the timebase value the engines start at.
    :param pull: 8-bit mask of the per-pin bus pull level used when no driver is
        active (bit set means the line idles high). Defaults to all high.
    :param wired_and: 8-bit mask of pins resolved as an open-drain wired-AND bus
        (any low driver pulls the line low, otherwise the pull level holds).
    """

    def __init__(
        self,
        specs: Sequence[EngineSpec],
        *,
        start_now: int = 0,
        pull: int = 0xFF,
        wired_and: int = 0,
    ) -> None:
        if not 1 <= len(specs) <= 2:
            raise ValueError("FirmwareBus takes one or two engines")
        self.base = model.Timebase()
        self.base.now = start_now & model.TIMEBASE_MASK
        self._start_now = self.base.now
        self.pull = pull & 0xFF
        self.wired_and = wired_and & 0xFF

        self.pins = [
            model.PinCell(open_drain=bool((self._combined_open_drain(specs) >> i) & 1))
            for i in range(PIN_COUNT)
        ]
        self.crc = model.Crc()
        self.engines: list[model.Engine] = []
        for spec in specs:
            engine = model.Engine(
                pins=self.pins,
                owner=spec.owner,
                open_drain=spec.open_drain,
                crc=self.crc,
            )
            engine.load(spec.image)
            self.engines.append(engine)

        self._filter = model.InputFilter()
        self._sync = model.Synchronizer()
        self._stimuli: list[Stimulus | None] = [None] * PIN_COUNT
        self._waves: list[list[tuple[int, int]]] = [[] for _ in range(PIN_COUNT)]
        self._last: list[int | None] = [None] * PIN_COUNT
        self._started = False

    @staticmethod
    def _combined_open_drain(specs: Sequence[EngineSpec]) -> int:
        mask = 0
        for spec in specs:
            mask |= spec.open_drain
        return mask

    def set_stimulus(self, pin: int, stimulus: Stimulus | None) -> None:
        """Set (or clear) the external driver for ``pin``.

        :param pin: the pin to drive externally.
        :param stimulus: a level-at-tick function, or ``None`` to leave the pin
            externally undriven.
        """
        if not 0 <= pin < PIN_COUNT:
            raise ValueError(f"pin must be 0..{PIN_COUNT - 1}, got {pin}")
        self._stimuli[pin] = stimulus

    def _resolve(self, pin: int, now: int) -> int:
        """Resolve the bus level of ``pin`` this cycle."""
        value, oe = self.pins[pin].drive()
        external = self._stimuli[pin](now) if self._stimuli[pin] is not None else None
        pull = (self.pull >> pin) & 1
        if (self.wired_and >> pin) & 1:
            level = pull
            if oe and value == 0:
                level = 0
            if external == 0:
                level = 0
            return level
        if oe:
            return value & 1
        if external is not None:
            return external & 1
        return pull

    def _record(self, now: int) -> None:
        for pin in range(PIN_COUNT):
            level = self._resolve(pin, now)
            if self._last[pin] != level:
                self._waves[pin].append((now, level))
                self._last[pin] = level

    def _start(self) -> None:
        for engine in self.engines:
            engine.start(self._start_now)
        # Seed every waveform with the idle level the pads hold before the first
        # commit, so a decoder sees the opening idle sample.
        for pin in range(PIN_COUNT):
            level = self._resolve(pin, self._start_now)
            self._waves[pin].append((self._start_now, level))
            self._last[pin] = level
        self._started = True

    def run(self, cycles: int) -> "FirmwareBus":
        """Run ``cycles`` clocks, recording pad changes and driving stimulus.

        Starts the engines on the first call. Each cycle resolves every pin's
        input bus level, feeds it through the input filter then the pin cell
        synchroniser, steps the engines, ticks the pin cells, then records any
        pad change.

        :param cycles: number of clocks to advance.
        :returns: ``self`` for chaining.
        """
        if not self._started:
            self._start()
        for _ in range(cycles):
            now = self.base.now
            bus_in = 0
            for pin in range(PIN_COUNT):
                bus_in |= (self._resolve(pin, now) & 1) << pin
            synced = self._sync.tick(self._filter.tick(bus_in))
            for pin in range(PIN_COUNT):
                self.pins[pin].observe((synced >> pin) & 1, now)

            self.engines[0].step(now)
            if len(self.engines) == 2:
                self.engines[1].step(now, crc_grant=not self.engines[0].crc_used)
            for cell in self.pins:
                cell.tick(now)

            self._record(now)
            self.base.tick()
        return self

    def waveform(self, pin: int) -> list[tuple[int, int]]:
        """Return the recorded ``(tick, level)`` change points for ``pin``."""
        return list(self._waves[pin])

    def any_missed(self) -> bool:
        """Return whether any pin cell has its sticky missed-deadline flag set."""
        return any(cell.missed for cell in self.pins)

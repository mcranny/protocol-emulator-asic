"""I2C firmware against the model and the independent I2C peers.

The controller program is exercised on the composed datapath with a software
I2C target (:class:`SoftTarget`) pulling SDA low to acknowledge and driving read
data; the resolved open-drain bus is decoded with
:class:`~protocol_emulator.v3.peers.I2cTarget`. The target program is exercised
with a software controller (:class:`SoftController`) that generates the clock
and honours clock stretching. SDA and SCL are a wired-AND open-drain pair.

``FirmwareBus`` models one engine driver plus an external stimulus per wire, not
two engine drivers, so the controller and target firmware cannot share the bus
in one model; each is driven by a software counterpart here. The full-chip
cocotb test pairs the controller firmware with the independent ``I2cTarget``
peer on the real RTL.
"""
from pathlib import Path

import pytest

from protocol_emulator.v3 import PROGRAM_WORDS, timing
from protocol_emulator.v3.firmware import i2c_bus_clear, i2c_controller, i2c_target
from protocol_emulator.v3.firmware.i2c import (
    OP_READ,
    OP_READ_NACK,
    OP_REPEATED_START,
    OP_START,
    OP_STOP,
    OP_WRITE,
    RX_META_ACK,
    RX_META_ARB_LOST,
    RX_META_LAST,
    RX_META_NACK,
    RX_META_READ,
    RX_META_WRITTEN,
    scan_high_drives,
)
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.peers import I2C_MINIMUM_CYCLES, I2cBus, I2cTarget
from tests.support.v3_firmware import EngineSpec, FirmwareBus

SDA = 1
SCL = 2
BOTH = (1 << SDA) | (1 << SCL)
EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "v3"


def _address_byte(address: int, read: bool) -> int:
    """Return the on-wire address/direction byte."""
    return (address << 1) | (1 if read else 0)


def _controller_bus(rate: int) -> FirmwareBus:
    """A composed datapath with the controller firmware and an open-drain bus."""
    return FirmwareBus(
        [
            EngineSpec(
                i2c_controller(SDA, SCL, rate),
                owner=BOTH,
                open_drain=BOTH,
            )
        ],
        pull=0xFF,
        wired_and=BOTH,
    )


def _target_bus(rate: int, address: int) -> FirmwareBus:
    """A composed datapath with the target firmware and an open-drain bus."""
    return FirmwareBus(
        [
            EngineSpec(
                i2c_target(SDA, SCL, rate, address),
                owner=BOTH,
                open_drain=BOTH,
            )
        ],
        pull=0xFF,
        wired_and=BOTH,
    )


def _level_at(wave, tick):
    level = wave[0][1]
    for change_tick, change_level in wave:
        if change_tick > tick:
            break
        level = change_level
    return level


def _bus_samples(bus: FirmwareBus) -> list[tuple[int, I2cBus]]:
    """Merge the recorded SCL and SDA waveforms into I2cBus change points."""
    scl_wave = bus.waveform(SCL)
    sda_wave = bus.waveform(SDA)
    times = sorted({t for t, _ in scl_wave} | {t for t, _ in sda_wave})
    return [(t, I2cBus(_level_at(scl_wave, t), _level_at(sda_wave, t))) for t in times]


def _pop_all(bus: FirmwareBus) -> list[tuple[int, int]]:
    records = []
    while True:
        record = bus.engines[0].queues.pop_rx()
        if record is None:
            return records
        records.append(record)


def _scl_pulses(bus: FirmwareBus) -> tuple[list[int], list[int]]:
    """Return the measured SCL low and high pulse widths, excluding the opening
    idle/START high and the closing level."""
    wave = bus.waveform(SCL)
    lows: list[int] = []
    highs: list[int] = []
    seen_fall = False
    for (t0, l0), (t1, _l1) in zip(wave, wave[1:]):
        if l0 == 0:
            lows.append(t1 - t0)
            seen_fall = True
        elif seen_fall:
            highs.append(t1 - t0)
    return lows, highs


class SoftTarget:
    """A software I2C target reacting to the controller firmware on the bus.

    Installed as the SDA (and optionally SCL) external stimulus, it reads the
    controller's driven levels each cycle, tracks SCL edges from a START, pulls
    SDA low to acknowledge, drives read data, and can hold SCL low to stretch
    the clock. The stimulus callback is invoked more than once per tick, so all
    state advances only when the tick number changes.
    """

    def __init__(
        self,
        bus: FirmwareBus,
        *,
        ack_address: bool = True,
        write_ack: bool = True,
        read_data: bytes = b"",
        stretch_low: int = 0,
    ) -> None:
        self.bus = bus
        self.ack_address = ack_address
        self.write_ack = write_ack
        self.read_data = read_data
        self.stretch_low = stretch_low
        self.received = bytearray()
        self._prev_scl = 1
        self._prev_sda = 1
        self._started = False
        self._bit = 0
        self._byte = 0
        self._direction = 0
        self._addr = 0
        self._shift = 0
        self._drive_sda_low = False
        self._stretch_until = -1
        self._read_bits: list[int] = []
        self._last_now = -1

    def _ctrl_scl(self) -> int:
        _v, oe = self.bus.pins[SCL].drive()
        return 0 if oe else 1

    def _ctrl_sda(self) -> int:
        _v, oe = self.bus.pins[SDA].drive()
        return 0 if oe else 1

    def _begin_read_byte(self) -> None:
        index = self._byte - 1
        value = self.read_data[index] if index < len(self.read_data) else 0xFF
        self._read_bits = [(value >> (7 - i)) & 1 for i in range(8)]

    def _advance(self, now: int) -> None:
        scl = self._ctrl_scl()
        sda = self._ctrl_sda()
        if scl == 1 and self._prev_scl == 1 and sda == 0 and self._prev_sda == 1:
            self._started = True
            self._bit = 0
            self._byte = 0
            self._direction = 0
            self._shift = 0
            self._drive_sda_low = False
        rising = self._prev_scl == 0 and scl == 1
        falling = self._prev_scl == 1 and scl == 0
        if self._started and rising:
            self._bit += 1
            if self._bit <= 8:
                self._shift = ((self._shift << 1) | sda) & 0xFF
        if self._started and falling:
            if self._bit == 8:
                if self._byte == 0:
                    self._addr = self._shift
                    self._direction = self._shift & 1
                    self._drive_sda_low = self.ack_address and (self._shift >> 1) == self._address
                    if self.stretch_low:
                        self._stretch_until = now + self.stretch_low
                elif self._direction == 0:
                    self.received.append(self._shift)
                    self._drive_sda_low = self.write_ack
                else:
                    self._drive_sda_low = False
            elif self._bit == 9:
                self._bit = 0
                self._byte += 1
                self._shift = 0
                if self._direction == 1 and self._byte >= 1:
                    self._begin_read_byte()
                    self._drive_sda_low = self._read_bits.pop(0) == 0
                else:
                    self._drive_sda_low = False
            elif self._direction == 1 and self._byte >= 1 and 1 <= self._bit <= 7:
                self._drive_sda_low = self._read_bits.pop(0) == 0 if self._read_bits else False
        self._prev_scl = scl
        self._prev_sda = sda

    #: set by the owning test before install
    _address = 0

    def __call__(self, now: int) -> "int | None":
        if now != self._last_now:
            self._advance(now)
            self._last_now = now
        if 0 <= self._stretch_until and now < self._stretch_until:
            # Hold SCL low (handled by the SCL stimulus); SDA follows drive flag.
            pass
        return 0 if self._drive_sda_low else None

    def scl_stimulus(self, now: int) -> "int | None":
        """SCL external stimulus that realises the clock stretch."""
        if 0 <= self._stretch_until and now < self._stretch_until:
            return 0
        return None


def _install_target(bus: FirmwareBus, target: SoftTarget, address: int) -> None:
    target._address = address
    bus.set_stimulus(SDA, target)
    if target.stretch_low:
        bus.set_stimulus(SCL, target.scl_stimulus)


def _run_controller(rate: int, ops: bytes, target: SoftTarget, address: int):
    low, high = timing.I2C[rate]
    bus = target.bus
    bus.engines[0].queues.push_tx(ops)
    _install_target(bus, target, address)
    cycles = (len(ops) * 10 + 20) * (low + high) + 4000
    bus.run(cycles)
    return bus


# -- controller: assembly, fit, no high drive ---------------------------


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_controller_fits_program_memory(rate: int) -> None:
    image = assemble(i2c_controller(SDA, SCL, rate))
    assert len(image.words) == PROGRAM_WORDS
    assert image.used < PROGRAM_WORDS


@pytest.mark.parametrize("rate", [100_000, 400_000])
@pytest.mark.parametrize(
    "generator",
    [
        lambda r: i2c_controller(SDA, SCL, r),
        lambda r: i2c_target(SDA, SCL, r, 0x55),
        lambda r: i2c_bus_clear(SDA, SCL, r),
    ],
)
def test_no_high_drive_on_sda_or_scl(rate: int, generator) -> None:
    image = assemble(generator(rate))
    assert scan_high_drives(image, SDA, SCL) == []


# -- controller: write, read, ack, nack, repeated start, stop -----------


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_write_transaction_acked(rate: int) -> None:
    address = 0x50
    ops = bytes([OP_START, _address_byte(address, False), OP_WRITE, 0xAB, OP_STOP])
    target = SoftTarget(_controller_bus(rate))
    bus = _run_controller(rate, ops, target, address)
    result = I2cTarget(rate).decode(_bus_samples(bus))
    assert result.data == bytes([_address_byte(address, False), 0xAB])
    assert result.violations == []
    assert [e.kind for e in result.events] == ["start", "byte", "ack", "byte", "ack", "stop"]
    assert _pop_all(bus) == [(0, RX_META_ACK), (0, RX_META_ACK)]
    assert not bus.any_missed()


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_write_nacked(rate: int) -> None:
    address = 0x50
    ops = bytes([OP_START, _address_byte(address, False), OP_STOP])
    target = SoftTarget(_controller_bus(rate), ack_address=False)
    bus = _run_controller(rate, ops, target, address)
    result = I2cTarget(rate).decode(_bus_samples(bus))
    assert result.violations == []
    assert [e.kind for e in result.events] == ["start", "byte", "nack", "stop"]
    assert _pop_all(bus) == [(0, RX_META_NACK)]


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_read_transaction(rate: int) -> None:
    address = 0x50
    ops = bytes([OP_START, _address_byte(address, True), OP_READ, OP_READ_NACK, OP_STOP])
    target = SoftTarget(_controller_bus(rate), read_data=bytes([0x3C, 0xA5]))
    bus = _run_controller(rate, ops, target, address)
    result = I2cTarget(rate).decode(_bus_samples(bus))
    assert result.violations == []
    records = _pop_all(bus)
    assert records == [
        (0, RX_META_ACK),
        (0x3C, RX_META_READ),
        (0xA5, RX_META_READ | RX_META_LAST),
    ]


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_repeated_start(rate: int) -> None:
    address = 0x50
    ops = bytes(
        [
            OP_START,
            _address_byte(address, False),
            OP_WRITE,
            0x11,
            OP_REPEATED_START,
            _address_byte(address, True),
            OP_READ_NACK,
            OP_STOP,
        ]
    )
    target = SoftTarget(_controller_bus(rate), read_data=bytes([0x7E]))
    bus = _run_controller(rate, ops, target, address)
    result = I2cTarget(rate).decode(_bus_samples(bus))
    assert result.violations == []
    kinds = [e.kind for e in result.events]
    assert "restart" in kinds
    records = _pop_all(bus)
    assert records == [
        (0, RX_META_ACK),  # address, write direction
        (0, RX_META_ACK),  # data byte 0x11
        (0, RX_META_ACK),  # repeated-start address, read direction
        (0x7E, RX_META_READ | RX_META_LAST),
    ]


# -- controller: arbitration loss ---------------------------------------


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_arbitration_loss(rate: int) -> None:
    address = 0x50  # address/dir byte 0xA0, MSB set, so the first bit is a one
    ops = bytes([OP_START, _address_byte(address, False), OP_WRITE, 0xFF, OP_STOP])
    bus = _controller_bus(rate)
    bus.engines[0].queues.push_tx(ops)

    def aggressor(now: int):
        # Another master holds SDA low throughout, from just after the START.
        return 0 if now > 200 else None

    bus.set_stimulus(SDA, aggressor)
    low, high = timing.I2C[rate]
    bus.run(20 * (low + high) + 4000)
    records = _pop_all(bus)
    assert records, "expected at least one receive record"
    assert records[0] == (0, RX_META_ARB_LOST)


# -- controller: measured SCL timing ------------------------------------


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_scl_low_high_times_match_the_table(rate: int) -> None:
    address = 0x50
    ops = bytes([OP_START, _address_byte(address, False), OP_WRITE, 0x5A, OP_STOP])
    target = SoftTarget(_controller_bus(rate))
    bus = _run_controller(rate, ops, target, address)
    low, high = timing.I2C[rate]
    min_low, min_high = I2C_MINIMUM_CYCLES[rate]
    lows, highs = _scl_pulses(bus)
    assert lows, "no SCL low pulses measured"
    assert highs, "no SCL high pulses measured"
    assert set(lows) == {low}, f"low pulses {sorted(set(lows))} != {low}"
    assert set(highs) == {high}, f"high pulses {sorted(set(highs))} != {high}"
    assert low >= min_low and high >= min_high
    assert min(lows) >= min_low
    assert min(highs) >= min_high


# -- controller: clock stretching tolerated -----------------------------


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_controller_tolerates_clock_stretch(rate: int) -> None:
    address = 0x50
    ops = bytes([OP_START, _address_byte(address, False), OP_WRITE, 0x5A, OP_STOP])
    low, high = timing.I2C[rate]
    target = SoftTarget(_controller_bus(rate), stretch_low=low)
    bus = _run_controller(rate, ops, target, address)
    result = I2cTarget(rate).decode(_bus_samples(bus))
    assert result.violations == []
    assert result.data == bytes([_address_byte(address, False), 0x5A])
    assert _pop_all(bus) == [(0, RX_META_ACK), (0, RX_META_ACK)]
    # The stretched low phase is longer than a nominal low.
    lows, _highs = _scl_pulses(bus)
    assert max(lows) > low


# -- bus clear ----------------------------------------------------------


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_bus_clear_nine_pulses_then_stop(rate: int) -> None:
    bus = FirmwareBus(
        [EngineSpec(i2c_bus_clear(SDA, SCL, rate), owner=BOTH, open_drain=BOTH)],
        pull=0xFF,
        wired_and=BOTH,
    )
    low, high = timing.I2C[rate]
    bus.run(14 * (low + high) + 2000)
    scl_wave = bus.waveform(SCL)
    falls = sum(1 for (_t0, l0), (_t1, l1) in zip(scl_wave, scl_wave[1:]) if l0 == 1 and l1 == 0)
    assert falls == 9, f"expected nine SCL pulses, saw {falls}"
    result = I2cTarget(rate).decode(_bus_samples(bus), allow_bus_clear=True)
    assert result.violations == []
    assert result.events[-1].kind == "stop"


# -- target firmware ----------------------------------------------------


class SoftController:
    """A software I2C controller that drives the clock and honours stretching.

    Installed as the SCL and SDA external stimulus, it plays a list of phases,
    each a ``(scl, sda, duration, gate, sample)`` tuple. A gated phase that
    releases SCL waits for the line to actually rise before timing its duration,
    so a target holding SCL low stretches it. ``sample`` phases latch the
    target's SDA so read bytes can be recovered. State advances only when the
    tick number changes (the callback runs more than once per tick).
    """

    _GUARD = 32

    def __init__(self, bus: FirmwareBus, low: int, high: int) -> None:
        self.bus = bus
        self.low = low
        self.high = high
        self.phases: list[tuple[int, int, int, bool, str | None]] = []
        self.received = bytearray()
        self._i = 0
        self._scl = 1
        self._sda = 1
        self._entering = True
        self._gated = False
        self._dur_start = 0
        self._read_bits: list[int] = []
        self._last_now = -1
        self._armed = False

    # -- waveform construction -----------------------------------------

    def build_write(self, address: int, data: int) -> None:
        self._start()
        self._byte((address << 1) | 0)
        self._ack()
        self._byte(data)
        self._ack()
        self._stop()

    def build_read(self, address: int) -> None:
        self._start()
        self._byte((address << 1) | 1)
        self._ack()
        for _ in range(8):
            self.phases.append((0, 1, self.low, False, None))
            self.phases.append((1, 1, self.high, True, "read"))
        # controller NACKs the single read byte
        self.phases.append((0, 1, self.low, False, None))
        self.phases.append((1, 1, self.high, True, None))
        self._stop()

    def _start(self) -> None:
        self.phases.append((1, 1, self._GUARD, False, None))
        self.phases.append((1, 0, self.high, False, None))  # SDA low while SCL high

    def _byte(self, value: int) -> None:
        for i in range(8):
            bit = (value >> (7 - i)) & 1
            self.phases.append((0, bit, self.low, False, None))
            self.phases.append((1, bit, self.high, True, None))

    def _ack(self) -> None:
        self.phases.append((0, 1, self.low, False, None))  # release for target
        self.phases.append((1, 1, self.high, True, "ack"))

    def _stop(self) -> None:
        self.phases.append((0, 0, self.low, False, None))
        self.phases.append((1, 0, self.high, True, None))
        self.phases.append((1, 1, self.high, False, None))  # SDA high while SCL high

    # -- stimulus -------------------------------------------------------

    def _bus_scl_high(self) -> bool:
        _v, oe = self.bus.pins[SCL].drive()
        return not oe

    def _target_sda(self) -> int:
        _v, oe = self.bus.pins[SDA].drive()
        return 0 if oe else 1

    def scl(self, now: int) -> "int | None":
        self._tick(now)
        return 0 if self._scl == 0 else None

    def sda(self, now: int) -> "int | None":
        self._tick(now)
        return 0 if self._sda == 0 else None

    def _tick(self, now: int) -> None:
        if now == self._last_now:
            return
        self._last_now = now
        if not self._armed:
            self._armed = True
            self._dur_start = now
        if self._i >= len(self.phases):
            self._scl, self._sda = 1, 1
            return
        scl, sda, dur, gate, sample = self.phases[self._i]
        if self._entering:
            self._scl, self._sda = scl, sda
            self._entering = False
            self._gated = gate
            self._dur_start = now
        if self._gated:
            if self._bus_scl_high():
                self._gated = False
                self._dur_start = now
            else:
                return
        if sample is not None:
            latched = self._target_sda()
            self._read_latch = latched
        if now - self._dur_start >= dur:
            if sample == "read":
                self._read_bits.append(self._read_latch)
                if len(self._read_bits) == 8:
                    value = 0
                    for b in self._read_bits:
                        value = (value << 1) | b
                    self.received.append(value)
                    self._read_bits = []
            self._i += 1
            self._entering = True

    _read_latch = 1


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_target_receives_a_written_byte(rate: int) -> None:
    address = 0x55
    low, high = timing.I2C[rate]
    bus = _target_bus(rate, address)
    ctrl = SoftController(bus, low, high)
    ctrl.build_write(address, 0xC3)
    bus.set_stimulus(SCL, ctrl.scl)
    bus.set_stimulus(SDA, ctrl.sda)
    bus.run(60 * (low + high) + 4000)
    records = _pop_all(bus)
    assert records == [(0xC3, RX_META_WRITTEN)], f"got {records}"


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_target_transmits_a_read_byte(rate: int) -> None:
    address = 0x55
    low, high = timing.I2C[rate]
    bus = _target_bus(rate, address)
    bus.engines[0].queues.push_tx(bytes([0x9D]))
    ctrl = SoftController(bus, low, high)
    ctrl.build_read(address)
    bus.set_stimulus(SCL, ctrl.scl)
    bus.set_stimulus(SDA, ctrl.sda)
    bus.run(60 * (low + high) + 4000)
    assert bytes(ctrl.received) == bytes([0x9D]), f"got {bytes(ctrl.received).hex()}"


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_target_clock_stretches_after_address(rate: int) -> None:
    address = 0x55
    low, high = timing.I2C[rate]
    bus = _target_bus(rate, address)
    ctrl = SoftController(bus, low, high)
    ctrl.build_write(address, 0xC3)
    bus.set_stimulus(SCL, ctrl.scl)
    bus.set_stimulus(SDA, ctrl.sda)
    bus.run(60 * (low + high) + 4000)
    # The target drove SCL low at some point (the stretch), so the measured low
    # pulses include one longer than the controller's nominal low period.
    lows, _highs = _scl_pulses(bus)
    assert max(lows) > low, f"no stretch seen in {sorted(set(lows))}"
    assert _pop_all(bus) == [(0xC3, RX_META_WRITTEN)]


# -- argument validation and checked-in examples ------------------------


def test_pins_must_differ() -> None:
    with pytest.raises(ValueError, match="different"):
        i2c_controller(3, 3, 100_000)


def test_pin_out_of_range_rejected() -> None:
    with pytest.raises(ValueError, match="scl must be"):
        i2c_controller(0, 9, 100_000)


def test_unsupported_rate_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported I2C rate"):
        i2c_controller(SDA, SCL, 250_000)


def test_target_address_must_be_seven_bit() -> None:
    with pytest.raises(ValueError, match="7-bit"):
        i2c_target(SDA, SCL, 100_000, 200)


def test_checked_in_controller_example_matches_generator() -> None:
    text = (EXAMPLES / "i2c_controller_100k.asm").read_text(encoding="utf-8")
    assert text == i2c_controller(0, 1, 100_000)


def test_checked_in_target_example_matches_generator() -> None:
    text = (EXAMPLES / "i2c_target_100k.asm").read_text(encoding="utf-8")
    assert text == i2c_target(0, 1, 100_000, 0x55)


def test_checked_in_bus_clear_example_matches_generator() -> None:
    text = (EXAMPLES / "i2c_bus_clear.asm").read_text(encoding="utf-8")
    assert text == i2c_bus_clear(0, 1, 100_000)

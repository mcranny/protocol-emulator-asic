"""Open-drain peers for V2 firmware feasibility (system-cycle domain)."""
import pytest

from protocol_emulator.v2.firmware import i2c_controller, i2c_target
from protocol_emulator.v2.isa import assemble
from protocol_emulator.v2.model import Engine, Watch
from protocol_emulator.v2.peers import I2CTarget


def loaded(source):
    e = Engine()
    e.owner = e.open_drain = 3
    e.load(assemble(source))
    e.watches = (Watch(3, 2, 1), Watch(3, 3, 1))
    e.abort_mask = 3
    e.start()
    return e


class ControllerPeer:
    """Independent bit driver; respects stretching before completing SCL high."""
    def __init__(self, engine, low=32, high=29, synchronization=0):
        self.engine, self.low, self.high = engine, low, high
        self.master = 3
        self.history = []
        self.pipeline = [3] * synchronization

    def tick(self, master=None, cycles=1):
        if master is not None:
            self.master = master
        for _ in range(cycles):
            value, enable = self.engine.pins
            bus = self.master & (value | ~enable) & 3
            self.pipeline.append(bus)
            self.engine.tick(self.pipeline.pop(0))
            self.history.append(bus)
        return self.history[-1]

    def start(self):
        self.tick(3, 40)
        self.tick(2, 30)
        self.tick(0, self.low)

    def high_phase(self, sda):
        self.tick(2 | sda)
        for _ in range(2000):
            if self.history[-1] & 2:
                break
            self.tick()
        else:
            raise AssertionError("unbounded stretch")
        self.tick(cycles=self.high)
        return self.history[-1] & 1

    def write(self, value):
        for bit in range(7, -1, -1):
            sda = value >> bit & 1
            self.tick(sda, self.low)
            self.high_phase(sda)
            self.tick(sda)
        self.tick(1, self.low)
        ack = self.high_phase(1) == 0
        self.tick(1, self.low)
        return ack

    def read(self, ack=False):
        value = 0
        for _ in range(8):
            self.tick(1, self.low)
            value = value << 1 | self.high_phase(1)
            self.tick(1)
        self.tick(0 if ack else 1, self.low)
        self.high_phase(0 if ack else 1)
        self.tick(1, self.low)
        return value

    def stop(self):
        self.tick(0, self.low)
        self.high_phase(0)
        self.tick(3, 40)


@pytest.mark.parametrize("low,high", [(32, 29), (123, 125)])
@pytest.mark.parametrize("synchronization", [0, 3])
def test_target_write_and_repeated_start_read(low, high, synchronization):
    e = loaded(i2c_target())
    e.push([0xA5, 0x81])
    peer = ControllerPeer(e, low, high, synchronization)
    peer.start()
    assert peer.write(0x84)
    assert peer.write(0x55)
    assert peer.write(0x01)
    peer.start()  # repeated start without STOP
    assert peer.write(0x85)
    assert peer.read(ack=True) == 0xA5
    assert peer.read(ack=False) == 0x81
    peer.stop()
    assert list(e.rx) == [(0x55, 0), (1, 0)]
    assert not e.errors
    assert e.pins[1] == 0  # idle open-drain outputs released


def test_target_ignores_other_addresses_and_recovers_partial_frame():
    e = loaded(i2c_target())
    peer = ControllerPeer(e)
    peer.start()
    assert not peer.write(0x82)
    peer.stop()
    peer.start()
    assert peer.write(0x84)
    peer.tick(0, 40)
    peer.high_phase(0)
    peer.stop()
    peer.start()
    assert peer.write(0x84)
    assert peer.write(0xA5)
    peer.stop()
    assert list(e.rx) == [(0xA5, 0)]
    assert not e.errors


def test_controller_write_with_independent_ack_peer():
    e = loaded(i2c_controller(write_count=2))
    e.push([0x55, 0xA5])
    previous = 3
    slave_low = False
    active = False
    bits, received, starts, stops = [], [], 0, 0
    ack_cycle = False
    ack_seen = False
    for _ in range(4000):
        value, enable = e.pins
        driven = (value | ~enable) & 3
        # Change peer SDA only at SCL falling edges.
        if previous & 2 and not driven & 2:
            if ack_cycle:
                if ack_seen:
                    ack_cycle, ack_seen, slave_low = False, False, False
            elif len(bits) == 8:
                received.append(sum(bit << (7 - i) for i, bit in enumerate(bits)))
                bits = []
                ack_cycle, slave_low = True, True
        bus = driven & (2 if slave_low else 3)
        if previous & 2 and bus & 2:
            if previous & 1 and not bus & 1:
                starts += 1
                active = True
            elif not previous & 1 and bus & 1:
                stops += 1
                active = False
        if active and not previous & 2 and bus & 2:
            if ack_cycle:
                ack_seen = True
            else:
                bits.append(bus & 1)
        e.tick(bus)
        previous = bus
        if not e.running:
            break
    assert received == [0x84, 0x55, 0xA5]
    assert starts == stops == 1
    assert not e.errors


@pytest.mark.parametrize("write_count,read_count", [(2, 0), (0, 3), (2, 3)])
@pytest.mark.parametrize("synchronization", [0, 3])
def test_controller_combined_with_byte_peer(write_count, read_count, synchronization):
    e = loaded(i2c_controller(write_count=write_count, read_count=read_count))
    e.push([0x55, 0xA5][:write_count])
    peer = I2CTarget(read_data=bytes([0x81, 0x55, 0xA5]))
    pipe = [3] * synchronization
    for _ in range(7000):
        value, enable = e.pins
        bus = (value | ~enable) & ~peer.drive_low & 3
        peer.observe(bus)
        bus = (value | ~enable) & ~peer.drive_low & 3
        pipe.append(bus)
        e.tick(pipe.pop(0))
        if not e.running:
            # Observe the final STOP before asserting the peer result.
            value, enable = e.pins
            peer.observe((value | ~enable) & ~peer.drive_low & 3)
            break
    assert not e.running and not e.errors
    assert peer.received == [0x55, 0xA5][:write_count]
    assert list(e.rx) == [(value, 0) for value in [0x81, 0x55, 0xA5][:read_count]]
    assert peer.starts == (2 if write_count and read_count else 1)
    assert peer.stops == 1
    if read_count:
        assert peer.acks == [0] * (read_count - 1) + [1]

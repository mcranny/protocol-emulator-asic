"""Generate and exercise the V2 submission examples using existing firmware.

Run from an installed source checkout: python examples/v2/run.py --output DIR.
The output contains assembly, raw big-endian 24-bit images, traces and results.
This cycle-model demonstration does not exercise physical SPI host transport.
"""

import argparse
import hashlib
import json
from pathlib import Path

from protocol_emulator.v2 import firmware
from protocol_emulator.v2.isa import assemble, decode
from protocol_emulator.v2.model import Device, Engine, Watch
from protocol_emulator.v2.peers import I2CTarget, decode_spi, decode_uart


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


class Examples:
    def __init__(self, output):
        self.output = output
        output.mkdir(parents=True, exist_ok=False)
        self.results = []

    def program(self, name, source, owner=0, open_drain=0):
        words = assemble(source, capacity=64)
        decoded = "\n".join(
            i.name + (" " + ", ".join(map(str, i.operands)) if i.operands else "")
            for i in map(decode, words)
        )
        require(assemble(decoded, capacity=64) == words, f"{name}: codec round trip")
        (self.output / f"{name}.asm").write_text(source + "\n")
        binary = b"".join(w.to_bytes(3, "big") for w in words)
        (self.output / f"{name}.bin").write_bytes(binary)
        engine = Engine(capacity=64)
        engine.owner, engine.open_drain = owner, open_drain
        engine.load(words)
        engine.start()
        return engine, dict(
            program=name,
            words=len(words),
            sha256=hashlib.sha256(binary).hexdigest(),
            owner=owner,
            open_drain=open_drain,
        )

    def record(self, name, programs, trace, **checks):
        (self.output / f"{name}.trace.json").write_text(json.dumps(trace) + "\n")
        self.results.append(
            dict(name=name, programs=programs, checks=checks, passed=True)
        )

    def uart(self, baud):
        period = 217 if baud == 115200 else 25
        tx, tx_info = self.program(f"uart_tx_{baud}", firmware.uart_tx(period), 1)
        rx, rx_info = self.program(f"uart_rx_{baud}", firmware.uart_rx(period))
        device = Device(capacity=64)
        device.engines = [tx, rx]
        tx_data, rx_data = [0, 0x55, 0xA5, 255], [0x81, 0x5A, 1, 0xFE]
        tx.push(tx_data)
        pipeline, trace, previous = [2] * 3, [], 255
        cycles = 20 + 42 * period
        for cycle in range(cycles):
            position = (cycle - 20) // period
            if 0 <= position < 40:
                frame, bit = divmod(position, 10)
                level = (
                    0
                    if bit == 0
                    else 1
                    if bit == 9
                    else rx_data[frame] >> (bit - 1) & 1
                )
            else:
                level = 1
            pipeline.append(level << 1)
            device.tick(pipeline.pop(0))
            value, enable = tx.pins
            bus = (value | ~enable) & 255
            if bus != previous:
                trace.append((cycle * 40, bus))
            previous = bus
        frames = decode_uart(trace, end_ns=cycles * 40, period_ns=period * 40)
        require(
            [f["value"] for f in frames] == tx_data
            and not any(f["framing_error"] for f in frames),
            "UART TX trace",
        )
        require(list(rx.rx) == [(v, 0) for v in rx_data], "UART RX bytes")
        require(not tx.errors and not rx.errors, "UART errors")
        self.record(
            f"uart_duplex_{baud}",
            [tx_info, rx_info],
            trace,
            tx=tx_data,
            rx=rx_data,
            period_cycles=period,
            tx_frames=frames,
            service="four-byte preloaded burst; no SPI host service simulated",
        )

    def spi(self, mode, role):
        source = (
            firmware.spi_controller(mode)
            if role == "controller"
            else firmware.spi_target(mode)
        )
        e, info = self.program(
            f"spi_{role}_mode{mode}", source, 7 if role == "controller" else 8
        )
        e.push([0xA5])
        cpol, cpha = mode >> 1, mode & 1
        initial = 4 | cpol << 1 | 1
        previous, trace, pipeline = initial, [], [initial] * 3
        if role == "target":
            e.watches, e.abort_mask = (Watch(4, 4, 4),), 1
        for cycle in range(350):
            if role == "controller":
                e.tick(8)
                value, enable = e.pins
                bus = (value | ~enable) & 255
            else:
                if cycle < 10 or cycle >= 300:
                    external = initial
                elif cycle < 50 or cycle >= 250:
                    external = cpol << 1 | 1
                else:
                    edge = int((cycle - 50) / 12.5)
                    external = ((cpol ^ (1 if edge % 2 == 0 else 0)) << 1) | 1
                value, enable = e.pins
                bus = external | ((value | ~enable) & 8)
                pipeline.append(external)
                e.tick(pipeline.pop(0))
            if bus != previous:
                trace.append((cycle * 40, bus))
            previous = bus
        frames = decode_spi(
            trace, mode=mode, data_pin=0 if role == "controller" else 3, initial=initial
        )
        require(
            [f["value"] for f in frames] == [0xA5], f"SPI {role} mode {mode} wire byte"
        )
        require(list(e.rx) == [(255, 0)] and not e.errors, f"SPI {role} mode {mode} RX")
        self.record(
            f"spi_{role}_mode{mode}",
            [info],
            trace,
            wire=[0xA5],
            rx=[255],
            mode=mode,
            frequency=1_000_000,
        )

    def i2c(self, frequency, role):
        name = f"i2c_{role}_{frequency}"
        source = (
            firmware.i2c_controller(write_count=2, read_count=2, frequency=frequency)
            if role == "controller"
            else firmware.i2c_target(frequency=frequency)
        )
        e, info = self.program(name, source, 3, 3)
        if role == "controller":
            e.push([0x55, 0xA5])
            peer, pipe, trace = I2CTarget(read_data=bytes([0x81, 0x5A])), [3] * 3, []
            for cycle in range(30000):
                value, enable = e.pins
                bus = (value | ~enable) & ~peer.drive_low & 3
                peer.observe(bus)
                bus = (value | ~enable) & ~peer.drive_low & 3
                trace.append((cycle * 40, bus))
                pipe.append(bus)
                e.tick(pipe.pop(0))
                if not e.running:
                    value, enable = e.pins
                    peer.observe((value | ~enable) & ~peer.drive_low & 3)
                    break
            require(not e.running and not e.errors, "I2C controller completion")
            require(
                peer.received == [0x55, 0xA5] and list(e.rx) == [(0x81, 0), (0x5A, 0)],
                "I2C controller data",
            )
            require(
                peer.starts == 2 and peer.stops == 1 and peer.acks == [0, 1],
                "I2C controller framing",
            )
            received = [0x81, 0x5A]
        else:
            e.watches, e.abort_mask = (Watch(3, 2, 1), Watch(3, 3, 1)), 3
            e.push([0x81, 0x5A])
            peer = ControllerPeer(e, frequency)
            peer.start()
            require(
                all(peer.write(v) for v in (0x84, 0x55, 0xA5)), "I2C target write ACK"
            )
            peer.start()
            require(peer.write(0x85), "I2C target read address ACK")
            received = [peer.read(True), peer.read(False)]
            peer.stop()
            require(
                received == [0x81, 0x5A] and list(e.rx) == [(0x55, 0), (0xA5, 0)],
                "I2C target data",
            )
            require(not e.errors and e.pins[1] == 0, "I2C target release")
            trace = [(i * 40, bus) for i, bus in enumerate(peer.history)]
        self.record(
            name,
            [info],
            trace,
            address=0x42,
            writes=[0x55, 0xA5],
            reads=received,
            frequency=frequency,
            transaction="START write, repeated START read, ACK then NACK, STOP",
        )


class ControllerPeer:
    """Bit-level external controller with bounded target clock stretching."""

    def __init__(self, engine, frequency):
        self.engine = engine
        self.low, self.high = (125, 125) if frequency == 100000 else (33, 30)
        self.master, self.history, self.pipeline = 3, [], [3] * 3

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
            raise RuntimeError("I2C target exceeded bounded clock stretch")
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

    def read(self, ack):
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    examples = Examples(args.output)
    for baud in (115200, 1000000):
        examples.uart(baud)
    for mode in range(4):
        for role in ("controller", "target"):
            examples.spi(mode, role)
    for frequency in (100000, 400000):
        for role in ("controller", "target"):
            examples.i2c(frequency, role)
    (args.output / "results.json").write_text(
        json.dumps(examples.results, indent=2) + "\n"
    )
    print(
        f"{len(examples.results)} protocol scenarios passed; assembly, images and traces: {args.output}"
    )


if __name__ == "__main__":
    main()

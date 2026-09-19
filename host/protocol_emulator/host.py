"""Host interface v1 with a one-frame response pipeline."""
from typing import Protocol
from .isa import decode, encode
from .model import Model

VERSION = 0x010140


class Transport(Protocol):
    def exchange(self, frame: bytes) -> bytes:
        """Transfer exactly five bytes with one CS assertion."""


class DeviceError(RuntimeError):
    pass


class Device:
    def __init__(self, transport: Transport):
        self.transport = transport

    def request(self, cmd: int, addr: int = 0, value: int = 0) -> int:
        if not 0 <= cmd <= 127 or not 0 <= addr <= 255 or not 0 <= value <= 0xFFFFFF:
            raise ValueError("request field out of range")
        frame = bytes((cmd, addr)) + value.to_bytes(3, "big")
        self.transport.exchange(frame)
        response = self.transport.exchange(bytes(5))
        if len(response) != 5:
            raise DeviceError("short SPI response")
        if response[0] != cmd or response[1] != addr:
            raise DeviceError(f"rejected or mismatched response: {response.hex()}")
        return int.from_bytes(response[2:], "big")

    def check_version(self):
        if self.request(9) != VERSION:
            raise DeviceError("unsupported host/ISA version")

    def load(self, words: list[int]):
        if not 1 <= len(words) <= 64:
            raise ValueError("program must have 1..64 instructions")
        # Validate the entire image before writing any word.
        for word in words:
            decode(word)
        self.check_version()
        if self.status() & 1:
            raise DeviceError("stop the engine before loading")
        # Clear stale instructions from a previously longer image.
        padded = list(words) + [encode("HALT")] * (64 - len(words))
        for addr, word in enumerate(padded):
            if self.request(1, addr, word) != word:
                raise DeviceError(f"write acknowledgement mismatch at {addr}")
        self.verify(padded)

    def verify(self, words: list[int]):
        if not 1 <= len(words) <= 64:
            raise ValueError("program must have 1..64 instructions")
        for addr, word in enumerate(words):
            if self.request(2, addr) != word:
                raise DeviceError(f"readback mismatch at {addr}")

    def start(self):
        self.request(3)

    def stop(self):
        self.request(4)

    def reset(self):
        self.request(5)

    def status(self) -> int:
        return self.request(6)

    def fifo_write(self, data: bytes):
        # No hidden retries: a rejected byte is surfaced to the caller.
        for byte in data:
            self.request(7, value=byte)


class SimulationTransport:
    """Transaction-level simulation; each SPI frame advances system time.

    This transport is for software use. RTL framing/timing is independently
    exercised with pin-driven cocotb tests.
    """
    def __init__(self, model=None, cycles_per_frame=1010):
        self.model = model or Model()
        self.response = bytes(5)
        self.cycles_per_frame = cycles_per_frame

    def exchange(self, frame: bytes) -> bytes:
        if len(frame) != 5:
            raise ValueError("SPI frames contain five bytes")
        result = self.response
        for _ in range(self.cycles_per_frame - 1):
            self.model.step()
        response = self.model.step(command=(frame[0], frame[1], int.from_bytes(frame[2:], "big")))
        self.response = bytes(response[:2]) + response[2].to_bytes(3, "big")
        return result


class SpiTransport:
    """Linux spidev adapter; mode 0, <=1 MHz, CS released per exchange."""
    def __init__(self, bus=0, device=0, speed_hz=1_000_000):
        if not 1 <= speed_hz <= 1_000_000:
            raise ValueError("host SPI maximum is 1 MHz")
        try:
            import spidev
        except ImportError as exc:
            raise RuntimeError("install protocol-emulator[hardware] on the SPI host") from exc
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.mode = 0
        self.spi.max_speed_hz = speed_hz

    def exchange(self, frame: bytes) -> bytes:
        import time
        if len(frame) != 5:
            raise ValueError("SPI frames contain five bytes")
        # Linux scheduling provides a conservative inactive interval between calls.
        result = bytes(self.spi.xfer2(list(frame)))
        time.sleep(0.000001)
        return result

    def close(self):
        self.spi.close()

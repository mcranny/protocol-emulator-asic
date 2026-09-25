"""Independent edge-based peers: no assembler or engine model dependencies."""

from bisect import bisect_right


def decode_uart(changes, *, end_ns, period_ns, pin=0, initial=255):
    """Decode 8N1 from timestamped resolved pin levels at bit centers.

    False starts are ignored; a low stop sample is reported on its frame.
    A framing error requires a new falling edge after the frame before recovery.
    Incomplete frames at the observation boundary are rejected.
    """
    times = [time for time, _ in changes]
    levels = [value >> pin & 1 for _, value in changes]
    def sample(time):
        index = bisect_right(times, time) - 1
        return levels[index] if index >= 0 else initial >> pin & 1

    frames, available = [], 0
    previous = initial >> pin & 1
    for time, level in zip(times, levels):
        falling = previous and not level
        previous = level
        if not falling or time < available:
            continue
        if sample(time + period_ns / 2):
            continue
        if time + 10 * period_ns > end_ns:
            raise ValueError("incomplete UART frame")
        value = sum(sample(time + (1.5 + bit) * period_ns) << bit for bit in range(8))
        frames.append({"start_ns": time, "value": value,
                       "framing_error": not bool(sample(time + 9.5 * period_ns))})
        available = time + 10 * period_ns
    return frames


class I2CTarget:
    """Seven-bit byte peer for simulation; SDA bit 0, SCL bit 1.

    observe() returns a low-drive mask. The caller resolves wired-AND levels
    and observes again as time advances. This peer never stretches the clock.
    """
    def __init__(self, address=0x42, read_data=b"", nack_data=False):
        self.address = address
        self.read_data = bytes(read_data)
        self.nack_data = nack_data
        self.previous = 3
        self.drive_low = 0
        self.state = "idle"
        self.received = []
        self.addresses = []
        self.acks = []
        self.starts = self.stops = 0
        self.index = self.bits = self.value = 0
        self.address_phase = True
        self.next_state = "receive"
        self.accepted = False

    def observe(self, bus):
        bus &= 3
        previous, self.previous = self.previous, bus
        scl, old_scl = bool(bus & 2), bool(previous & 2)
        if scl and old_scl and bus & 1 != previous & 1:
            if bus & 1:
                self.stops += 1
                self.state = "idle"
            else:
                self.starts += 1
                self.state = "receive"
                self.address_phase = True
                self.bits = self.value = 0
            self.drive_low = 0
        elif scl and not old_scl:
            if self.state == "receive":
                self.value = self.value << 1 | (bus & 1)
                self.bits += 1
                if self.bits == 8:
                    if self.address_phase:
                        self.addresses.append(self.value)
                        self.accepted = self.value >> 1 == self.address
                        self.next_state = "transmit" if self.value & 1 else "receive"
                        self.address_phase = False
                    else:
                        self.received.append(self.value)
                        self.accepted = not self.nack_data
                        self.next_state = "receive"
                    if not self.accepted:
                        self.next_state = "ignore"
                    self.state = "ack_setup"
            elif self.state == "ack":
                self.state = "ack_done"
            elif self.state == "transmit":
                self.bits += 1
            elif self.state == "master_ack":
                self.acks.append(bus & 1)
                self.state = "continue_read" if not bus & 1 else "ignore"
        elif old_scl and not scl:
            if self.state == "ack_setup":
                self.drive_low = int(self.accepted)
                self.state = "ack"
            elif self.state == "ack_done":
                self.state = self.next_state
                self.drive_low = 0
                self.bits = self.value = 0
                if self.state == "transmit":
                    self._read_bit()
            elif self.state == "transmit":
                if self.bits == 8:
                    self.state = "master_ack"
                    self.drive_low = 0
                    self.index += 1
                else:
                    self._read_bit()
            elif self.state == "continue_read":
                self.bits = 0
                self.state = "transmit"
                self._read_bit()
        return self.drive_low

    def _read_bit(self):
        value = self.read_data[self.index] if self.index < len(self.read_data) else 255
        self.drive_low = 1 ^ (value >> (7 - self.bits) & 1)

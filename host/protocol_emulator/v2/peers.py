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


def decode_spi(changes, *, mode=0, data_pin=3, clock_pin=1, select_pin=2, initial=255):
    """Decode selected eight-bit SPI transfers directly from bus edges."""
    if mode not in range(4):
        raise ValueError("invalid SPI mode")
    previous, bits, frames, start = initial, [], [], None
    for time, bus in changes:
        selected, was_selected = not bus & (1 << select_pin), not previous & (1 << select_pin)
        if selected and not was_selected:
            bits, start = [], time
        if selected and (bus ^ previous) & (1 << clock_pin):
            leading = bool(bus & (1 << clock_pin)) != bool(mode >> 1)
            if leading != bool(mode & 1):
                bits.append(bus >> data_pin & 1)
                if len(bits) == 8:
                    frames.append({"start_ns": start, "sample_ns": time,
                                   "value": sum(bit << (7 - i) for i, bit in enumerate(bits))})
                    bits = []
        if not selected and was_selected and bits:
            raise ValueError("incomplete SPI byte")
        previous = bus
    if bits:
        raise ValueError("incomplete SPI byte")
    return frames


def decode_i2c(changes, *, initial=3):
    """Decode SDA=0/SCL=1 address/data bytes and their ninth-clock ACK."""
    previous, active, address = initial & 3, False, True
    bits, frames = [], []
    for time, bus in changes:
        bus &= 3
        if bus & 2 and previous & 2 and (bus ^ previous) & 1:
            if not bus & 1:
                active, address, bits = True, True, []
            else:
                # STOP may be prepared with a partial bit after the final ACK.
                active, bits = False, []
        elif active and bus & 2 and not previous & 2:
            bits.append(bus & 1)
            if len(bits) == 9:
                frames.append({"ack_ns": time, "address": address,
                               "value": sum(bit << (7 - i) for i, bit in enumerate(bits[:8])),
                               "ack": bits[8] == 0})
                bits, address = [], False
        previous = bus
    if active:
        raise ValueError("I2C transaction has no STOP")
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

"""Candidate firmware used to establish V2 instruction and timing budgets.

These generators use generic instructions only. They do not establish protocol
acceptance until exercised against independent asynchronous peers.
"""
from ..firmware import uart_source as v1_uart_source
from .isa import assemble


def uart_tx(period=25, pin=0, bad_stop=False):
    if not 0 <= pin <= 7:
        raise ValueError("invalid pin")
    source = v1_uart_source(period)
    source = source.replace("SET 1, 1", f"SET {1 << pin}, {1 << pin}")
    source = source.replace("OE 1, 1", f"OE {1 << pin}, {1 << pin}")
    source = source.replace("SET 1, 0", f"SET {1 << pin}, 0").replace("OUT 0, 0", f"OUT {pin}, 0")
    if bad_stop:
        lines = source.splitlines()
        lines[-3] = f"SET {1 << pin}, 0"
        source = "\n".join(lines)
    return source


def uart_rx(period=25, pin=1):
    if not isinstance(period, int) or not 20 <= period <= 60000 or not 0 <= pin <= 7:
        raise ValueError("unsupported UART timing/pin")
    half = period // 2
    lines = ["LI 1, 65535", f"idle: WAITFOR {pin}, 1", "BRC idle, 2",
             f"start: WAITFOR {pin}, 0", "BRC start, 2", f"DELAY {half - 2}",
             f"BRPIN start, {pin}, 1", "CLEARIS", f"DELAY {period - 2}"]
    for bit in range(8):
        lines += [f"IN {pin}, 0", f"DELAY {period - 1}"]
    lines += [f"BRPIN good, {pin}, 1", "MOVIS 0", "BREQ zero_frame, 0, 0",
              "bad: RX 1", "JMP idle", f"zero_frame: DELAY {half - 3}",
              f"BRPIN bad, {pin}, 1", "RX 3", "JMP idle", "good: RX 0", "JMP start"]
    return "\n".join(lines)


def spi_controller(mode=0, half=13):
    """MOSI=0, SCK=1, CS_n=2, MISO=3; one CS assertion per byte."""
    if mode not in range(4) or half < 12:
        raise ValueError("unsupported SPI mode/half period")
    idle = (mode >> 1) * 2
    active = idle ^ 2
    lines = [f"DRIVE {idle | 4}, 7", "next: PULL 0", "MOVOS 0", "CLEARIS",
             "LI 0, 8", f"SET 4, 0"]
    if mode & 1:
        lines += [f"loop: SET 2, {active}", "OUT8 0", f"DELAY {half - 2}",
                  f"SET 2, {idle}", "IN 3, 1", f"DELAY {half - 3}", "DJNZ 0, loop"]
    else:
        lines += ["loop: OUT8 0", f"DELAY {half - 1}", f"SET 2, {active}",
                  "IN 3, 1", f"DELAY {half - 3}", f"SET 2, {idle}", "DJNZ 0, loop"]
    lines += [f"DELAY {half}", "SET 4, 4", "RX 0", "JMP next"]
    return "\n".join(lines)


def spi_target(mode=0):
    """CS abort is watcher 0 (rising pin 2), also the WAITFOR abort mask.

    MOSI=0, SCK=1, CS_n=2, MISO=3. Each selected byte consumes one TX
    item. The polling/edge path is measured by the feasibility tests.
    """
    if mode not in range(4):
        raise ValueError("unsupported SPI mode")
    idle = mode >> 1
    sample = idle if mode & 1 else 1 - idle
    shift = 1 - sample
    lines = ["DRIVE 0, 0", "LI 1, 65535", "select: CLEAREVENT 1",
             "WAITFOR 2, 0", "BRC select, 2", "byte: PULLNB 0", "BRC underrun, 6",
             "MOVOS 0", "OE 8, 8", "LI 0, 8"]
    if mode & 1:
        lines += [f"loop: WAITFOR 1, {shift}", "BRC abort, 3", "BRC timeout, 2", "OUT8 3",
                  f"WAITFOR 1, {sample}", "BRC abort, 3", "BRC timeout, 2", "IN 0, 1"]
    else:
        # Finish the byte at its sample edge and preload the next byte during
        # the remaining half-cycle. Waiting for the final shift edge before
        # PULL/MOVOS misses the next first bit at 1 MHz after synchronization.
        lines += ["OUT8 3", f"loop: WAITFOR 1, {sample}", "BRC abort, 3", "BRC timeout, 2",
              "IN 0, 1", "DJNZ 0, shift", "RX 0", "PULLNB 0", "BRC drained, 6",
                  "MOVOS 0", "LI 0, 8", f"shift: WAITFOR 1, {shift}", "BRC abort, 3",
                  "BRC timeout, 2", "OUT8 3", "JMP loop"]
    if mode & 1:
        lines += ["DJNZ 0, loop", "RX 0", "BRPIN abort, 2, 1", "JMP byte"]
        # An empty queue is an error only if the controller clocks another
        # byte; ending CS after the final byte is a normal transaction.
        lines = [line.replace("BRC underrun, 6", "BRC drained, 6") for line in lines]
    lines += [f"drained: WAITFOR 1, {shift}", "BRC abort, 3", "BRC timeout, 2"]
    if not mode & 1:
        lines += [f"WAITFOR 1, {sample}", "BRC abort, 3", "BRC timeout, 2"]
    lines += ["FAIL 1"]
    lines += [
              "abort: DRIVE 0, 0", "JMP select", "underrun: FAIL 1", "timeout: FAIL 2"]
    return "\n".join(lines)


def sizes():
    sources = {"uart_tx": uart_tx(), "uart_rx": uart_rx()}
    for mode in range(4):
        sources[f"spi_controller_{mode}"] = spi_controller(mode)
        sources[f"spi_target_{mode}"] = spi_target(mode)
    return {name: len(assemble(source)) for name, source in sources.items()}


def i2c_controller(address=0x42, write_count=1, read_count=0, half=32):
    """SDA=0/SCL=1, both open-drain. Optional combined write/repeated-start/read.

    Byte counts are firmware parameters. TX contains write data; RX contains
    read data. The transmitted address is checked for arbitration like data.
    """
    if not 0x08 <= address <= 0x77 or not 0 <= write_count <= 16 or not 0 <= read_count <= 16:
        raise ValueError("invalid I2C address/count")
    if not write_count + read_count or half < 32:
        raise ValueError("unsupported I2C timing/empty transaction")
    lines = ["WAIT 0, 1, 65535", "CALL start"]
    if write_count:
        lines += [f"LI 0, {address << 1}", "CALL send", f"LI 1, {write_count}",
                  "write: PULLNB 0", "BRC underrun, 6", "CALL send", "DJNZ 1, write"]
        if read_count:
            lines += ["CALL start"]
    if read_count:
        lines += [f"LI 0, {address << 1 | 1}", "CALL send", f"LI 1, {read_count}",
                  "read: CLEARIS", "LI 0, 8", "SET 1, 1", f"bit: DELAY {half}",
                  "SET 2, 2", "WAIT 1, 1, 65535", f"DELAY {half}", "IN 0, 1",
                  "SET 2, 0", "DJNZ 0, bit", "RX 0", "DJNZ 1, ack",
                  "SET 1, 1", "JMP ack_clock", "ack: SET 1, 0",
                  f"ack_clock: DELAY {half}", "SET 2, 2", "WAIT 1, 1, 65535",
                  f"DELAY {half}", "SET 2, 0", "BREQ stop, 0, 1", "JMP read"]
    lines += ["stop: SET 1, 0", f"DELAY {half}", "SET 2, 2", "WAIT 1, 1, 65535",
              f"DELAY {half}", "SET 1, 1", "HALT",
              "send: MOVOS 0", "LI 0, 8", "send_bit: OUT8 0", f"DELAY {half}",
              "SET 2, 2", "WAIT 1, 1, 65535", f"DELAY {half}", "BRDIFF lost, 0",
              "SET 2, 0", "DJNZ 0, send_bit", "SET 1, 1", f"DELAY {half}",
              "SET 2, 2", "WAIT 1, 1, 65535", f"DELAY {half}", "BRPIN nack, 0, 1",
              "SET 2, 0", "RET", "lost: FAIL 4", "nack: FAIL 8", "underrun: FAIL 1",
              "start: SET 1, 1", f"DELAY {half}", "SET 2, 2", "WAIT 1, 1, 65535",
              f"DELAY {half}", "DRIVE 2, 3", f"DELAY {half}", "SET 2, 0", "RET"]
    source = "\n".join(lines)
    clock_high = f"SET 2, 2\nWAIT 1, 1, 65535\nDELAY {half}"
    return source.replace(clock_high, "CALL clock_high") + "\nclock_high: " + clock_high + "\nRET"


def i2c_target(address=0x42):
    """SDA=0/SCL=1; watchers 0=START, 1=STOP, abort mask=3.

    One non-reserved seven-bit address, read and write. Host preloads read data;
    starvation is bounded by a software counter while SCL is held low.
    """
    if not 0x08 <= address <= 0x77:
        raise ValueError("reserved/invalid I2C address")
    lines = ["DRIVE 3, 3", "LI 1, 65535", "idle: WAITEVENT 1", "BRC idle, 2",
             "address: CLEAREVENT 3", "CLEARIS", "LI 0, 8", "CALL low",
             "address_bit: CALL high", "IN 0, 1", "CALL low", "DJNZ 0, address_bit",
             "MOVIS 0", f"BREQ write_address, {address << 1}, 0",
             f"BREQ read_address, {address << 1 | 1}, 0", "JMP idle",
             "write_address: SET 1, 0", "CALL high", "CALL low", "SET 1, 1",
             "write: CLEARIS", "LI 0, 8", "write_bit: CALL high", "IN 0, 1",
             "CALL low", "DJNZ 0, write_bit", "RX 0", "SET 1, 0",
             "CALL high", "CALL low", "SET 1, 1", "JMP write",
             "read_address: SET 1, 0", "CALL high", "CALL low", "SET 1, 1",
             "read: SET 2, 0", "LI 1, 65535", "refill: PULLNB 0", "BRC empty, 6",
             "MOVOS 0", "LI 0, 8", "LI 1, 65535", "OUT8 0", "SET 2, 2",
             "read_bit: CALL high", "CALL low", "DJNZ 0, next_bit", "JMP read_ack",
             "next_bit: OUT8 0", "JMP read_bit", "read_ack: SET 1, 1", "CALL high",
             "SAMPLE 0, 1", "CALL low", "BREQ idle, 1, 0", "JMP read",
             "empty: DJNZ 1, refill", "FAIL 2",
             "interrupted: BRC expired, 2", "DRIVE 3, 3", "BRC address, 4",
             "JMP idle", "expired: FAIL 2"]
    lines = [line.replace("CALL high", "WAITBR interrupted, 1, 1")
             .replace("CALL low", "WAITBR interrupted, 1, 0") for line in lines]
    return "\n".join(lines)

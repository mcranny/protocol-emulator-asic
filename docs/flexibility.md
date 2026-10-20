# Flexibility

The chip has no protocol peripherals. It has two programmable engines, a shared
48 MHz timebase, and one pin cell per protocol pin that commits a scheduled edge
at a deadline on that timebase. A protocol is a program: the engine computes
when each edge should land and the pin cell places it there. Changing the
protocol changes only the program loaded over the host port. The silicon is the
same for every protocol on this page.

The clearest way to see this is to put a protocol the chip was obviously built
with next to one it was not, and note that the hardware underneath is identical.

## A UART transmitter

An 8N1 UART line idles high, frames a byte between a low start bit and a high
stop bit, and sends the eight data bits least significant first. The transmitter
pulls a byte from the host queue, re-anchors its cursor to the present, and
schedules one edge per bit a cursor-period apart:

```
start:
    PULL reg=0
    LI reg=1 value=8
    LI reg=3 value=1
    NOW reg=2
    CURSORSET reg=2
    OUT pin=0 level=0 offset=4
    ADVANCE ticks=417
bit:
    MOV dst=2 src=0
    AND dst=2 src=3
    BREQ reg=2 value=0 target=low
    OUT pin=0 level=1 offset=4
    JMP target=after
low:
    OUT pin=0 level=0 offset=4
after:
    SHR dst=0 amount=1
    ADVANCE ticks=417
    DJNZ reg=1 target=bit
    OUT pin=0 level=1 offset=4
    ADVANCE ticks=417
    WAITTIME
    JMP target=start
```

## A WS2812 transmitter

The WS2812 is an addressable RGB LED with a single-wire, self-clocked protocol
that looks nothing like a UART: there is no idle-high line and no start or stop
bit. The wire idles low, and every bit is a fixed 1.25 µs period made of a high
pulse then a low, where a short high (0.4 µs) is a zero and a long high (0.8 µs)
is a one. A low held for 300 µs latches the pixels. It is exactly the kind of
protocol a general-purpose chip is not designed for.

It is the same chip, the same engine, and the same pin cell. The transmitter
pulls colour bytes from the host queue and schedules two edges per bit — a
rising edge at the cursor and a falling edge a symbol's high-time later — then
advances the cursor by the rest of the period:

```
byte_loop:
    PULL reg=0
    SHL dst=0 amount=16
    LI reg=1 value=8
bit_tx:
    SHL dst=0 amount=1
    BRC target=one_tx condition=carry
    OUT pin=0 level=1 offset=0
    ADVANCE ticks=19
    OUT pin=0 level=0 offset=0
    ADVANCE ticks=41
    JMP target=next_tx
one_tx:
    OUT pin=0 level=1 offset=0
    ADVANCE ticks=38
    OUT pin=0 level=0 offset=0
    ADVANCE ticks=22
next_tx:
    DJNZ reg=1 target=bit_tx
    DJNZ reg=2 target=byte_loop
```

## Only the firmware differs

Both programs run on the same two instructions that do the real work: `OUT`
schedules a pin change for a deadline, and `ADVANCE` moves the cursor that
deadline is measured from. Neither program counts clock cycles to place an edge,
so neither is sensitive to how many instructions a branch happens to take; the
edge lands where the cursor says, and the pin cell's single pending slot paces
each loop to its own wire with no synchronisation instruction. The UART advances
the cursor by a bit period and emits one edge per bit; the WS2812 advances by a
symbol period and emits two. That is the whole difference.

There is no WS2812 block in the design, no mode bit that switched one on, and no
change to the register-transfer logic between these two programs. The protocol
the chip was never designed for is a few dozen bytes of program memory. A
self-contained version that drives three LEDs to a fixed rainbow and latches
them is checked in at `examples/v3/ws2812_rainbow.asm`.

# SPI controller, mode 0 (CPOL=0 CPHA=0), MSB first, 1 MHz.
# Drives sclk=0 cs=1 mosi=2; samples miso=3.
# R0 MOSI/MISO shift register, R1 bit counter, R2 scratch, R3 the MSB mask.
start:
    PULL reg=0
    CLEARIS
    LI reg=1 value=8
    LI reg=3 value=128
    NOW reg=2
    CURSORSET reg=2
    # a half period of headroom before chip select asserts, so the
    # setup edges are scheduled ahead of the cursor, never late
    ADVANCE ticks=24
    OUT pin=0 level=0 offset=12
    OUT pin=1 level=0 offset=12
    # present the first MOSI bit before the first leading edge
    MOV dst=2 src=0
    AND dst=2 src=3
    BREQ reg=2 value=0 target=c0_lo
    OUT pin=2 level=1 offset=12
    JMP target=c0_done
c0_lo:
    OUT pin=2 level=0 offset=12
c0_done:
    SHL dst=0 amount=1
    ADVANCE ticks=24
bit:
    OUT pin=0 level=1 offset=12
    ADVANCE ticks=24
    WAITTIME
    IN pin=3 msb_first=1
    OUT pin=0 level=0 offset=12
    MOV dst=2 src=0
    AND dst=2 src=3
    BREQ reg=2 value=0 target=c1_lo
    OUT pin=2 level=1 offset=12
    JMP target=c1_done
c1_lo:
    OUT pin=2 level=0 offset=12
c1_done:
    SHL dst=0 amount=1
    ADVANCE ticks=24
    DJNZ reg=1 target=bit
    WAITTIME
    OUT pin=1 level=1 offset=12
    RX meta=0
    JMP target=start

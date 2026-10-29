# SPI target, mode 0 (CPOL=0 CPHA=0), MSB first.
# Drives miso=3; samples sclk=0 cs=1 mosi=2.
# R0 reply shift register, R1 bit counter, R2 scratch, R3 the MSB mask.
start:
    PULL reg=0
    CLEARIS
    LI reg=1 value=8
    LI reg=3 value=128
    NOW reg=2
    CURSORSET reg=2
    ADVANCE ticks=1048576
    WAITEDGE pin=1 polarity=falling
    BRC target=start condition=timeout
    # present the first MISO bit before the first leading edge
    NOW reg=2
    CURSORSET reg=2
    MOV dst=2 src=0
    AND dst=2 src=3
    BREQ reg=2 value=0 target=t0_lo
    OUT pin=3 level=1 offset=12
    JMP target=t0_done
t0_lo:
    OUT pin=3 level=0 offset=12
t0_done:
    SHL dst=0 amount=1
    ADVANCE ticks=1048576
bit:
    WAITEDGE pin=0 polarity=rising
    BRC target=start condition=timeout
    IN pin=2 msb_first=1
    WAITEDGE pin=0 polarity=falling
    BRC target=start condition=timeout
    NOW reg=2
    CURSORSET reg=2
    MOV dst=2 src=0
    AND dst=2 src=3
    BREQ reg=2 value=0 target=t1_lo
    OUT pin=3 level=1 offset=12
    JMP target=t1_done
t1_lo:
    OUT pin=3 level=0 offset=12
t1_done:
    SHL dst=0 amount=1
    ADVANCE ticks=1048576
    DJNZ reg=1 target=bit
    RX meta=0
    JMP target=start

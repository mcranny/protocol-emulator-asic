# UART transmit, 8N1. One host transmit-queue byte per frame.
# R0 data shift register, R1 bit counter, R2 time scratch, R3 the constant one.
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

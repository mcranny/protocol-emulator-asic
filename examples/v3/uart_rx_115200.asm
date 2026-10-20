# UART receive, 8N1. The engine samples 0 and never drives it.
# R0 pin mask scratch, R1 bit counter, R2 edge timestamp / time scratch,
# R3 the sampled stop-bit byte. The byte accumulates in the input shift
# register and is pushed by RX; its meta byte flags framing.
rearm:
    CLEARIS
    NOW reg=2
    CURSORSET reg=2
    ADVANCE ticks=1048576
    WAITEDGE pin=0 polarity=falling
    BRC target=rearm condition=timeout
    EDGETIME pin=0 reg=2
    CURSORSET reg=2
    ADVANCE ticks=625
    LI reg=1 value=8
sample:
    WAITTIME
    IN pin=0 msb_first=0
    ADVANCE ticks=417
    DJNZ reg=1 target=sample
    WAITTIME
    SAMPLE reg=3
    LI reg=0 value=1
    AND dst=3 src=0
    BREQ reg=3 value=0 target=framing
    RX meta=0
    JMP target=rearm
framing:
    RX meta=1
    JMP target=rearm

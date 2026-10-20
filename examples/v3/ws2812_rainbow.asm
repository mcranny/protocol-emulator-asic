# WS2812 rainbow: three LEDs driven to fixed, distinct GRB triples, then a
# reset-low latch, then halt. The colour bytes are immediates, not pulled.
# R0 data shift register, R1 bit counter, R3 time scratch.
start:
    NOW reg=3
    CURSORSET reg=3
    ADVANCE ticks=16
    LI reg=0 value=0
    CALL target=send_byte
    LI reg=0 value=16711680
    CALL target=send_byte
    LI reg=0 value=0
    CALL target=send_byte
    LI reg=0 value=16711680
    CALL target=send_byte
    LI reg=0 value=0
    CALL target=send_byte
    LI reg=0 value=0
    CALL target=send_byte
    LI reg=0 value=0
    CALL target=send_byte
    LI reg=0 value=0
    CALL target=send_byte
    LI reg=0 value=16711680
    CALL target=send_byte
    ADVANCE ticks=14400
    WAITTIME
    HALT
# Send R0 (left-justified byte) as eight WS2812 symbols, most significant first.
send_byte:
    LI reg=1 value=8
bit_rb:
    SHL dst=0 amount=1
    BRC target=one_rb condition=carry
    OUT pin=0 level=1 offset=0
    ADVANCE ticks=19
    OUT pin=0 level=0 offset=0
    ADVANCE ticks=41
    JMP target=next_rb
one_rb:
    OUT pin=0 level=1 offset=0
    ADVANCE ticks=38
    OUT pin=0 level=0 offset=0
    ADVANCE ticks=22
next_rb:
    DJNZ reg=1 target=bit_rb
    RET

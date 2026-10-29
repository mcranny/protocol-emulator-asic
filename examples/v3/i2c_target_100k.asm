# I2C target at 7-bit address 85. SDA is pin 0, SCL is pin 1.
# Matches the address, acknowledges, clock-stretches once, then receives one
# byte (write) or transmits one byte from the queue (read). A START or STOP
# re-synchronises it. R0 byte/scratch, R1 scratch, R2 pins/send, R3 counter.
reset_t:
    OE mask=3 oe=0 offset=0
    CLEARIS
wait_start:
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=1048576
    WAITEDGE pin=0 polarity=falling
    BRC condition=timeout target=wait_start
    SAMPLE reg=2
    LI reg=1 value=2
    AND dst=1 src=2
    BREQ reg=1 value=0 target=wait_start
    CLEARIS
    LI reg=3 value=8
ga_loop:
    CALL target=wait_scl_rise
    IN pin=0 msb_first=1
    DJNZ reg=3 target=ga_loop
    MOVIS reg=0
    MOV dst=1 src=0
    SHR dst=1 amount=1
    BREQ reg=1 value=85 target=addr_ok
    JMP target=reset_t
addr_ok:
    CALL target=wait_scl_fall
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=8
    OE mask=1 oe=1 offset=0
    OE mask=2 oe=2 offset=0
    ADVANCE ticks=240
    OE mask=2 oe=0 offset=0
    CALL target=wait_scl_rise
    CALL target=wait_scl_fall
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=8
    OE mask=1 oe=0 offset=0
    LI reg=1 value=1
    AND dst=1 src=0
    BREQ reg=1 value=0 target=t_receive
    JMP target=t_transmit
t_receive:
    CLEARIS
    LI reg=3 value=8
tr_loop:
    CALL target=wait_scl_rise
    IN pin=0 msb_first=1
    DJNZ reg=3 target=tr_loop
    RX meta=16
    CALL target=wait_scl_fall
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=8
    OE mask=1 oe=1 offset=0
    CALL target=wait_scl_rise
    CALL target=wait_scl_fall
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=8
    OE mask=1 oe=0 offset=0
    JMP target=reset_t
t_transmit:
    PULL reg=2
    LI reg=3 value=8
tt_loop:
    MOV dst=1 src=2
    LI reg=0 value=128
    AND dst=1 src=0
    NOW reg=0
    CURSORSET reg=0
    ADVANCE ticks=8
    BREQ reg=1 value=0 target=tt_zero
    OE mask=1 oe=0 offset=0
    JMP target=tt_next
tt_zero:
    OE mask=1 oe=1 offset=0
tt_next:
    CALL target=wait_scl_rise
    SHL dst=2 amount=1
    DJNZ reg=3 target=tt_fall
    JMP target=tt_after
tt_fall:
    CALL target=wait_scl_fall
    JMP target=tt_loop
tt_after:
    CALL target=wait_scl_fall
    NOW reg=0
    CURSORSET reg=0
    ADVANCE ticks=8
    OE mask=1 oe=0 offset=0
    CALL target=wait_scl_rise
    CALL target=wait_scl_fall
    JMP target=reset_t
wait_scl_rise:
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=1048576
    WAITEDGE pin=1 polarity=rising
    RET
wait_scl_fall:
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=1048576
    WAITEDGE pin=1 polarity=falling
    RET

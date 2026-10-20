# I2C controller. R0 byte/shift, R1 scratch, R2 sampled pins, R3 status/counter.
# SDA is pin 0, SCL is pin 1; both open-drain, pulled low by OE only.
main:
    PULL reg=0
    BREQ reg=0 value=1 target=h_start
    BREQ reg=0 value=5 target=h_restart
    BREQ reg=0 value=2 target=h_write
    BREQ reg=0 value=3 target=h_read
    BREQ reg=0 value=4 target=h_readnack
    BREQ reg=0 value=6 target=h_stop
    JMP target=main
h_start:
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=8
    OE mask=1 oe=1 offset=0
    ADVANCE ticks=240
    OE mask=2 oe=2 offset=0
    PULL reg=0
    CALL target=send_byte
    BREQ reg=3 value=2 target=h_abort
    JMP target=main
h_restart:
    OE mask=1 oe=0 offset=120
    ADVANCE ticks=240
    OE mask=2 oe=0 offset=0
    ADVANCE ticks=240
    OE mask=1 oe=1 offset=0
    ADVANCE ticks=240
    OE mask=2 oe=2 offset=0
    PULL reg=0
    CALL target=send_byte
    BREQ reg=3 value=2 target=h_abort
    JMP target=main
h_write:
    PULL reg=0
    CALL target=send_byte
    BREQ reg=3 value=2 target=h_abort
    JMP target=main
h_read:
    CALL target=read_byte
    OE mask=1 oe=1 offset=120
    CALL target=tick_bit
    RX meta=4
    JMP target=main
h_readnack:
    CALL target=read_byte
    OE mask=1 oe=0 offset=120
    CALL target=tick_bit
    RX meta=12
    JMP target=main
h_stop:
    OE mask=1 oe=1 offset=120
    ADVANCE ticks=240
    OE mask=2 oe=0 offset=0
    ADVANCE ticks=240
    OE mask=1 oe=0 offset=0
    ADVANCE ticks=240
    JMP target=main
h_abort:
    OE mask=3 oe=0 offset=0
    JMP target=main
# send_byte: clock out R0 MSB first, then read the acknowledge. Pushes one RX
# record and returns the status (0 ACK, NACK, or ARB_LOST) in R3.
send_byte:
    CLEARIS
    LI reg=3 value=8
sb_loop:
    MOV dst=1 src=0
    LI reg=2 value=128
    AND dst=1 src=2
    BREQ reg=1 value=0 target=sb_zero
    OE mask=1 oe=0 offset=120
    CALL target=tick_bit
    LI reg=1 value=1
    AND dst=1 src=2
    BREQ reg=1 value=0 target=sb_arblost
    JMP target=sb_adv
sb_zero:
    OE mask=1 oe=1 offset=120
    CALL target=tick_bit
sb_adv:
    SHL dst=0 amount=1
    DJNZ reg=3 target=sb_loop
    OE mask=1 oe=0 offset=120
    CALL target=tick_bit
    LI reg=1 value=1
    AND dst=1 src=2
    BREQ reg=1 value=0 target=sb_acked
    LI reg=3 value=1
    RX meta=1
    RET
sb_acked:
    LI reg=3 value=0
    RX meta=0
    RET
sb_arblost:
    OE mask=3 oe=0 offset=0
    LI reg=3 value=2
    RX meta=2
    RET
# read_byte: clock in eight bits MSB first into the input shift register.
read_byte:
    CLEARIS
    LI reg=3 value=8
rb_loop:
    OE mask=1 oe=0 offset=120
    CALL target=tick_bit
    IN pin=0 msb_first=1
    DJNZ reg=3 target=rb_loop
    RET
# tick_bit: one SCL low/high pulse. SDA is set up by the caller during the low
# phase. Samples the bus into R2 during the high phase and tolerates a target
# that stretches the clock by holding SCL low.
tick_bit:
    ADVANCE ticks=240
    OE mask=2 oe=0 offset=0
    ADVANCE ticks=16
    WAITTIME
    SAMPLE reg=2
    LI reg=1 value=2
    AND dst=1 src=2
    BREQ reg=1 value=0 target=tb_stretch
    ADVANCE ticks=224
    OE mask=2 oe=2 offset=0
    RET
tb_stretch:
    ADVANCE ticks=1048576
    WAITEDGE pin=1 polarity=rising
    EDGETIME pin=1 reg=1
    CURSORSET reg=1
    ADVANCE ticks=16
    WAITTIME
    SAMPLE reg=2
    ADVANCE ticks=224
    OE mask=2 oe=2 offset=0
    RET

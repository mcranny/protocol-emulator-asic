# I2C bus clear: nine SCL pulses with SDA released, then a STOP. Recovery only.
# SDA is pin 0, SCL is pin 1; both open-drain.
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks=8
    OE mask=1 oe=0 offset=0
    LI reg=3 value=8
bc_loop:
    OE mask=2 oe=2 offset=0
    ADVANCE ticks=240
    OE mask=2 oe=0 offset=0
    ADVANCE ticks=240
    DJNZ reg=3 target=bc_loop
    OE mask=2 oe=2 offset=0
    OE mask=1 oe=1 offset=120
    ADVANCE ticks=240
    OE mask=2 oe=0 offset=0
    ADVANCE ticks=240
    OE mask=1 oe=0 offset=0
    ADVANCE ticks=240
    HALT

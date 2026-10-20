# Project datasheet

## How it works

V3 has two programmable engines sharing eight protocol pins and one 48 MHz
timebase. Each engine has 256 words of 32-bit program memory in SRAM and a
private time cursor. Each pin holds one pending `{due_time, value, oe}` commit
and applies it on its due tick. A shared CRC/LFSR serves both engines, engine 0
first. A 32-record capture unit stamps pin activity with the shared timestamp
and does not control the engines. The top module is
`tt_um_mcranny_protocol_emulator_v3`.

An engine can drive only the pins it owns, and the two ownership masks are
disjoint. Open-drain pins never drive high. Reset and disabling the design
release every protocol output.

## How to test

Connect a mode-0 SPI host to `ui_in[0]` (SCK), `ui_in[1]` (CS_n) and `ui_in[2]`
(MOSI), and read `uo_out[0]` (MISO). A frame is exactly 48 clocks. The response
comes back on the following frame. `uo_out[1]` and `uo_out[2]` are the two
engines running; `uo_out[3]` is an aggregate error. `uio[7:0]` are the protocol
pins.

Write all 256 program words, read them back, configure disjoint ownership, then
start. The version word is `0x03000000`.

## External hardware

A mode-0 SPI host and a way to observe the protocol pins are enough for a
digital demonstration. I2C and other open-drain signals need pull-ups. This
datasheet does not claim pad timing or a board demonstration.

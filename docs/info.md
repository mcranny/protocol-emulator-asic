# Project datasheet

## How it works

V2 has two programmable timing engines, each with 64 24-bit instructions,
16 TX bytes and 16 RX records. At 25 MHz, ordinary firmware implements UART
transmit/receive at approximately 115200 baud or 1 Mbaud, SPI modes 0–3 in
controller/target roles, and I2C at 100/400 kHz in controller/target roles.
A separate triggered capture unit records up to 32 timestamped pin samples.
The V2 top is `tt_um_mcranny_protocol_emulator_v2`. Default project metadata is
still V1 until the release-promotion step; this datasheet describes V2.

Programs share eight bidirectional protocol pins through disjoint ownership
masks. Open-drain masks release high levels. Reset, deselection, halt and fatal
errors release protocol outputs. Following state loss, assert reset, wait for
its synchronizer, reconfigure ownership/watches/filtering, reload programs and
refill queues before restarting. Program and capture retention across power
loss is not supported. See [architecture](v2-architecture.md) and
[reset behavior](reset-and-power.md).

## How to test

Connect a mode-0 SPI host to ui0 (SCK), ui1 (CS_n), ui2 (MOSI), and uo0 (MISO).
Use at most 1 MHz SCK, exactly 40 clocks per frame and at least 200 ns for
high/low and CS setup/hold/inactive intervals. uo1/uo2 indicate engines 0/1
running; uo3 indicates an aggregate error. Read version 0x020240 before use.

Load and verify firmware, configure disjoint ownership, queue data and start
the selected engines. [Runnable examples](example-programs.md) provide UART,
SPI and I2C pin assignments, expected transactions and linked verification.
The [verification write-up](verification.md) and [release evidence](release-evidence.md)
separate model/RTL checks, stock TT qualification and demonstrated hardware.

## External hardware

A compatible SPI host and protocol peer or logic analyzer are needed for a
physical demonstration. I2C/open-drain lines need suitable pull-ups. Check pad
and board voltages and avoid competing push-pull drivers. Digital verification
does not establish analog rise times or hardware operation; no physical
board demonstration has been completed.

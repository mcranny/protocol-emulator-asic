## How it works

This project is the foundation for a programmable, dual-engine protocol
emulator. The initial top-level design exposes a reset-safe passthrough from the
dedicated inputs to the dedicated outputs while leaving every bidirectional pin
released. This small behavior validates the CMOS5L simulation and physical
implementation flow before the execution engines are added.

## How to test

Drive a value on `ui_in` while the design is enabled and out of reset. The same
value appears on `uo_out`. When reset is asserted or the design is disabled,
`uo_out` is zero. All `uio` pins remain inputs.

## External hardware

None for the foundation design.

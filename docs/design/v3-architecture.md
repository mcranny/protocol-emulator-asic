# Protocol Emulator ASIC V3

V3 is the Jane Street protocol-emulator submission. The goal is a chip with SRAM program
memory, deadline-based pin timing, a 48 MHz clock, and larger electrical and protocol margins
than V2. Flexibility is the first criterion: the chip has to be reprogrammable for protocols
it was not designed for. UART, SPI and I2C in both roles are the designed protocols. WS2812 is
the protocol it was not designed for. Low-speed USB is deferred; the CRC unit is included so
that firmware can be added later. 10 Mbit Ethernet, 1-Wire and an independent second model are
out of scope.

There is no V1 or V2 compatibility. Those sources remain on the `v1.0.0` and `v2` tags. See
[the project history](../history.md).

## Architecture

Engines decide what happens. Pin cells decide when it happens.

**Timebase.** One free-running 24-bit counter at 48 MHz, wrapping every 349.5 ms. Both engines,
all eight pin cells and capture use it. Deadline comparisons use a signed distance, so the wrap
is invisible as long as a deadline stays within ±174.8 ms of the present. An 8-bit epoch extends
the timestamp to 32 bits for capture, covering about 89 s.

**Pin cells.** Each protocol pin has one cell and one pending slot `{due_time, value, oe}`:

- When the signed distance `due_time - now` reaches zero, the cell applies the slot and frees it.
- A slot written with a distance that is already negative is applied on that cycle and sets the
  pin's sticky missed-deadline flag.
- An occupied slot is never overwritten.
- Open-drain pins never drive high. A requested high becomes high impedance in the cell.
- The cell releases its output enable when it is disabled or in reset.
- The input path is a two-flop synchronizer. A three-sample majority filter runs on every pad;
  one 8-bit mask set by `FILTER` chooses, per pin, whether the cell takes the filtered level (bit
  set, the reset state) or the raw pad (bit clear). The engine sees the synchronized result, plus
  the timestamp and polarity of its last edge. Capture records the raw synchronized input, not
  the filtered or selected level.

**Engines.** Two engines share the eight pins. Ownership masks are disjoint by construction:
engine 0 keeps the mask it was given, and engine 1's mask has those bits removed. The masks and
the open-drain configuration change only while both engines are stopped. An engine can commit
only to pins it owns. Each engine has its own 24-bit cursor, set to `now` on start, and the
cursor changes only when the program changes it.

**Program memory.** One `RM_IHPSG13_1P_256x32_c2_bm_bist` per engine. The read is registered:
the address in one cycle, the word in the next. `A_MEN` is high only for a fetch or a write.
`A_DLY` is tied high and the `A_BIST_*` inputs are tied low. SRAM contents are undefined at
power-up, so each engine keeps a 256-bit validity bitmap. Fetching an unwritten word is fatal.
Start is rejected unless every bit is set. A START frame is all-or-nothing: if any commanded
engine's image is incomplete the whole frame is rejected and neither engine starts, so a pair can
never be half-started. The loader writes all 256 words, fills unused words with HALT and reads the
image back before start.

**CRC.** One shared 16-bit CRC/LFSR. `msb_first` selects the shift direction and therefore which
end of the register is the polynomial tap: MSB first shifts left and uses the normal-form
polynomial (CRC-16/CCITT, 0x1021); LSB first shifts right and uses the reflected polynomial
(CRC-16/ARC, 0xA001, and USB CRC-5, 0x0014). One message bit moves per `shift` strobe. The unit
performs one operation per clock. Engine 0 has fixed priority. If both engines request a CRC
operation in the same cycle, engine 0 proceeds and engine 1 stalls that cycle and retries, so no
request is lost; both operations land, engine 0's first.

**Capture.** 32 records. Each record is stamped with the shared `{epoch, now}` timestamp and
holds the raw synchronized inputs, the driven outputs and enables, and the engine flags. The
store does not wrap. When it fills, capture stops with the truncated flag and reason 5, keeping
the first 32 records. Reason 6 (timestamp saturation) is never produced. Capture observes; it
does not control an engine. The host arms and stops it with `CAPARM`, reads its state with
`STATUS` and reads its records with `CAPREAD`; starting an engine does not arm it.

**Host.** SPI mode 0, MSB first, exactly 48 clocks per frame: `cmd[7:0]`, `addr[7:0]`,
`payload[31:0]`, first bit at bit 47. Any other length is rejected and discarded. SCK, CS_n and
MOSI each pass through two synchronizer flops. The response to a frame is shifted out during the
following frame. MISO is gated by the raw CS_n, so the pin releases as soon as the host
deselects. A rejected command sets bit 7 of the echoed command byte. Engine selection uses
another bit of the command byte.

The command code is the low four bits of the command byte. `TXPUSH` (0x08) carries its byte count
(one to four) in `addr` and the bytes in the payload, most significant first, and is rejected if
the count is out of range or the bytes do not fit. `RXPOP` (0x09) returns the oldest receive
record as `{value, meta}` in the low 16 payload bits, and rejects with a zero payload when the
queue is empty, popping nothing. Both are accepted while the engines run, which is their purpose.
`CLEAR` (0x0A) invalidates the addressed engine's program image by clearing its validity bitmap,
so every word must be rewritten before the image is complete again; it is rejected while that
engine runs. The loader issues it before each reload so an interrupted reload leaves the image
incomplete rather than letting stale valid words make a partial reload startable.

The next four commands ignore the engine bit except where noted.

- `FILTER` (0x0B) with `addr[0] = 0` writes `payload[7:0]` as the input-filter mask, effective
  the next clock, and is rejected unless both engines are stopped. With `addr[0] = 1` it reads
  the mask and is never rejected. Either way the reply is `{24'b0, mask}` holding the mask that
  remains, so a rejected write shows the unchanged mask. The mask resets to `0xFF`.
- `CAPARM` (0x0C) is always accepted. With `addr[0] = 0` it arms capture with `payload[1:0]` as
  the trigger mode and `payload[15:8]` as the trigger mask, restarting a capture already running,
  and replies `{16'b0, mask, 6'b0, mode}`. With `addr[0] = 1` it stops capture and replies zero.
- `STATUS` (0x0D) address 0 returns the engine word for the engine the engine bit selects: `[0]`
  running, `[1]` halted, `[2]` stalled, `[10:3]` fault code, `[15:11]` transmit level, `[20:16]`
  receive level, `[28:21]` the eight pins' missed flags (shared, so the same for either engine).
  Address 1 returns capture state: `[0]` armed, `[1]` triggered, `[2]` truncated, `[5:3]` reason,
  `[11:6]` count. Address 2 returns the 32-bit trigger timestamp. Unused bits are zero. Any other
  address is rejected with a zero payload.
- `CAPREAD` (0x0E) is always accepted. `addr[5:1]` is the record index and `addr[0]` selects the
  half: 0 returns the sample `{pins_in, pins_out, pins_oe, flags}`, 1 the timestamp. The read
  port is combinational, so no prefetch is needed. An index at or past the count reads zero.
  Reading changes no capture state.

Code 0x0F echoes like `NOP`.

| `ui_in` | Function | `uo_out` | Function |
|---|---|---|---|
| 0 | SPI SCK | 0 | SPI MISO |
| 1 | SPI CS_n | 1 | engine 0 running |
| 2 | SPI MOSI | 2 | engine 1 running |
| 7:3 | unused | 3 | aggregate sticky error |
| | | 7:4 | unused, driven low |

`uio[7:0]` are the protocol pins.

**Queues.** Each engine has a 16-deep transmit byte queue and a 16-deep receive record queue, each
a ring with a read pointer, a write pointer and a separate level counter. The host fills the
transmit queue with `TXPUSH` and the engine drains it with `PULL`; the engine fills the receive
queue with `RX` and the host drains it with `RXPOP`. A transmit push carries one to four bytes and
is atomic, taken only if the whole group fits. The head is combinational from storage, so a `PULL`
or an `RXPOP` reply sees the oldest entry in the same cycle it removes it. The queues keep their
contents across start and stop; only reset clears them. An `RX` push to a full receive queue never
happens in practice: the engine faults `E_QUEUE` first, so the drop-on-full is only a backstop.

## Instruction set

A word is 32 bits. The opcode is a flat 6-bit field in `[31:26]` and the operand is `[25:0]`.
There is no sub-opcode escape. Unused opcodes are illegal, and reserved operand bits must be
zero. The opcode table is one source. It drives the assembler, the Python model and a generated
Verilog header that the RTL uses for its legality check.

State per engine: R0-R3 at 24 bits, a 24-bit output shift register, an 8-bit input shift
register, a 24-bit cursor, a two-entry return stack, and timeout, interrupt and missed-deadline
flags.

| Class | Instructions |
|---|---|
| Control | NOP, HALT, JMP, BRC, DJNZ, CALL, RET, FAIL |
| Registers | LI, MOV, ADD, SUB, AND, OR, XOR, SHL, SHR, BREQ |
| Time | ADVANCE, ADVANCER, CURSORSET, CURSORGET, NOW, WAITTIME |
| Pin commit | SET, OE, DRIVE, OUT, each with a 10-bit cursor offset |
| Input | SAMPLE, IN, EDGETIME, WAITEDGE, WAITFOR |
| Queues | PULL, PULLNB, RX |
| Events | MARK, WAITEVENT, CLEAREVENT, CLEARMISS |
| CRC | CRCCFG, CRCLOAD, CRCBIT, CRCGET |

**Fetch.** The engine prefetches the next word every cycle, so straight-line code runs one
instruction per cycle. A taken branch inserts one bubble and costs two cycles. A return on an
empty stack, or a call when the two-entry stack is full, faults and halts.

**Time.** `ADVANCE n` adds `n` to the cursor in one cycle and never stalls. A commit writes
`{value, oe}` with `due_time = cursor + offset` for every targeted pin. The 10-bit offset covers
0 to 1023 ticks (21.3 µs). A commit stalls until every pin it targets has a free slot. Firmware
keeps the cursor within ±8,388,608 ticks (±174.8 ms) of `now`; longer delays are repeated
advances, so a due time is never misread as already past.

`WAITEDGE` latches `now` as a baseline on its first cycle and matches a timestamped edge at or
after that baseline with the requested polarity. It does not read the live level. `WAITFOR`
matches a filtered level. Both end on a match, an aborting event, or the cursor-derived timeout,
with priority interrupt, then match, then timeout. `WAITEVENT` ends on a matching event or the
same timeout and is not aborted. `PULL` on an empty transmit queue is the only stall with no
timeout.

`CLEARMISS` clears the missed flag only on masked pins the engine owns. `CRCBIT` shifts the low
bit of its register.

## Clock and physical target

The clock constraint is 20.833 ns. The maximum transition constraint is 1.5 ns. Placement and
global-route hold margin is 0.10 ns. Signoff, once the flow is run, is zero setup, hold, slew,
capacitance and fanout violations at all three corners, with at least +0.10 ns worst fast-corner
hold slack and at least +2 ns worst slow-corner register-to-register setup slack.

The die is 8x4 tiles (1724.16 x 710.64 µm), using the CMOS5L 8x4 template from Tiny Tapeout
support tools `d66cf17`. The two SRAM macros, about 98,977 µm² together, sit side by side on
the bottom edge at y = 15.60 µm, at x = 2.88 µm and x = 452.48 µm. Both are mirrored (`FS`) so
their signal pins face up into the core. They sit 0.48 µm above the row boundary at 15.12 µm
because the Metal1 power rail on that boundary would otherwise come within 0.065 µm of Metal1
inside the SRAM. Nothing is placed below or between them, so no net has
to cross a macro. Stacked on the left edge instead, each macro walled the logic under the
top-edge I/O pins off from the logic below it, and global routing ran 112 nets through them.
The CMOS5L tile has vertical Metal4
supply stripes only, and a stripe feeds a macro only where it lands on that macro's supply column,
so the power grid is derived from this macro's LEF rather than from the published 512x32
walkthrough. The columns are 2.81 µm wide on an 11.24 µm pitch, but the right half of the macro is
0.67 µm off the left half's phase. The stripes are therefore 2.81 µm wide at a 44.96 µm pitch,
aligned to the left-half columns, with the first stripe centred 5.665 µm from the core edge.
The second macro is ten stripe pitches (449.6 µm) right of the first, so its columns have the
same phase.
The power-grid generator stops core stripes short of a macro and has no layer above Metal4 to
join them through, so `physical/v3/pdn_cfg.tcl` extends each stripe end that lines up with a
supply column of its own net 1 µm onto that column. Each macro gets 16 such bridges across 4 VDD,
4 VDDARRAY and 4 VSS columns, and the script fails if a macro gets none. A Metal4 power-grid
obstruction covers the off-phase half of each macro and ends 4 µm past its top and bottom
edges, so the in-phase stripes still reach both ends of the columns. The physical configuration
is `physical/v3/config.json`. It also sets Metal2 to Metal4 routing obstructions over each macro
body, leaving the 1 µm pin edge open, and antenna repair inserts diodes where the router needs
them; none are placed ahead of routing.

Each macro has 186 signal pins, 110 of them tied off, and their tie cells and output buffers
have to sit near the pins. The placement halo is 10 µm above and below each macro and 20 µm
beside it. The two side halos cover the 32.96 µm gap between the macros, which would otherwise
be a pocket of rows with its own short power stripes. An 80 µm horizontal and 50 µm vertical
halo pushed every tie cell and output buffer 80 µm away from the pins, and the routed result
had 41 SRAM outputs over their capacitance limit. Global routing uses a 0.2 capacity adjustment
and may not finish with congestion, so an overflow stops the flow at that step. At 0.3 it
overflowed, and at 0.15 the slow-corner setup slack after global routing fell to 0.20 ns,
against 0.31 to 0.87 ns in two runs at 0.2.

`DESIGN_REPAIR_MAX_WIRE_LENGTH` is 190 µm. Left at its 0 (disabled) default, repair only buffers
a net for slew or capacitance, not for length on its own, and one SRAM output had no load
anywhere nearby: `u_progmem1.u_sram/A_DOUT[3]` drove straight to its first standard-cell load
about 319 µm away, well outside the 10 µm halo, with nothing in between. That left 0.087 pF on a
0.064 pF pin, since the wire's own capacitance, undivided by any buffer, was charged to the
macro's weak output driver. The post-global-route repair pass found that violation but did not
fix it. Capping the buffering distance at 190 µm, below the roughly 190-235 µm break-even for
that pin, makes a repair pass split a run like that instead. It is set on the pre-route repair
(`OpenROAD.RepairDesignPostGPL`) rather than the post-route one (`OpenROAD.RepairDesignPostGRT`):
the same 100 µm cap applied after global routing had to re-split routes global routing had
already committed to, which added about 22,500 buffers across its two repair passes (13,000 of
them in the larger single pass alone) and overflowed routing.
Applied before routing instead, the splits are placed and legalized once, and global routing
only has to route the result.

Fixing this violation has a cost: the roughly 3,300 extra buffers it adds shift enough paths that
slow-corner `timing__setup_r2r__ws` (a dedicated single-corner rerun of `OpenROAD.STAMidPNR-3`,
since that step itself only analyzes the first of `PNR_CORNERS`, the fast corner) dropped from
+0.31 ns (uncapped, with the one violation) to +0.15 ns at 190 µm. Loosening the cap to 230 µm did
not recover that margin; it reintroduced a different capacitance violation and took the fast
corner's own r2r setup slack negative instead, so the tool's response to this one knob is not
monotonic and was not pursued further. 190 µm is accepted with 0 known capacitance or slew
violations and +0.15 ns of pre-route register-to-register margin at the slow corner; final timing
is decided by the signed-off post-route numbers, not this estimate.

That pre-route estimate was low. On the routed design, extracted net capacitance was 1.29 times
the estimate, weighted by capacitance, and the same slow-corner register-to-register slack came
out at −3.63 ns. `LAYERS_RC` capacitance for Metal1 through Metal4 is therefore 1.29 times the
earlier value, so repair and the pre-route timing see a wire load in line with extraction.
Removing the 190 µm cap on top of that, to let repair add still more buffers, overflowed global
routing: the post-global-route repair pass hit a heap underflow on `pm1_fetch_data[9]` after
inserting 4444 buffers. The cap stays.

With the calibrated values, slow-corner register-to-register slack was −0.28 ns before routing and
−2.43 ns extracted, on 52 paths that all launch from an SRAM output. No timing repair runs after
detailed routing, so the setup repair after clock-tree synthesis and after global routing
(`PL_RESIZER_SETUP_SLACK_MARGIN`, `GRT_RESIZER_SETUP_SLACK_MARGIN`) aims at 2.0 ns, the signoff
margin, instead of the 0.05 and 0.025 ns defaults. In a preliminary run that took the pre-route
slack to +1.79 ns, mostly by upsizing about 280 cells; it added 22 cells.

Global placement is timing-driven (`PL_TIMING_DRIVEN`). Without it, the worst SRAM-launched path
wandered about 5,600 µm across the die. With it, at the same margins, slow-corner pre-route slack
rose to +2.18 ns. The cost is placement time, 11 minutes 40 seconds against about 1 minute 20
seconds before, and 9.9% more cell area from the repair it runs during placement.

Clock-tree synthesis balances the macro clock against the register tree by putting 13 delay
buffers, 1.55 ns at the slow corner, in front of both SRAM `A_CLK` pins and nowhere else. That
delay comes straight out of every failing path, while the register-to-SRAM paths it protects
have more than 15 ns of setup slack. `CTS_DELAY_BUFFER_DERATE_PCT` does not remove it: at 0, 1
and 0.5 the flow passes `-delay_buffer_derate` through and OpenROAD still inserts all 13, and no
other clock-tree variable in this LibreLane version reaches the latency-balancing stage.

The halo cuts the placement rows just above and below each macro, and the power generator will
not strap a supply stripe across a gap with no row, which leaves the SRAM `VDD!` pins
unconnected. The power script puts those row segments back while it builds the grid, so the
stripes and their rail vias are created, then removes the rows again. No cell is placed within
the halo. Mirroring puts each macro's `VDDARRAY!` column ends at the bottom, so `VDDARRAY!` is fed
only by the short stripes below the macros, which reach the rest of the grid through the Metal1
rails of the restored rows. Antenna repair before detailed routing (`GRT_ANTENNA_REPAIR_ITERS`)
runs, but the routed result still had 12 Metal3 antenna violations, the worst at 1.24 times its
limit. Detailed routing's own antenna-repair loop (`DRT_ANTENNA_REPAIR_ITERS`) therefore gets
one diode-only pass. Each pass re-runs detailed routing, and on a congested earlier floorplan a
longer loop re-ran a full 64-iteration route and nearly doubled the marker count.

IR drop is reported on every full run. Without `VSRC_LOC_FILES` the analysis feeds the grid
at the library voltage, and the bottom-edge run found every `VPWR` and `VGND` shape connected,
`VDDARRAY!` included, with a worst-case drop of 0.25 mV (0.02%). Magic DRC stays off: the Tiny
Tapeout precheck for CMOS5L runs KLayout DRC with the SG13CMOS5L deck and does not run Magic,
and KLayout DRC runs in the flow.

The flow is the stock Tiny Tapeout flow, run locally from RTL. Release evidence is a fresh run:
precheck, KLayout DRC, LVS, extracted timing and gate-level tests with the SRAM model. No V3
run has passed it yet. No pad timing is claimed until Tiny Tapeout publishes CMOS5L pad data.

## Toolchain

`protocol_emulator.v3` is a Python 3.11 package, type-checked with mypy. It holds the opcode
table, the assembler, the image loader, the host frame codec and the cycle-accurate model.
Timing constants are computed from seconds at 48 MHz. A result that is not an integer cycle
within the stated tolerance is rejected, and the image records the actual rate and the error.

| Target | Cycles | Result |
|---|---|---|
| UART 9600 | 5000 | exact |
| UART 115200 | 417 | 0.080% error |
| UART 1 Mbaud | 48 | exact |
| SPI 1 MHz | 24 high, 24 low | exact |
| I2C 100 kHz | 240 low, 240 high | 5.0 µs each |
| I2C 400 kHz | 72 low, 48 high | 1.5 µs and 1.0 µs |
| WS2812 bit 0 | 19 high, 41 low | 0.40 / 0.85 µs |
| WS2812 bit 1 | 38 high, 22 low | 0.79 / 0.46 µs |
| WS2812 reset | at least 14,400 low | at least 300 µs |

The 400 kHz split leaves 400 ns of high-time margin for open-drain rise. The firmware rule is to
schedule an edge and then advance the cursor by the interval. The loop body has to finish inside
the interval; a late commit is applied immediately and flagged. A program larger than 256 words
fails assembly.

Python tests run on Windows. RTL simulation (Icarus Verilog, cocotb) and the induction proofs
(Yosys) run in WSL. Line endings are LF, forced by `.gitattributes`, because provenance checks
hash the sources.

## Verification

The property under test is when a pin changed, not only which instruction retired.

| Proof | What it establishes |
|---|---|
| Timebase | The counter advances by one each cycle, and the epoch carries exactly on the wrap |
| Pin cell | Never early; fires on the due cycle; a past-due slot applies immediately and flags; no overwrite; open-drain never drives high; reset and disable release the pin; an occupied slot frees within its distance |
| Input filter | Three agreeing samples qualify a change; a disagreeing sample does not |
| Program memory | Macro tie-offs, registered read of the word last written, `image_complete` exactly when all 256 bits are set, a write sets one bit, invalidate clears them |
| CRC | Reset state, configuration only on `configure`, load takes the seed, and one step of the recurrence in both directions |
| Queue | Reset state, the level never exceeds the depth and tracks accepted pushes and pops, a rejected push writes nothing, the read pointer advances only on an accepted pop, and the head is the oldest entry |
| Engine | Return stack stays within two, commits strobe only owned pins, the engine never pushes a full receive queue or pulls an empty transmit queue, a bubble has no side effects, errors are sticky, a taken branch costs two cycles, and an empty-stack return faults and halts |
| Host SPI | Two-flop synchronization, acceptance only at exactly 48 edges, rejection otherwise, MISO low while CS_n is high, response shifted on the following frame |
| Capture | Count bound, truncation, reason 6 never reported, invalid reads masked, stopped state stable |
| Integration | The two ownership masks are disjoint, a commit reaches only an owned pin, reset or disable releases every output enable, an accepted start frame can strobe both engines while a rejected start strobes neither, a program write is refused while that engine runs, and the filter mask is written only while both engines are stopped |

The integration proof black-boxes the engines and the pin cells. The only assumption it adds is
the pin cell's output-enable release, which the pin-cell proof establishes on its own. The
program-memory proof covers the register-file model used for inference; the same cocotb tests
also run against the PDK behavioral model.

Each block also has a cocotb differential or directed suite. The top-level suite loads both
programs over SPI, starts both engines and compares every `uio` transition with a model composed
of the same blocks. It also pushes bytes with `TXPUSH` and checks that the engine pulls and drives
them to match the model, and reads engine records back with `RXPOP`, including the empty-queue
rejection. Directed tests cover the filter mask (reset value, a write refused while an engine
runs, a raw pin passing a pulse the filter rejects), capture arm, stop and record readback, and
each `STATUS` address.

## Not in this tree yet

- The fresh Tiny Tapeout run and its evidence (source, netlist and GDS hashes).
- The FPGA prototype. The intended board is an Arty A7-35T: a BRAM with the same registered-read
  contract as the SRAM wrapper, an MMCM making 48 MHz from 100 MHz, and a UART-to-SPI bridge.
  It is a comparison aid. It does not verify pad delay or ASIC timing, and it does not gate the
  rest of the work.

## Open items

1. Whether the Tiny Tapeout timing constraints set I/O delay as a fraction of the clock or as a
   fixed value. That decides whether external paths close at 48 MHz, and the first timing run
   answers it.
2. Whether 4 columns per supply hold IR drop within budget at the macros. The power grid
   connects both macros on every supply, but IR drop is only reported after placement.
3. The PDK commit the precheck pins at hardening time.
4. CMOS5L pad characterization, which Tiny Tapeout has not published.

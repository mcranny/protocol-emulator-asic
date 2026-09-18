/*
 * Copyright (c) 2026 Matthew Cranny
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_mcranny_protocol_emulator (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
    input  wire [7:0] uio_in,   // IOs: Input path
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
    input  wire       ena,      // always 1 when the design is powered, so you can ignore it
    input  wire       clk,      // clock
    input  wire       rst_n     // reset_n - low to reset
);

  // Foundation behavior: pass dedicated inputs through only while the design
  // is selected and out of reset. Bidirectional pins remain released.
  assign uo_out  = (ena && rst_n) ? ui_in : 8'b0;
  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;

  // List all unused inputs to prevent warnings
  wire _unused = &{clk, uio_in, 1'b0};

endmodule

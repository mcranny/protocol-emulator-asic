// SPDX-License-Identifier: Apache-2.0
`default_nettype none
`timescale 1ns/1ps
// Test-only power-loss model: select a second, previously unclocked DUT.
// No initializer, force, deposit, or memory preload supplies DUT state.
module reset_tb;
    reg select_cold;
    reg warm_clk, warm_rst_n, warm_ena;
    reg cold_clk, cold_rst_n, cold_ena;
    reg [7:0] warm_ui_in, warm_uio_in, cold_ui_in, cold_uio_in;
    wire [7:0] warm_uo_out, warm_uio_out, warm_uio_oe;
    wire [7:0] cold_uo_out, cold_uio_out, cold_uio_oe;
    wire [7:0] selected_uio_oe = select_cold ? cold_uio_oe : warm_uio_oe;
    `DUT_TOP warm (.clk(warm_clk), .rst_n(warm_rst_n), .ena(warm_ena),
        .ui_in(warm_ui_in), .uio_in(warm_uio_in), .uo_out(warm_uo_out),
        .uio_out(warm_uio_out), .uio_oe(warm_uio_oe));
    `DUT_TOP cold (.clk(cold_clk), .rst_n(cold_rst_n), .ena(cold_ena),
        .ui_in(cold_ui_in), .uio_in(cold_uio_in), .uo_out(cold_uo_out),
        .uio_out(cold_uio_out), .uio_oe(cold_uio_oe));
endmodule

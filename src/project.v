// Copyright (c) 2026 Matthew Cranny
// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module tt_um_mcranny_protocol_emulator (
    input wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input wire ena, clk, rst_n
);
    (* async_reg = "true" *) reg [7:0] pins_meta, pins_sync;
    (* async_reg = "true" *) reg [1:0] reset_pipe;
    wire reset_n = reset_pipe[1];
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) reset_pipe <= 0;
        else reset_pipe <= {reset_pipe[0], 1'b1};
    end
    wire cmd_valid, frame_error, miso, running, error;
    wire [7:0] cmd, addr;
    wire [23:0] payload;
    wire [39:0] reply;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin pins_meta <= 0; pins_sync <= 0; end
        else begin pins_meta <= uio_in; pins_sync <= pins_meta; end
    end
    protocol_host_spi host (
        .clk(clk), .rst_n(reset_n), .ena(ena),
        .sck(ui_in[0]), .cs_n(ui_in[1]), .mosi(ui_in[2]), .miso(miso),
        .cmd_valid(cmd_valid), .frame_error(frame_error),
        .cmd(cmd), .addr(addr), .payload(payload), .reply(reply)
    );
    protocol_engine engine (
        .clk(clk), .rst_n(reset_n), .ena(ena), .pins_in(pins_sync),
        .cmd_valid(cmd_valid), .frame_error(frame_error),
        .cmd(cmd), .addr(addr), .payload(payload), .reply(reply),
        .pins_out(uio_out), .pins_oe(uio_oe), .running(running), .error(error)
    );
    assign uo_out = {5'b0, error, running, miso};
    wire _unused = &{ui_in[7:3], 1'b0};
endmodule

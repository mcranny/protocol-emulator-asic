// SPDX-License-Identifier: Apache-2.0
`default_nettype none
// Isolated macro qualification vehicle, not the V2 capture implementation.
// ui[6] shifts one byte into the write register; ui[7] writes that register.
// ui[5:0] addresses all 64 words. Otherwise the macro reads on each clock.
// uio_in[2:0] selects the read byte on uo_out. No bidirectional output drive.
module tt_um_mcranny_sram_probe (
    input wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input wire ena, clk, rst_n
);
    reg [63:0] write_data;
    wire [63:0] read_data;
    wire active = ena && rst_n;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) write_data <= 64'b0;
        else if (ena && ui_in[6]) write_data <= {write_data[55:0], uio_in};
    end
    RM_IHPSG13_1P_64x64_c2_bm_bist sram (
        .A_CLK(clk), .A_MEN(active), .A_WEN(active && ui_in[7]),
        .A_REN(active && !ui_in[7]), .A_ADDR(ui_in[5:0]),
        .A_DIN(write_data), .A_DOUT(read_data), .A_DLY(1'b1),
        .A_BM(64'hffffffffffffffff),
        .A_BIST_CLK(1'b0), .A_BIST_EN(1'b0), .A_BIST_MEN(1'b0),
        .A_BIST_WEN(1'b0), .A_BIST_REN(1'b0), .A_BIST_ADDR(6'b0),
        .A_BIST_DIN(64'b0), .A_BIST_BM(64'b0)
    );
    assign uo_out = read_data[8*uio_in[2:0] +: 8];
    assign uio_out = 8'b0;
    assign uio_oe = 8'b0;
endmodule
`default_nettype wire

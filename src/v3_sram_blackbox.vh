// Blackbox declaration of the IHP 256x32 single-port SRAM macro.
//
// Include this file at file scope, outside any module, when compiling with
// V3_SRAM_BLACKBOX. Synthesis then treats the macro as an opaque cell that the
// physical flow replaces with the hard macro. The port list matches
// RM_IHPSG13_1P_256x32_c2_bm_bist in the IHP SG13G2 SRAM library.

`ifndef V3_SRAM_BLACKBOX_VH
`define V3_SRAM_BLACKBOX_VH

(* blackbox *)
module RM_IHPSG13_1P_256x32_c2_bm_bist (
    input  wire        A_CLK,
    input  wire        A_MEN,
    input  wire        A_WEN,
    input  wire        A_REN,
    input  wire [7:0]  A_ADDR,
    input  wire [31:0] A_DIN,
    input  wire        A_DLY,
    output wire [31:0] A_DOUT,
    input  wire [31:0] A_BM,
    input  wire        A_BIST_CLK,
    input  wire        A_BIST_EN,
    input  wire        A_BIST_MEN,
    input  wire        A_BIST_WEN,
    input  wire        A_BIST_REN,
    input  wire [7:0]  A_BIST_ADDR,
    input  wire [31:0] A_BIST_DIN,
    input  wire [31:0] A_BIST_BM
);
endmodule

`endif

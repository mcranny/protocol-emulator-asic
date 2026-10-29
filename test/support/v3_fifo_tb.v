`default_nettype none

// Harness exposing both engine-queue configurations of v3_fifo at once: the
// TX byte queue (8-bit entries, up to four pushed at a time) and the RX record
// queue (16-bit {value, meta} entries, one at a time). The differential suite
// drives both against one model.Queues.
module v3_fifo_tb (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [2:0]  tx_push_count,
    input  wire [31:0] tx_push_data,
    output wire        tx_push_ok,
    input  wire        tx_pop,
    output wire [7:0]  tx_head,
    output wire [4:0]  tx_level,

    input  wire        rx_push_count,
    input  wire [15:0] rx_push_data,
    output wire        rx_push_ok,
    input  wire        rx_pop,
    output wire [15:0] rx_head,
    output wire [4:0]  rx_level
);

    v3_fifo #(.WIDTH(8), .DEPTH(16), .PUSH_MAX(4)) u_tx (
        .clk        (clk),
        .rst_n      (rst_n),
        .push_count (tx_push_count),
        .push_data  (tx_push_data),
        .push_ok    (tx_push_ok),
        .pop        (tx_pop),
        .head       (tx_head),
        .level      (tx_level)
    );

    v3_fifo #(.WIDTH(16), .DEPTH(16), .PUSH_MAX(1)) u_rx (
        .clk        (clk),
        .rst_n      (rst_n),
        .push_count (rx_push_count),
        .push_data  (rx_push_data),
        .push_ok    (rx_push_ok),
        .pop        (rx_pop),
        .head       (rx_head),
        .level      (rx_level)
    );

endmodule

`default_nettype wire

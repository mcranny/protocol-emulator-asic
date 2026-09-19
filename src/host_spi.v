// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module protocol_host_spi (
    input wire clk, rst_n, ena,
    input wire sck, cs_n, mosi,
    output wire miso,
    output reg cmd_valid, frame_error,
    output reg [7:0] cmd, addr,
    output reg [23:0] payload,
    input wire [39:0] reply
);
    (* async_reg = "true" *) reg [1:0] sck_sync, cs_sync, mosi_sync;
    reg sck_prev, cs_prev;
    reg [5:0] bit_count;
    reg [39:0] rx_shift, tx_shift, response;
    assign miso = rst_n && ena && !cs_n ? tx_shift[39] : 1'b0;

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            sck_sync <= 0; cs_sync <= 3; mosi_sync <= 0;
            sck_prev <= 0; cs_prev <= 1;
            bit_count <= 0;
            rx_shift <= 0; tx_shift <= 0; response <= 0;
            cmd_valid <= 0; frame_error <= 0;
            cmd <= 0; addr <= 0; payload <= 0;
        end else if (!ena) begin
            sck_sync <= 0; cs_sync <= 3; mosi_sync <= 0;
            sck_prev <= 0; cs_prev <= 1;
            bit_count <= 0;
            rx_shift <= 0; tx_shift <= 0; response <= 0;
            cmd_valid <= 0; frame_error <= 0;
            cmd <= 0; addr <= 0; payload <= 0;
        end else begin
            sck_sync <= {sck_sync[0], sck};
            cs_sync <= {cs_sync[0], cs_n};
            mosi_sync <= {mosi_sync[0], mosi};
            sck_prev <= sck_sync[1];
            cs_prev <= cs_sync[1];
            cmd_valid <= 0;
            frame_error <= 0;
            // The core's combinational response is the pre-execution snapshot.
            if (cmd_valid) response <= reply;
            if (cs_prev && !cs_sync[1]) begin
                tx_shift <= response;
                rx_shift <= 0;
                bit_count <= 0;
            end else if (!cs_prev && cs_sync[1]) begin
                if (bit_count == 40) begin
                    {cmd, addr, payload} <= rx_shift;
                    cmd_valid <= 1;
                end else begin
                    frame_error <= 1;
                    response <= 40'h8000000000;
                end
            end else if (!cs_sync[1]) begin
                if (!sck_prev && sck_sync[1]) begin
                    rx_shift <= {rx_shift[38:0], mosi_sync[1]};
                    if (bit_count < 41) bit_count <= bit_count + 1'b1;
                end
                if (sck_prev && !sck_sync[1]) tx_shift <= {tx_shift[38:0], 1'b0};
            end
        end
    end
endmodule

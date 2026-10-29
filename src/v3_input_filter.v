// SPDX-License-Identifier: Apache-2.0
`default_nettype none
// Three consecutive synchronized samples qualify a change. At 48 MHz a
// digital spike lasting at most 62.5 ns spans at most two sampling edges.
module v3_input_filter (
    input wire clk, rst_n,
    input wire [7:0] samples,
    output reg [7:0] filtered
);
    reg [7:0] previous, older;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin previous <= 0; older <= 0; filtered <= 0; end
        else begin
            previous <= samples;
            older <= previous;
            filtered <= (samples & previous & older) |
                        (filtered & (samples | previous | older));
        end
    end
`ifdef FORMAL
    reg [2:0] valid;
    initial valid = 0;
    always @(posedge clk) begin
        valid <= {valid[1:0],1'b1};
        if (!valid[0]) assume(!rst_n);
        if (&valid && rst_n && $past(rst_n) && $past(rst_n,2) && $past(rst_n,3)) begin
            assert((filtered & ($past(samples) & $past(samples,2) & $past(samples,3))) ==
                   ($past(samples) & $past(samples,2) & $past(samples,3)));
            assert((~filtered & (~$past(samples) & ~$past(samples,2) & ~$past(samples,3))) ==
                   (~$past(samples) & ~$past(samples,2) & ~$past(samples,3)));
            assert(((filtered ^ $past(filtered)) &
                    (($past(samples) ^ $past(samples,2)) | ($past(samples) ^ $past(samples,3)))) == 0);
        end
    end
`endif
endmodule

// SPDX-License-Identifier: Apache-2.0
`default_nettype none
// Capture candidate. Samples pre-execution state; never controls an engine.
// Combined physical acceptance is still required.
module protocol_capture_v2 #(
    parameter DEPTH = 32,
    parameter TIMESTAMP_WIDTH = 32
) (
    input wire clk, rst_n,
    input wire arm, stop, disable_capture,
    input wire [1:0] trigger_mode,
    input wire [7:0] trigger_mask,
    input wire [7:0] pins_in, pins_out, pins_oe, flags,
    input wire [4:0] read_address,
    output wire [63:0] read_data,
    output reg [5:0] count,
    output reg [31:0] timestamp, trigger_timestamp,
    output reg armed, triggered, truncated,
    output reg [2:0] reason
);
    localparam [31:0] MAX_TIMESTAMP = 32'hffffffff >> (32 - TIMESTAMP_WIDTH);
    localparam [5:0] CAPACITY = DEPTH[5:0];
    // reason: reset=0, armed=1, capturing=2, stopped=3, untriggered=4,
    // capacity=5, timestamp=6, disabled=7. Trigger annotation occupies flags[3].
    reg [63:0] records [0:DEPTH-1];
    reg [31:0] previous;
    reg [1:0] mode;
    reg [7:0] mask;
    reg clear_trigger;
    wire [7:0] sampled_flags = flags & 8'hf7;
    wire [31:0] sample = {pins_in,pins_out,pins_oe,sampled_flags};
    wire [1:0] selected_mode = arm ? trigger_mode : mode;
    wire [7:0] selected_mask = arm ? trigger_mask : mask;
    wire fire = (arm || !triggered) && (
        selected_mode == 0 ||
        (selected_mode == 1 && !arm && |((pins_in ^ previous[31:24]) & selected_mask)) ||
        (selected_mode == 2 && |(sampled_flags & selected_mask & 8'h03)) ||
        (selected_mode == 3 && sampled_flags[2]));
    wire [31:0] cycle = arm ? 0 : timestamp + 1'b1;
    wire take = arm ? fire : (triggered || fire) &&
        (fire || sample != previous || clear_trigger || |sampled_flags[1:0]);
    wire sampling = arm || (armed && !stop && !disable_capture);
    wire [5:0] next_count = arm ? 6'd1 : count + 1'b1;
    // Decode once for all record bits, keeping the full timestamp and sample.
    wire [DEPTH-1:0] read_select;
    genvar read_index;
    generate for (read_index = 0; read_index < DEPTH; read_index = read_index + 1) begin: record_decoder
        assign read_select[read_index] = read_address == read_index && {1'b0,read_address} < count;
    end endgenerate
    reg [63:0] selected_record;
    integer read_word;
    always @* begin
        selected_record = 0;
        for (read_word = 0; read_word < DEPTH; read_word = read_word + 1)
            selected_record = selected_record | (records[read_word] & {64{read_select[read_word]}});
    end
    assign read_data = selected_record;

    always @(posedge clk) begin
        if (rst_n && sampling && take)
            records[arm ? 5'b0 : count[4:0]] <= {cycle, sample[31:8],sampled_flags | {4'b0,fire,3'b0}};
    end
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            count<=0; timestamp<=0; trigger_timestamp<=0;
            armed<=0; triggered<=0; truncated<=0; reason<=0;
            previous<=0; mode<=0; mask<=0; clear_trigger<=0;
        end else if (arm) begin
            mode<=trigger_mode; mask<=trigger_mask; previous<=sample;
            timestamp<=0; trigger_timestamp<=0; triggered<=fire;
            count<=fire ? 6'd1 : 6'd0; clear_trigger<=fire;
            armed<=!(fire && CAPACITY == 1);
            truncated<=fire && CAPACITY == 1;
            reason<=fire ? (CAPACITY == 1 ? 3'd5 : 3'd2) : 3'd1;
        end else if (armed) begin
            if (disable_capture) begin armed<=0; reason<=7; end
            else if (stop) begin armed<=0; reason<=triggered ? 3'd3 : 3'd4; end
            else begin
                timestamp<=cycle; previous<=sample; clear_trigger<=fire;
                if (fire) begin triggered<=1; trigger_timestamp<=cycle; reason<=2; end
                if (take) count<=next_count;
                if (take && next_count == CAPACITY) begin
                    armed<=0; truncated<=1; reason<=5;
                end else if (cycle == MAX_TIMESTAMP) begin
                    armed<=0; truncated<=1; reason<=6;
                end
            end
        end
    end
`ifdef FORMAL
    reg past_valid;
    initial past_valid=0;
    always @(posedge clk) begin
        past_valid<=1;
        if (!past_valid) assume(!rst_n);
        if (past_valid && rst_n) begin
            assert(count <= CAPACITY);
            assert(timestamp <= MAX_TIMESTAMP);
            if (armed) begin assert(count < CAPACITY); assert(timestamp < MAX_TIMESTAMP); end
            if (truncated) assert(!armed);
            if ({1'b0,read_address} >= count) assert(read_data == 0);
            if ($past(rst_n && !armed) && !arm && !$past(arm)) begin
                assert($stable({count,timestamp,trigger_timestamp,armed,triggered,truncated,reason}));
            end
        end
    end
`endif
endmodule

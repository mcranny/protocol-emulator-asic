// SPDX-License-Identifier: Apache-2.0
`default_nettype none
// Candidate integration top. The released top/module and info.yaml remain V1
// until V2 capability and physical gates are satisfied.
module tt_um_mcranny_protocol_emulator_v2 (
    input wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input wire [7:0] uio_in,
    output wire [7:0] uio_out, uio_oe,
    input wire ena, clk, rst_n
);
    (* async_reg = "true" *) reg [1:0] reset_pipe;
    (* async_reg = "true" *) reg [7:0] pins_meta, pins_sync;
    reg [7:0] pins_previous;
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin reset_pipe <= 0; pins_meta <= 0; pins_sync <= 0; pins_previous <= 0; end
        else begin
            reset_pipe <= {reset_pipe[0],1'b1};
            pins_meta <= uio_in; pins_sync <= pins_meta; pins_previous <= pins_sync;
        end
    end
    wire reset_n = reset_pipe[1];
    wire cmd_valid, frame_error, miso;
    wire [7:0] cmd, addr;
    wire [23:0] payload;
    reg [39:0] reply;
    protocol_host_spi host (
        .clk(clk), .rst_n(reset_n), .ena(ena), .sck(ui_in[0]), .cs_n(ui_in[1]),
        .mosi(ui_in[2]), .miso(miso), .cmd_valid(cmd_valid), .frame_error(frame_error),
        .cmd(cmd), .addr(addr), .payload(payload), .reply(reply)
    );
    reg [7:0] owner0, owner1, od0, od1, abort0, abort1;
    reg [23:0] watch00, watch01, watch10, watch11;
    wire [7:0] change = pins_sync ^ pins_previous;
    wire [1:0] event0 = {((pins_sync & watch01[7:0]) == watch01[15:8]) && |(change & watch01[23:16]),
                        ((pins_sync & watch00[7:0]) == watch00[15:8]) && |(change & watch00[23:16])};
    wire [1:0] event1 = {((pins_sync & watch11[7:0]) == watch11[15:8]) && |(change & watch11[23:16]),
                        ((pins_sync & watch10[7:0]) == watch10[15:8]) && |(change & watch10[23:16])};
    wire [1:0] running;
    wire [15:0] errors0, errors1;
    wire [7:0] out0, out1, oe0, oe1, mark0, mark1;
    wire [23:0] read0, read1;
    wire [15:0] rx0, rx1, rxnext0, rxnext1;
    wire [4:0] txlevel0, txlevel1, rxlevel0, rxlevel1;
    wire ready0, ready1;
    wire valid0, valid1, legal0, legal1, start_ready0, start_ready1;
    wire select_engine = addr[7];
    wire [4:0] txlevel = select_engine ? txlevel1 : txlevel0;
    wire [4:0] rxlevel = select_engine ? rxlevel1 : rxlevel0;
    wire selected_running = select_engine ? running[1] : running[0];
    wire [15:0] selected_errors = select_engine ? errors1 : errors0;
    wire [1:0] tx_count = cmd == 8'h12 ? 2'd3 : cmd == 8'h11 ? 2'd2 : 2'd1;
    wire tx_command = cmd == 7 || (cmd >= 8'h10 && cmd <= 8'h12);
    reg pending_rx, pending_engine;
    // A queued RX response is consumed only after a complete subsequent frame
    // has actually shifted it to the host. A short frame preserves it.
    wire consume_rx = cmd_valid && pending_rx;
    wire consume_selected = consume_rx && pending_engine == select_engine;
    wire [4:0] available_rx = rxlevel - {4'b0,consume_selected};
    wire [15:0] selected_rx = select_engine ? (consume_selected ? rxnext1 : rx1) :
                                                            (consume_selected ? rxnext0 : rx0);
    reg reject, fatal;
    reg [23:0] result;
    reg [15:0] host_errors;
    wire [63:0] capture_data;
    wire [31:0] capture_time, capture_trigger_time;
    wire [5:0] capture_count;
    wire capture_armed, capture_triggered, capture_truncated;
    wire [2:0] capture_reason;
    always @* begin
        reject = 0; fatal = 0; result = 0;
        case (cmd)
            0: if (addr != 0 || payload != 0) begin reject=1; fatal=1; end
            1,2: begin
                if (addr[6]) begin reject=1; fatal=1; end
                else if (cmd == 1) begin
                    reject=selected_running; result=payload;
                    if (!(select_engine ? legal1 : legal0)) begin reject=1; fatal=1; end
                end
                else begin
                    if (payload != 0) begin reject=1; fatal=1; end
                    if (!(select_engine ? valid1 : valid0)) begin reject=1; fatal=1; end
                    result = select_engine ? read1 : read0;
                end
            end
            3,4,5,6,8: begin
                if (addr[6:0] != 0 || payload != 0) begin reject=1; fatal=1; end
                if (cmd == 3 && !(select_engine ? start_ready1 : start_ready0)) reject=1;
                if (cmd == 6) result = {7'b0,(selected_errors | host_errors),selected_running};
                if (cmd == 8) result = {14'b0,rxlevel,txlevel};
            end
            7,8'h10,8'h11,8'h12: begin
                if (addr[6:0] != 0 || (tx_count == 1 && payload[23:8] != 0) ||
                    (tx_count == 2 && payload[23:16] != 0)) begin reject=1; fatal=1; end
                if (!(select_engine ? ready1 : ready0)) reject=1;
                result = {17'b0,tx_count,txlevel};
            end
            9: begin
                if (addr != 0 || payload != 0) begin reject=1; fatal=1; end
                result = 24'h020240;
            end
            10: begin
                if (addr != 0 || payload != 0) begin reject=1; fatal=1; end
                result = 24'h021010; // two engines, 16 TX and 16 RX entries
            end
            11: begin
                if (addr != 0 || payload != 0) begin reject=1; fatal=1; end
                result = 24'h20001f; // 32 capture records and feature flags 0..4
            end
            8'h13: begin
                if (addr[6:0] != 0 || payload != 0) begin reject=1; fatal=1; end
                if (available_rx == 0) reject=1;
                result = {3'b0,available_rx,selected_rx};
            end
            8'h20: begin
                if (addr != 0 || payload == 0 || payload[23:2] != 0) begin reject=1; fatal=1; end
                if (|(running & payload[1:0])) reject=1;
                if ((payload[0] && !start_ready0) || (payload[1] && !start_ready1)) reject=1;
            end
            8'h21: begin
                if (addr[6:0] != 0 || payload[23:16] != 0) begin reject=1; fatal=1; end
                if (|running || |(payload[7:0] & (select_engine ? owner0 : owner1)) ||
                    |(payload[15:8] & ~payload[7:0])) reject=1;
            end
            8'h22,8'h23: begin
                if (addr[6:0] != 0 || |(payload[15:8] & ~payload[7:0])) begin reject=1; fatal=1; end
                if (selected_running) reject=1;
            end
            8'h24: begin
                if (addr[6:0] != 0 || payload[23:2] != 0) begin reject=1; fatal=1; end
                if (selected_running) reject=1;
            end
            8'h30: begin
                if (addr != 0 || |(payload & 24'hff00fc)) begin reject=1; fatal=1; end
            end
            8'h31,8'h32: begin
                if (addr != 0 || payload != 0) begin reject=1; fatal=1; end
                if (cmd == 8'h32)
                    result = {12'b0,capture_count,capture_reason,capture_truncated,capture_triggered,capture_armed};
            end
            8'h33: begin
                if (addr > 3 || payload != 0) begin reject=1; fatal=1; end
                if (capture_armed) reject=1;
                case (addr[1:0])
                    0: result = capture_time[23:0];
                    1: result = {16'b0,capture_time[31:24]};
                    2: result = capture_trigger_time[23:0];
                    3: result = {16'b0,capture_trigger_time[31:24]};
                endcase
            end
            8'h35,8'h36,8'h37: begin
                if (addr > 31 || payload != 0) begin reject=1; fatal=1; end
                if (capture_armed) reject=1;
                case (cmd)
                    8'h35: result = capture_data[23:0];
                    8'h36: result = capture_data[47:24];
                    default: result = {8'b0,capture_data[63:48]};
                endcase
            end
            default: begin reject=1; fatal=1; end
        endcase
        if (reject) result = 0;
        reply = {cmd | {reject,7'b0},addr,result};
    end
    wire accepted = cmd_valid && !reject;
    wire shared_fault = frame_error || (cmd_valid && fatal);
    wire start0 = accepted && ((cmd == 3 && !select_engine) || (cmd == 8'h20 && payload[0]));
    wire start1 = accepted && ((cmd == 3 && select_engine) || (cmd == 8'h20 && payload[1]));
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            owner0<=0; owner1<=0; od0<=0; od1<=0; abort0<=0; abort1<=0;
            watch00<=0; watch01<=0; watch10<=0; watch11<=0;
            pending_rx<=0; pending_engine<=0; host_errors<=0;
        end else if (!ena) pending_rx<=0;
        else begin
            if (shared_fault) host_errors <= host_errors | 16'h0100;
            if (frame_error) pending_rx <= 0;
            else if (cmd_valid) pending_rx <= accepted && cmd == 8'h13;
            if (accepted) begin
                if (cmd == 8'h13) pending_engine <= select_engine;
                if (cmd == 5) host_errors <= 0;
                if (cmd == 8'h21) begin
                    if (select_engine) begin owner1<=payload[7:0]; od1<=payload[15:8]; end
                    else begin owner0<=payload[7:0]; od0<=payload[15:8]; end
                end
                if (cmd == 8'h22) begin
                    if (select_engine) watch10<=payload; else watch00<=payload;
                end
                if (cmd == 8'h23) begin
                    if (select_engine) watch11<=payload; else watch01<=payload;
                end
                if (cmd == 8'h24) begin
                    if (select_engine) abort1<=payload[7:0]; else abort0<=payload[7:0];
                end
            end
        end
    end
    protocol_engine_v2 e0 (
        .clk(clk), .rst_n(reset_n), .ena(ena), .pins_in(pins_sync), .owner(owner0), .open_drain(od0),
        .event_set({6'b0,event0}), .abort_mask(abort0), .start(start0),
        .stop(accepted && cmd == 4 && !select_engine), .state_reset(accepted && cmd == 5 && !select_engine),
        .external_fault(shared_fault), .program_write(accepted && cmd == 1 && !select_engine),
        .program_addr(addr[5:0]), .program_data(payload), .program_read(read0),
        .program_valid(valid0), .program_legal(legal0), .start_ready(start_ready0),
        .tx_valid(accepted && tx_command && !select_engine), .tx_count(tx_count), .tx_data(payload),
        .tx_ready(ready0), .rx_pop(consume_rx && !pending_engine), .rx_data(rx0), .rx_next(rxnext0),
        .tx_level(txlevel0), .rx_level(rxlevel0), .pins_out(out0), .pins_oe(oe0),
        .marker(mark0), .running(running[0]), .errors(errors0)
    );
    protocol_engine_v2 e1 (
        .clk(clk), .rst_n(reset_n), .ena(ena), .pins_in(pins_sync), .owner(owner1), .open_drain(od1),
        .event_set({6'b0,event1}), .abort_mask(abort1), .start(start1),
        .stop(accepted && cmd == 4 && select_engine), .state_reset(accepted && cmd == 5 && select_engine),
        .external_fault(shared_fault), .program_write(accepted && cmd == 1 && select_engine),
        .program_addr(addr[5:0]), .program_data(payload), .program_read(read1),
        .program_valid(valid1), .program_legal(legal1), .start_ready(start_ready1),
        .tx_valid(accepted && tx_command && select_engine), .tx_count(tx_count), .tx_data(payload),
        .tx_ready(ready1), .rx_pop(consume_rx && pending_engine), .rx_data(rx1), .rx_next(rxnext1),
        .tx_level(txlevel1), .rx_level(rxlevel1), .pins_out(out1), .pins_oe(oe1),
        .marker(mark1), .running(running[1]), .errors(errors1)
    );
    assign uio_out = (out0 & oe0) | (out1 & oe1);
    assign uio_oe = (oe0 | oe1) & {8{rst_n && ena}};
    assign uo_out = {4'b0, |(errors0 | errors1 | host_errors), running, miso};
    protocol_capture_v2 capture (
        .clk(clk), .rst_n(reset_n), .arm(ena && accepted && cmd == 8'h30),
        .stop(accepted && cmd == 8'h31), .disable_capture(!ena),
        .trigger_mode(payload[1:0]), .trigger_mask(payload[15:8]),
        .pins_in(pins_sync), .pins_out(uio_out), .pins_oe(uio_oe),
        .flags({2'b0,running,1'b0,|(errors0 | errors1 | host_errors),|mark1,|mark0}),
        .read_address(addr[4:0]), .read_data(capture_data), .count(capture_count),
        .timestamp(capture_time), .trigger_timestamp(capture_trigger_time),
        .armed(capture_armed), .triggered(capture_triggered),
        .truncated(capture_truncated), .reason(capture_reason)
    );
    wire _unused = &{ui_in[7:3],1'b0};
endmodule

// SPDX-License-Identifier: Apache-2.0
`default_nettype none
// V2 candidate core. Integration/signoff remains separate from the V1 top.
module protocol_engine_v2 (
    input wire clk, rst_n, ena,
    input wire [7:0] pins_in, owner, open_drain,
    input wire [7:0] event_set, abort_mask,
    input wire start, stop, state_reset, external_fault,
    input wire program_write,
    input wire [5:0] program_addr,
    input wire [23:0] program_data,
    output wire [23:0] program_read,
    output wire program_valid, program_legal, start_ready,
    input wire tx_valid,
    input wire [1:0] tx_count,
    input wire [23:0] tx_data,
    output wire tx_ready,
    input wire rx_pop,
    output wire [15:0] rx_data,
    output wire [15:0] rx_next,
    output reg [4:0] tx_level, rx_level,
    output wire [7:0] pins_out, pins_oe,
    output reg [7:0] marker,
    output reg running,
    output reg [15:0] errors
);
    localparam [15:0] E_ILLEGAL=16'h0010, E_ADDRESS=16'h0020,
        E_BUSY=16'h0040, E_FRAME=16'h0100, E_OVERFLOW=16'h0200,
        E_TIMEOUT=16'h0400, E_OWNER=16'h0800, E_FIRMWARE=16'h1000;
    reg [23:0] program_mem [0:63];
    reg [63:0] valid_words;
    reg [7:0] tx_mem [0:15];
    reg [15:0] rx_mem [0:15];
    reg [3:0] tx_rd, tx_wr, rx_rd, rx_wr;
    reg [5:0] pc;
    reg [15:0] r0, r1, osr, delay_left, wait_left;
    reg [7:0] isr, out_value, out_enable, events, firmware_error;
    reg timed_out, interrupted, pull_empty;
    reg [1:0] link_depth;
    reg [5:0] link0, link1;
`ifdef FORMAL
    // Overapproximate every fetched instruction independently. FIFO/drive
    // safety must hold even for arbitrary instruction sequences; program
    // storage correctness is covered by the separate readback/differential tests.
    (* anyseq *) wire [23:0] word;
`else
    wire [23:0] word = program_mem[pc];
`endif
    wire [3:0] op = word[23:20], sub = word[19:16];
    wire [15:0] selected = word[16] ? r1 : r0;
    wire [7:0] active_events = events | event_set;
    assign program_read = program_mem[program_addr];
    assign program_valid = valid_words[program_addr];
    assign program_legal = legal(program_data);
    assign start_ready = !running && valid_words[0];
    assign tx_ready = tx_count != 0 && ({1'b0,tx_level} + {4'b0,tx_count}) <= 16;
    assign rx_data = rx_level != 0 ? rx_mem[rx_rd] : 16'b0;
    assign rx_next = rx_level > 1 ? rx_mem[rx_rd + 4'd1] : 16'b0;
    assign pins_out = out_value & owner & ~open_drain;
    assign pins_oe = out_enable & owner & ~(out_value & open_drain)
        & {8{rst_n && ena && running}};

    function legal;
        input [23:0] instruction;
        reg [19:0] arg;
        begin
            arg = instruction[19:0];
            legal = 0;
            case (instruction[23:20])
                0,12: legal = arg == 0;
                1,2: legal = instruction[19:16] == 0;
                3: legal = instruction[19:17] == 0;
                4,5: legal = (arg & 20'heffff) == 0;
                6: legal = instruction[19:4] == 0;
                7: legal = (arg & 20'heff00) == 0;
                8: legal = instruction[19:16] == 0 && instruction[15:0] != 0;
                9: legal = instruction[19:4] != 0;
                10: legal = (arg & 20'heff80) == 0;
                11: legal = (arg & 20'hfff80) == 0;
                13: case (instruction[19:16])
                    0,7: legal = instruction[15:4] == 0;
                    1,8,9,13,14: legal = instruction[15:8] == 0;
                    2: legal = instruction[15:0] == 0;
                    3: legal = instruction[15:11] == 0;
                    4,6: legal = 1;
                    5: legal = instruction[15:11] == 0 && instruction[10:7] <= 6;
                    10,12: legal = instruction[15:1] == 0;
                    11: legal = instruction[15:3] == 0;
                    15: legal = instruction[15:10] == 0;
                    default: legal = 0;
                endcase
                14: case (instruction[19:16])
                    0: legal = instruction[15:7] == 0;
                    1,2: legal = instruction[15:0] == 0;
                    3: legal = instruction[15:11] == 0;
                    default: legal = 0;
                endcase
                default: legal = 0;
            endcase
        end
    endfunction

    wire execute = running && ena && !start && !stop && !state_reset && !external_fault;
    wire fetch_ok = valid_words[pc] && legal(word);
    wire tx_push = tx_valid && tx_ready && ena && !state_reset;
    wire tx_take = execute && fetch_ok && tx_level != 0 &&
        (op == 4 || (op == 13 && sub == 12));
    wire rx_take = rx_pop && rx_level != 0 && ena && !state_reset;
    wire rx_push = execute && fetch_ok && op == 13 && sub == 1 && rx_level < 16;
    wire write_ok = program_write && !running && legal(program_data) && ena;

    reg condition;
    always @* begin
        case (word[10:7])
            0: condition = tx_level == 0;
            1: condition = rx_level == 16;
            2: condition = timed_out;
            3: condition = interrupted;
            4: condition = active_events[0];
            5: condition = active_events[1];
            6: condition = pull_empty;
            default: condition = 0;
        endcase
    end

    wire v1_wait = op == 9;
    wire wait_branch = op == 14 && sub == 3;
    wire event_wait = op == 13 && sub == 9;
    wire [2:0] wait_pin = wait_branch ? word[9:7] : word[2:0];
    wire wait_level = wait_branch ? word[10] : word[3];
    wire wait_match = event_wait ? |(active_events & word[7:0]) : pins_in[wait_pin] == wait_level;
    wire wait_abort = !v1_wait && !event_wait && |(active_events & abort_mask);
    wire [15:0] wait_initial = v1_wait ? word[19:4] : r1;
    wire [15:0] wait_remaining = wait_left != 0 ? wait_left : wait_initial;

    // Single-cycle retirement/fault decision, independent from state updates.
    reg retire, branch;
    reg [6:0] target;
    reg [15:0] fault;
    always @* begin
        retire = execute && fetch_ok;
        branch = 0;
        target = word[6:0];
        fault = 0;
        if (execute) begin
            if (!fetch_ok) fault = E_ILLEGAL;
            else case (op)
                2: if (|(word[7:0] & word[15:8] & ~owner)) fault = E_OWNER;
                4: retire = tx_level != 0;
                8: retire = delay_left == 1 || (delay_left == 0 && word[15:0] == 1);
                9: begin
                    retire = wait_match;
                    if (!wait_match && wait_remaining == 1) fault = E_TIMEOUT;
                end
                10: branch = selected != 1;
                11: branch = 1;
                12: retire = 0;
                13: case (sub)
                    1: if (rx_level == 16) fault = E_OVERFLOW;
                    3: branch = pins_in[word[9:7]] == word[10];
                    4: branch = (word[15] ? r1 : r0) == {8'b0, word[14:7]};
                    5: branch = condition;
                    6: if (|(word[15:8] & ~owner)) fault = E_OWNER;
                    7,9: begin
                        retire = wait_match || wait_abort || wait_remaining == 1;
                        if (wait_remaining == 0) fault = E_ILLEGAL;
                    end
                    14: begin fault = E_FIRMWARE; retire = 0; end
                    15: branch = pins_in[word[9:7]] != out_value[word[9:7]];
                    default: begin end
                endcase
                14: case (sub)
                    0: begin
                        branch = 1;
                        if (link_depth == 2 || pc == 63) fault = E_ILLEGAL;
                    end
                    1: begin
                        branch = 1;
                        target = {1'b0, link_depth == 2 ? link1 : link0};
                        if (link_depth == 0) fault = E_ILLEGAL;
                    end
                    3: begin
                        retire = wait_match || wait_abort || wait_remaining == 1;
                        branch = wait_abort || (!wait_match && wait_remaining == 1);
                        if (wait_remaining == 0) fault = E_ILLEGAL;
                    end
                    default: begin end
                endcase
                default: begin end
            endcase
            if (fault == 0 && retire && ((branch && target[6]) || (!branch && pc == 63)))
                fault = E_ADDRESS;
        end
    end

    always @(posedge clk) begin
        if (rst_n && write_ok) program_mem[program_addr] <= program_data;
        if (rst_n && tx_push) begin
            tx_mem[tx_wr] <= tx_data[7:0];
            if (tx_count >= 2) tx_mem[tx_wr + 4'd1] <= tx_data[15:8];
            if (tx_count == 3) tx_mem[tx_wr + 4'd2] <= tx_data[23:16];
        end
        if (rst_n && rx_push) rx_mem[rx_wr] <= {word[7:0], isr};
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            valid_words <= 0;
            running <= 0; errors <= 0;
            pc <= 0; r0 <= 0; r1 <= 0; osr <= 0; isr <= 0;
            out_value <= 0; out_enable <= 0; delay_left <= 0; wait_left <= 0;
            tx_level <= 0; rx_level <= 0; tx_rd <= 0; tx_wr <= 0; rx_rd <= 0; rx_wr <= 0;
            marker <= 0; events <= 0; firmware_error <= 0;
            timed_out <= 0; interrupted <= 0; pull_empty <= 0;
            link_depth <= 0; link0 <= 0; link1 <= 0;
        end else if (!ena || state_reset) begin
            running <= 0;
            pc <= 0; r0 <= 0; r1 <= 0; osr <= 0; isr <= 0;
            out_value <= 0; out_enable <= 0; delay_left <= 0; wait_left <= 0;
            tx_level <= 0; rx_level <= 0; tx_rd <= 0; tx_wr <= 0; rx_rd <= 0; rx_wr <= 0;
            marker <= 0; events <= 0; firmware_error <= 0;
            timed_out <= 0; interrupted <= 0; pull_empty <= 0;
            link_depth <= 0; link0 <= 0; link1 <= 0;
            if (ena && state_reset) errors <= 0;
        end else begin
            marker <= 0;
            events <= active_events;
            errors <= errors | fault;
            if (write_ok) valid_words[program_addr] <= 1;
            if (program_write) begin
                if (running) errors <= errors | fault | E_BUSY;
                else if (!legal(program_data)) errors <= errors | fault | E_ILLEGAL;
            end
            if (tx_push) tx_wr <= tx_wr + {2'b0,tx_count};
            if (tx_take) tx_rd <= tx_rd + 1'b1;
            case ({tx_push,tx_take})
                2'b10: tx_level <= tx_level + {3'b0,tx_count};
                2'b01: tx_level <= tx_level - 1'b1;
                2'b11: tx_level <= tx_level + {3'b0,tx_count} - 1'b1;
                default: begin end
            endcase
            if (rx_push) rx_wr <= rx_wr + 1'b1;
            if (rx_take) rx_rd <= rx_rd + 1'b1;
            case ({rx_push,rx_take})
                2'b10: rx_level <= rx_level + 1'b1;
                2'b01: rx_level <= rx_level - 1'b1;
                default: begin end
            endcase
            if (external_fault) begin
                errors <= errors | E_FRAME;
                running <= 0;
            end else if (stop) running <= 0;
            else if (start) begin
                if (running) errors <= errors | E_BUSY;
                else if (!valid_words[0]) errors <= errors | E_ILLEGAL;
                else begin
                    running <= 1; pc <= 0; r0 <= 0; r1 <= 0; osr <= 0; isr <= 0;
                    out_value <= 0; out_enable <= 0; delay_left <= 0; wait_left <= 0;
                    marker <= 0; events <= 0; firmware_error <= 0;
                    timed_out <= 0; interrupted <= 0; pull_empty <= 0;
                    link_depth <= 0; link0 <= 0; link1 <= 0;
                end
            end else if (execute) begin
                if (fault != 0) running <= 0;
                if (retire && fault == 0) pc <= branch ? target[5:0] : pc + 1'b1;
                if (fetch_ok) begin
                    case (op)
                        1: out_value <= (out_value & ~word[15:8]) | (word[7:0] & word[15:8]);
                        2: if (fault == 0) out_enable <= (out_enable & ~word[15:8]) | (word[7:0] & word[15:8]);
                        3: if (word[16]) r1 <= word[15:0]; else r0 <= word[15:0];
                        4: if (tx_take) begin
                            if (word[16]) r1 <= {8'b0,tx_mem[tx_rd]}; else r0 <= {8'b0,tx_mem[tx_rd]};
                        end
                        5: osr <= selected;
                        6: begin
                            out_value[word[2:0]] <= word[3] ? osr[15] : osr[0];
                            osr <= word[3] ? {osr[14:0],1'b0} : {1'b0,osr[15:1]};
                        end
                        7: if (word[16]) r1 <= {8'b0,pins_in & word[7:0]}; else r0 <= {8'b0,pins_in & word[7:0]};
                        8: if (retire) delay_left <= 0;
                           else delay_left <= delay_left == 0 ? word[15:0] - 1'b1 : delay_left - 1'b1;
                        9: if (wait_match || wait_remaining == 1) wait_left <= 0;
                           else wait_left <= wait_remaining - 1'b1;
                        10: if (word[16]) r1 <= r1 - 1'b1; else r0 <= r0 - 1'b1;
                        12: running <= 0;
                        13: case (sub)
                            0: isr <= word[3] ? {isr[6:0],pins_in[word[2:0]]} : {pins_in[word[2:0]],isr[7:1]};
                            2: isr <= 0;
                            6: if (fault == 0) begin out_value <= word[7:0]; out_enable <= word[15:8]; end
                            7,9: begin
                                if (retire) begin
                                    wait_left <= 0;
                                    timed_out <= !wait_match && !wait_abort;
                                    interrupted <= wait_abort;
                                end else if (fault == 0) wait_left <= wait_remaining - 1'b1;
                            end
                            8: marker <= word[7:0];
                            10: if (word[0]) r1 <= {8'b0,isr}; else r0 <= {8'b0,isr};
                            11: begin out_value[word[2:0]] <= osr[7]; osr <= {8'b0,osr[6:0],1'b0}; end
                            12: begin
                                pull_empty <= tx_level == 0;
                                if (tx_take) begin
                                    if (word[0]) r1 <= {8'b0,tx_mem[tx_rd]}; else r0 <= {8'b0,tx_mem[tx_rd]};
                                end
                            end
                            13: events <= active_events & ~word[7:0];
                            14: firmware_error <= firmware_error | word[7:0];
                            default: begin end
                        endcase
                        14: case (sub)
                            0: if (fault == 0) begin
                                if (link_depth == 0) link0 <= pc + 1'b1; else link1 <= pc + 1'b1;
                                link_depth <= link_depth + 1'b1;
                            end
                            1: if (fault == 0) link_depth <= link_depth - 1'b1;
                            2: link_depth <= 0;
                            3: begin
                                if (retire) begin
                                    wait_left <= 0;
                                    timed_out <= !wait_match && !wait_abort;
                                    interrupted <= wait_abort;
                                end else if (fault == 0) wait_left <= wait_remaining - 1'b1;
                            end
                            default: begin end
                        endcase
                        default: begin end
                    endcase
                end
            end
        end
    end
`ifdef FORMAL
    reg past_valid;
    // Follow an arbitrary accepted byte through each queue. Ghost distance is
    // transaction order, not the implementation's circular read/write pointer.
    (* anyseq *) reg track_tx, track_rx;
    (* anyconst *) reg [1:0] track_byte;
    reg tx_tracked, rx_tracked;
    reg [4:0] tx_distance, rx_distance;
    reg [7:0] tx_expected;
    reg [15:0] rx_expected;
    wire [3:0] tx_tracked_addr = tx_rd + tx_distance[3:0];
    wire [3:0] rx_tracked_addr = rx_rd + rx_distance[3:0];
    wire [3:0] tx_pointer_distance = tx_wr - tx_rd;
    wire [3:0] rx_pointer_distance = rx_wr - rx_rd;
    initial past_valid = 0;
    always @(posedge clk) begin
        past_valid <= 1;
        if (!past_valid) assume(!rst_n);
        if (!rst_n || !ena || state_reset) begin
            tx_tracked <= 0; rx_tracked <= 0;
            tx_distance <= 0; rx_distance <= 0;
            tx_expected <= 0; rx_expected <= 0;
        end else begin
            if (tx_tracked && tx_take) begin
                if (tx_distance == 0) begin
                    assert(tx_mem[tx_rd] == tx_expected);
                    tx_tracked <= 0;
                end else tx_distance <= tx_distance - 1'b1;
            end
            if (!tx_tracked && tx_push && track_tx && track_byte < tx_count) begin
                tx_tracked <= 1;
                tx_distance <= tx_level + {3'b0,track_byte} - {4'b0,tx_take};
                case (track_byte)
                    0: tx_expected <= tx_data[7:0];
                    1: tx_expected <= tx_data[15:8];
                    default: tx_expected <= tx_data[23:16];
                endcase
            end
            if (rx_tracked && rx_take) begin
                if (rx_distance == 0) begin
                    assert(rx_mem[rx_rd] == rx_expected);
                    rx_tracked <= 0;
                end else rx_distance <= rx_distance - 1'b1;
            end
            if (!rx_tracked && rx_push && track_rx) begin
                rx_tracked <= 1;
                rx_distance <= rx_level - {4'b0,rx_take};
                rx_expected <= {word[7:0],isr};
            end
        end
        if (past_valid && rst_n) begin
            assert(tx_level <= 16);
            assert(rx_level <= 16);
            assert(link_depth <= 2);
            assert((pins_oe & ~owner) == 0);
            assert((pins_out & pins_oe & open_drain) == 0);
            if (!running || !ena) assert(pins_oe == 0);
            assert(tx_pointer_distance == tx_level[3:0]);
            assert(rx_pointer_distance == rx_level[3:0]);
            if (tx_tracked) begin
                assert(tx_distance < tx_level);
                assert(tx_mem[tx_tracked_addr] == tx_expected);
            end
            if (rx_tracked) begin
                assert(rx_distance < rx_level);
                assert(rx_mem[rx_tracked_addr] == rx_expected);
            end
        end
    end
`endif
endmodule
